"""hl autoresearch — autonomous strategy research loop.

Karpathy-style propose → commit → eval → keep/discard loop over a strategy
project (see `hl strategy new`). Two proposers ship:

  --agent demo  parametric: perturbs module-level numeric constants in
                strategy.py by a random factor in [0.8, 1.25].
  --agent llm   Anthropic Claude rewrites strategy.py end-to-end, one parameter
                per experiment. Resolves ANTHROPIC_API_KEY from
                env → ~/.nunchi/anthropic_api_key → interactive prompt.

Commands:
  hl autoresearch run <name> [--iterations N]    run the loop
  hl autoresearch results <name>                 show last N rows of results.tsv
  hl autoresearch status <name>                  best score / commit / #experiments
  hl autoresearch tail <run_id> [--follow]       tail the run's JSONL event log

Ported from nunchi-cli (which ported it from house/skills/autoresearch).
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

autoresearch_app = typer.Typer(
    name="autoresearch",
    help="Autonomous strategy research loop (Karpathy autoresearch pattern).",
    no_args_is_help=True,
    add_completion=False,
)


def _strategies_root() -> Path:
    override = os.environ.get("NUNCHI_STRATEGIES_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".nunchi" / "strategies"


def runs_root() -> Path:
    return Path.home() / ".nunchi" / "autoresearch_runs"


def _python_runner(strategy_dir: Path) -> list[str]:
    if shutil.which("uv") and (strategy_dir / "pyproject.toml").exists():
        return ["uv", "run", "python"]
    return [sys.executable]


def _git(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + cmd, cwd=cwd, check=True, text=True, capture_output=True)


def _run_backtest(strategy_dir: Path, timeout: int = 240) -> dict:
    """Run backtest.py in the strategy dir; parse `key: value` lines for metrics."""
    log_path = strategy_dir / "run.log"
    runner = _python_runner(strategy_dir)
    try:
        proc = subprocess.run(
            runner + ["backtest.py"],
            cwd=strategy_dir, timeout=timeout, text=True, capture_output=True,
        )
        log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    except subprocess.TimeoutExpired:
        log_path.write_text("TIMEOUT")
        return {"score": None, "sharpe": None, "max_dd": None, "returncode": -1, "error": "timeout"}

    metrics: dict = {"score": None, "sharpe": None, "max_dd": None, "returncode": proc.returncode}
    for line in proc.stdout.splitlines():
        for key in ("score", "sharpe", "max_drawdown_pct"):
            m = re.match(rf"^{key}:\s+(-?[\d.]+)", line.strip())
            if m:
                target = "max_dd" if key == "max_drawdown_pct" else key
                try:
                    metrics[target] = float(m.group(1))
                except ValueError:
                    pass
    if proc.returncode != 0:
        metrics["error"] = proc.stderr.splitlines()[-1] if proc.stderr else "non-zero exit"
    return metrics


# Match module-level constants like LOOKBACK = 24 or STOP_LOSS_PCT = 0.03
_PARAM_PATTERN = re.compile(r"^(?P<name>[A-Z_][A-Z0-9_]*)\s*=\s*(?P<val>-?\d+(?:\.\d+)?)\s*$", re.M)

# Default model for --agent llm. Override with ANTHROPIC_MODEL or --model.
_DEFAULT_LLM_MODEL = "claude-sonnet-4-6"

_ANTHROPIC_KEYSTORE = Path.home() / ".nunchi" / "anthropic_api_key"


def _resolve_anthropic_key(*, interactive: bool = True) -> str:
    """Resolve the Anthropic API key.

    Order: env ANTHROPIC_API_KEY → ~/.nunchi/anthropic_api_key → interactive prompt.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    if _ANTHROPIC_KEYSTORE.exists():
        try:
            stored = _ANTHROPIC_KEYSTORE.read_text().strip()
        except OSError:
            stored = ""
        if stored:
            os.environ["ANTHROPIC_API_KEY"] = stored
            return stored
    if not interactive or not sys.stdin.isatty():
        typer.echo(
            "error: --agent llm needs an Anthropic API key.\n"
            "       Set ANTHROPIC_API_KEY in env, write it to "
            f"{_ANTHROPIC_KEYSTORE}, or run interactively to be prompted.",
            err=True,
        )
        raise typer.Exit(code=2)
    typer.echo("--agent llm needs an Anthropic API key.")
    typer.echo("  Create one at: https://console.anthropic.com/settings/keys")
    entered = typer.prompt("ANTHROPIC_API_KEY", hide_input=True).strip()
    if not entered:
        typer.echo("error: empty API key", err=True)
        raise typer.Exit(code=2)
    if not entered.startswith("sk-ant-"):
        typer.echo(
            "warning: key doesn't start with 'sk-ant-' — Anthropic keys usually do.",
            err=True,
        )
    if typer.confirm(f"Save to {_ANTHROPIC_KEYSTORE} for future runs?", default=True):
        _ANTHROPIC_KEYSTORE.parent.mkdir(parents=True, exist_ok=True)
        _ANTHROPIC_KEYSTORE.write_text(entered)
        try:
            _ANTHROPIC_KEYSTORE.chmod(0o600)
        except OSError:
            pass
        typer.echo(f"✓ saved to {_ANTHROPIC_KEYSTORE} (0600)")
    os.environ["ANTHROPIC_API_KEY"] = entered
    return entered


def _llm_propose(
    *,
    strategy_py: Path,
    results_tsv: Path,
    program_md: Optional[Path],
    iteration: int,
    model: str,
    api_key: str,
) -> tuple[str, str]:
    """LLM-driven proposal — Claude rewrites strategy.py with a single change."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "--agent llm requires the anthropic SDK. Install:\n"
            "  pip install 'anthropic>=0.40.0'"
        ) from e

    strategy_text = strategy_py.read_text()
    history = ""
    if results_tsv.exists():
        lines = results_tsv.read_text().splitlines()
        if len(lines) > 1:
            history = "\n".join(lines[:1] + lines[-20:])
    program_blob = ""
    if program_md and program_md.exists():
        program_blob = program_md.read_text()

    system = (
        "You are an autonomous AI researcher running the autoresearch loop "
        "(Karpathy-style propose → eval → keep/discard) on a Hyperliquid "
        "trading strategy. Read program.md, the current strategy.py, and the "
        "results.tsv history. Propose EXACTLY ONE single-parameter change — "
        "either tweak ONE module-level constant or rewrite ONE small block of "
        "logic. NEVER change multiple things at once (attribution + search-"
        "space discipline). Prefer simplicity. If results.tsv shows ≥5 "
        "consecutive non-improvements, switch to a contrarian / regime-shift "
        "/ radical proposal (Council Mode). Output the COMPLETE replacement "
        "strategy.py inside <strategy>...</strategy> and a one-line human "
        "description inside <desc>...</desc>. Do not include any other prose."
    )
    user = (
        f"# program.md\n{program_blob or '(not present — infer from strategy.py)'}\n\n"
        f"# current strategy.py\n```python\n{strategy_text}\n```\n\n"
        f"# results.tsv (header + last 20 rows)\n{history or '(empty — this is the first proposal)'}\n\n"
        f"Iteration #{iteration}. Propose ONE change.\n"
        "Return ONLY the two tagged blocks:\n"
        "<strategy>\n<the complete, runnable new strategy.py>\n</strategy>\n"
        "<desc>one-line summary of the single change</desc>"
    )

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    body = "".join(getattr(b, "text", "") for b in msg.content)
    sm = re.search(r"<strategy>\s*(.*?)\s*</strategy>", body, re.DOTALL)
    if not sm:
        raise RuntimeError(
            f"llm response missing <strategy>...</strategy> block. First 500 chars:\n{body[:500]}"
        )
    new_text = sm.group(1)
    # Strip optional ```python fences if the model wrapped the code
    new_text = re.sub(r"^```(?:python)?\s*\n", "", new_text)
    new_text = re.sub(r"\n```\s*$", "", new_text)
    if not new_text.strip():
        raise RuntimeError("llm returned an empty <strategy> block")
    dm = re.search(r"<desc>\s*(.*?)\s*</desc>", body, re.DOTALL)
    desc = (dm.group(1).strip() if dm else "llm proposal")[:200]
    return desc, new_text


def _demo_propose(strategy_py: Path, rng: random.Random) -> tuple[str, str]:
    """Pick a numeric module-level constant and perturb it by a factor in [0.8, 1.25]."""
    text = strategy_py.read_text()
    matches = list(_PARAM_PATTERN.finditer(text))
    if not matches:
        return ("no-op: no module-level numeric params found", text)
    pick = rng.choice(matches)
    name = pick.group("name")
    raw = pick.group("val")
    val = float(raw)
    factor = rng.uniform(0.8, 1.25)
    new = val * factor
    if val >= 0 and new < 0:
        new = abs(new)
    if "." not in raw:
        new_str = str(max(1, int(round(new))))
    else:
        new_str = f"{new:.6f}".rstrip("0").rstrip(".")
        if new_str == "":
            new_str = "0.0"
    new_text = text[: pick.start()] + f"{name} = {new_str}" + text[pick.end():]
    desc = f"perturb {name}: {raw} → {new_str} (x{factor:.3f})"
    return desc, new_text


@autoresearch_app.command("run")
def autoresearch_run(
    name: str = typer.Argument(..., help="Strategy name (create with `hl strategy new`)"),
    iterations: int = typer.Option(5, "--iterations", "-n"),
    seed: Optional[int] = typer.Option(None, "--seed"),
    agent: str = typer.Option("demo", "--agent", help="demo | llm — demo perturbs numeric constants; llm uses Anthropic Claude"),
    model: Optional[str] = typer.Option(None, "--model", help=f"Override LLM model when --agent llm (default {_DEFAULT_LLM_MODEL}, or $ANTHROPIC_MODEL)"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Branch tag; default today's date"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Override auto-generated run id"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Run the autoresearch loop. Prints a JSONL event stream + writes results.tsv."""
    strategy_dir = _strategies_root() / name
    if not strategy_dir.exists():
        typer.echo(f"error: strategy {name!r} not found at {strategy_dir}", err=True)
        typer.echo(f"hint:  hl strategy new {name}", err=True)
        raise typer.Exit(code=2)

    if agent not in ("demo", "llm"):
        typer.echo(f"error: --agent={agent!r} not supported. Choose 'demo' or 'llm'.", err=True)
        raise typer.Exit(code=2)

    strategy_py = strategy_dir / "strategy.py"
    results_tsv = strategy_dir / "results.tsv"
    program_md = strategy_dir / "program.md"
    if not strategy_py.exists():
        typer.echo(f"error: {strategy_py} missing", err=True)
        raise typer.Exit(code=2)
    if not results_tsv.exists():
        results_tsv.write_text("commit\tscore\tsharpe\tmax_dd\tstatus\tdescription\n")

    api_key: Optional[str] = None
    llm_model = model or os.environ.get("ANTHROPIC_MODEL", "").strip() or _DEFAULT_LLM_MODEL
    if agent == "llm":
        api_key = _resolve_anthropic_key()
        try:
            import anthropic  # noqa: F401  (early check before the baseline spends time)
        except ImportError:
            typer.echo(
                "error: --agent llm requires the anthropic SDK. Install with:\n"
                "  pip install 'anthropic>=0.40.0'",
                err=True,
            )
            raise typer.Exit(code=2)

    run_id = run_id or uuid.uuid4().hex[:12]
    run_tag = tag or datetime.now(timezone.utc).strftime("%Y%m%d")
    runs_root().mkdir(parents=True, exist_ok=True)
    run_log = runs_root() / f"{run_id}.jsonl"

    def emit(event: str, **payload):
        rec = {"ts": time.time(), "event": event, **payload}
        with run_log.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        if not json_output:
            visible = {k: v for k, v in payload.items() if k not in ("log",)}
            typer.echo(f"[{event}] " + json.dumps(visible))

    emit(
        "run_start",
        run_id=run_id,
        strategy=name,
        iterations=iterations,
        agent=agent,
        tag=run_tag,
        model=llm_model if agent == "llm" else None,
    )

    in_git = (strategy_dir / ".git").exists()
    branch = f"autoresearch/{run_tag}-{run_id}"
    if in_git:
        try:
            _git(["checkout", "-b", branch], cwd=strategy_dir)
        except subprocess.CalledProcessError as e:
            emit("git_branch_failed", error=(e.stderr or str(e))[:200])
            in_git = False

    emit("baseline_start")
    baseline = _run_backtest(strategy_dir)
    emit("baseline_done", **baseline)
    head = "BASELINE"
    if in_git:
        try:
            head = _git(["rev-parse", "--short", "HEAD"], cwd=strategy_dir).stdout.strip()
        except subprocess.CalledProcessError:
            pass
    with results_tsv.open("a") as f:
        f.write(
            f"{head}\t{baseline.get('score') if baseline.get('score') is not None else '?'}"
            f"\t{baseline.get('sharpe') if baseline.get('sharpe') is not None else '?'}"
            f"\t{baseline.get('max_dd') if baseline.get('max_dd') is not None else '?'}"
            f"\tbaseline\tinitial scaffold\n"
        )
    best_score: float = baseline.get("score") if baseline.get("score") is not None else float("-inf")

    rng = random.Random(seed)

    for i in range(1, iterations + 1):
        try:
            if agent == "demo":
                desc, new_text = _demo_propose(strategy_py, rng)
            else:  # agent == "llm"
                assert api_key is not None
                desc, new_text = _llm_propose(
                    strategy_py=strategy_py,
                    results_tsv=results_tsv,
                    program_md=program_md,
                    iteration=i,
                    model=llm_model,
                    api_key=api_key,
                )
        except Exception as e:
            emit("proposal_failed", iteration=i, error=str(e)[:500])
            continue
        emit("proposed", iteration=i, description=desc)
        strategy_py.write_text(new_text)

        commit = "DRY"
        if in_git:
            try:
                _git(["add", "strategy.py"], cwd=strategy_dir)
                _git(["commit", "-m", f"autoresearch {i}: {desc}"], cwd=strategy_dir)
                commit = _git(["rev-parse", "--short", "HEAD"], cwd=strategy_dir).stdout.strip()
            except subprocess.CalledProcessError as e:
                emit("commit_failed", iteration=i, error=(e.stderr or str(e))[:200])

        metrics = _run_backtest(strategy_dir)
        emit("eval_result", iteration=i, **metrics)

        score = metrics.get("score")
        keep = score is not None and score > best_score
        status = "kept" if keep else "discarded"
        if keep:
            best_score = score
            emit("kept", iteration=i, score=score, commit=commit)
        else:
            emit("discarded", iteration=i, score=score, commit=commit)
            if in_git and commit != "DRY":
                try:
                    _git(["reset", "--hard", "HEAD~1"], cwd=strategy_dir)
                except subprocess.CalledProcessError as e:
                    emit("revert_failed", iteration=i, error=(e.stderr or str(e))[:200])

        with results_tsv.open("a") as f:
            f.write(
                f"{commit}\t{score if score is not None else '?'}"
                f"\t{metrics.get('sharpe') if metrics.get('sharpe') is not None else '?'}"
                f"\t{metrics.get('max_dd') if metrics.get('max_dd') is not None else '?'}"
                f"\t{status}\t{desc}\n"
            )

    emit("done", run_id=run_id, best_score=best_score if best_score != float("-inf") else None)

    summary = {
        "run_id": run_id,
        "strategy": name,
        "iterations": iterations,
        "best_score": best_score if best_score != float("-inf") else None,
        "results_tsv": str(results_tsv),
        "log": str(run_log),
        "branch": branch if in_git else None,
    }
    if json_output:
        typer.echo(json.dumps(summary, indent=2))
    else:
        typer.echo(f"\n✓ autoresearch done — run_id={run_id} best_score={summary['best_score']}")
        typer.echo(f"  results: {results_tsv}")
        typer.echo(f"  log:     {run_log}")


@autoresearch_app.command("results")
def autoresearch_results(
    name: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the last N results from results.tsv."""
    path = _strategies_root() / name / "results.tsv"
    if not path.exists():
        typer.echo(f"error: {path} not found", err=True)
        raise typer.Exit(code=2)
    lines = path.read_text().splitlines()
    if not lines:
        typer.echo("(empty)")
        return
    header = lines[0].split("\t")
    rows = [dict(zip(header, line.split("\t"))) for line in lines[1:][-limit:]]
    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return
    typer.echo(f"{'Commit':<12} {'Score':<10} {'Sharpe':<10} {'MaxDD':<10} {'Status':<10} Description")
    typer.echo(f"{'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*40}")
    for r in rows:
        typer.echo(
            f"{r.get('commit','')[:12]:<12} {r.get('score',''):<10} {r.get('sharpe',''):<10}"
            f" {r.get('max_dd',''):<10} {r.get('status',''):<10} {r.get('description','')}"
        )


@autoresearch_app.command("status")
def autoresearch_status(
    name: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show best score / commit / experiment count for a strategy project.

    Read-only summary via the autotrader bridge (Python port of ACC's
    autotrader-bridge.ts results.tsv parser).
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from cli.autotrader_bridge import get_status

    strategy_dir = _strategies_root() / name
    if not strategy_dir.exists():
        typer.echo(f"error: strategy {name!r} not found at {strategy_dir}", err=True)
        raise typer.Exit(code=2)

    st = get_status(strategy_dir)
    if json_output:
        typer.echo(json.dumps({
            "name": st.name,
            "path": st.path,
            "branch": st.branch,
            "data_ready": st.data_ready,
            "best_score": st.best_score,
            "best_commit": st.best_commit,
            "total_experiments": st.total_experiments,
        }, indent=2))
        return

    typer.echo(f"\033[1m{st.name}\033[0m  ({st.path})")
    typer.echo(f"  branch:            {st.branch}")
    typer.echo(f"  data ready:        {st.data_ready}  (BTC/ETH/SOL parquet present)")
    typer.echo(f"  experiments:       {st.total_experiments}")
    typer.echo(f"  best score:        {st.best_score if st.best_score is not None else '—'}")
    typer.echo(f"  best commit:       {st.best_commit or '—'}")
    if st.git_log:
        typer.echo("  recent commits:")
        for e in st.git_log[:5]:
            typer.echo(f"    {e.hash:<10} {e.message}")


@autoresearch_app.command("tail")
def autoresearch_tail(
    run_id: str = typer.Argument(..., help="Run ID returned by `hl autoresearch run`"),
    follow: bool = typer.Option(False, "--follow", "-f"),
):
    """Tail a run's JSONL event log."""
    path = runs_root() / f"{run_id}.jsonl"
    if not path.exists():
        typer.echo(f"error: {path} not found", err=True)
        raise typer.Exit(code=2)
    if not follow:
        typer.echo(path.read_text())
        return
    proc = subprocess.Popen(["tail", "-n", "+1", "-f", str(path)])
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
