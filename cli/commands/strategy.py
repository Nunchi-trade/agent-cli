"""hl strategy — strategy scaffold, lifecycle, and live load.

Each strategy is a project directory holding the autotrader scaffold (program.md
+ strategy.py + backtest.py + prepare.py + benchmarks/ + results.tsv). The
autoresearch loop (`hl autoresearch run`) mutates strategy.py and logs to
results.tsv. Once a strategy is found, `hl strategy load` wraps the SAME hourly
`on_bar` strategy in the AutoBarStrategy tick adapter and runs it through the
live TradingEngine.

The scaffold was ported from Nunchi-trade/house (apps/autotrader) via nunchi-cli.

Commands:
  hl strategy new <name>       scaffold a new strategy project from the template
  hl strategy list             list local strategies + registered runtime strategies
  hl strategy show <name>      show metadata + latest results
  hl strategy path <name>      print the strategy directory path
  hl strategy prepare <name>   download/prepare backtest data (runs prepare.py)
  hl strategy load <name>      run the strategy live (on_bar -> on_tick bridge)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

strategy_app = typer.Typer(
    name="strategy",
    help="Strategy scaffold, lifecycle, and live load (autotrader template). See `hl strategies` for the runtime registry.",
    no_args_is_help=True,
    add_completion=False,
)


def strategies_root() -> Path:
    """Default root for local strategy projects. Override with NUNCHI_STRATEGIES_DIR."""
    override = os.environ.get("NUNCHI_STRATEGIES_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".nunchi" / "strategies"


def template_root() -> Path:
    """Built-in autotrader scaffold template."""
    return Path(__file__).resolve().parent.parent.parent / "spawn" / "templates" / "strategy"


def _python_runner(strategy_dir: Path) -> list[str]:
    """Prefer `uv run python` if uv is installed and the strategy has a
    pyproject.toml; else the current interpreter."""
    if shutil.which("uv") and (strategy_dir / "pyproject.toml").exists():
        return ["uv", "run", "python"]
    return [sys.executable]


@strategy_app.command("new")
def strategy_new(
    name: str = typer.Argument(..., help="Strategy name (also used as dir + branch tag)"),
    dir: Optional[Path] = typer.Option(None, "--dir", "-d", help="Parent directory override (default ~/.nunchi/strategies/)"),
    no_git: bool = typer.Option(False, "--no-git", help="Skip git init"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Scaffold a new strategy project from the autotrader template."""
    parent = (dir or strategies_root()).expanduser().resolve()
    target = parent / name

    if target.exists():
        typer.echo(f"error: {target} already exists", err=True)
        raise typer.Exit(code=2)

    template = template_root()
    if not template.exists():
        typer.echo(f"error: template not found at {template}", err=True)
        raise typer.Exit(code=2)

    parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, target, ignore=shutil.ignore_patterns("__pycache__"))

    # .gitignore experiment artifacts so `git reset --hard` (discard path) won't
    # roll them back.
    (target / ".gitignore").write_text(
        "# autoresearch experiment artifacts — untracked so discard-resets don't clobber them\n"
        "results.tsv\n"
        "run.log\n"
        "council_log.md\n"
        "uv.lock\n"
        "__pycache__/\n"
        ".venv/\n"
        ".pytest_cache/\n"
    )

    # Seed results.tsv header (autoresearch loop appends rows here).
    (target / "results.tsv").write_text("commit\tscore\tsharpe\tmax_dd\tstatus\tdescription\n")

    git_initialized = False
    if not no_git:
        try:
            subprocess.run(["git", "init", "-q"], cwd=target, check=True)
            subprocess.run(["git", "add", "-A"], cwd=target, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", f"scaffold strategy {name} from autotrader template"],
                cwd=target, check=True,
            )
            git_initialized = True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            typer.echo(f"warn: git init failed ({e}); continuing without git", err=True)

    payload = {
        "name": name,
        "path": str(target),
        "files": sorted(p.name for p in target.iterdir() if not p.name.startswith(".")),
        "git": git_initialized,
        "template_source": "Nunchi-trade/house:apps/autotrader (via nunchi-cli)",
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"✓ scaffolded {name} at {target}")
        typer.echo(f"  git:   {'initialized' if git_initialized else 'skipped'}")
        typer.echo(f"  next:  hl strategy prepare {name}   (download data)")
        typer.echo(f"         hl autoresearch run {name}    (start the loop)")
        typer.echo(f"         hl strategy load {name} --mock  (dry-run the bridge)")


@strategy_app.command("list")
def strategy_list(
    json_output: bool = typer.Option(False, "--json"),
):
    """List local strategy projects + runtime-registered strategies."""
    root = strategies_root()
    local: list[dict] = []
    if root.exists():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            results_tsv = entry / "results.tsv"
            latest_score: Optional[float] = None
            iterations = 0
            if results_tsv.exists():
                lines = results_tsv.read_text().splitlines()[1:]  # skip header
                iterations = len(lines)
                if lines:
                    parts = lines[-1].split("\t")
                    if len(parts) >= 2:
                        try:
                            latest_score = float(parts[1])
                        except ValueError:
                            pass
            local.append({
                "name": entry.name,
                "path": str(entry),
                "iterations": iterations,
                "latest_score": latest_score,
            })

    registered: list[str] = []
    try:
        project_root = str(Path(__file__).resolve().parent.parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from cli.strategy_registry import STRATEGY_REGISTRY
        registered = sorted(STRATEGY_REGISTRY.keys())
    except Exception as e:
        if not json_output:
            typer.echo(f"warn: could not load runtime registry: {e}", err=True)

    if json_output:
        typer.echo(json.dumps({"local": local, "registered": registered}, indent=2))
        return

    if local:
        typer.echo(f"\033[1mLocal strategy projects ({root}):\033[0m")
        typer.echo(f"{'Name':<24} {'Iterations':<12} {'Latest Score':<14} Path")
        typer.echo(f"{'-'*24} {'-'*12} {'-'*14} {'-'*40}")
        for s in local:
            score = f"{s['latest_score']:.4f}" if s["latest_score"] is not None else "—"
            typer.echo(f"\033[36m{s['name']:<24}\033[0m {s['iterations']:<12} {score:<14} {s['path']}")
    else:
        typer.echo("(no local strategies — run `hl strategy new <name>` to scaffold one)")

    typer.echo("")
    typer.echo(f"\033[1mRuntime registry ({len(registered)} strategies — `hl strategies` for full table):\033[0m")
    for name in registered:
        typer.echo(f"  • {name}")


@strategy_app.command("show")
def strategy_show(
    name: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show strategy metadata + last 5 results."""
    path = strategies_root() / name
    if not path.exists():
        typer.echo(f"error: {path} does not exist", err=True)
        raise typer.Exit(code=2)

    program = (path / "program.md").read_text() if (path / "program.md").exists() else "(no program.md)"
    strategy_lines = (path / "strategy.py").read_text().count("\n") if (path / "strategy.py").exists() else 0
    results_tsv = path / "results.tsv"
    iterations = 0
    rows: list[dict] = []
    if results_tsv.exists():
        lines = results_tsv.read_text().splitlines()
        if lines:
            header = lines[0].split("\t")
            iterations = len(lines) - 1
            for line in lines[1:][-5:]:
                parts = line.split("\t")
                rows.append(dict(zip(header, parts)))

    payload = {
        "name": name,
        "path": str(path),
        "strategy_py_lines": strategy_lines,
        "iterations": iterations,
        "last_5": rows,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"\033[1m{name}\033[0m  ({path})")
    typer.echo(f"  strategy.py: {strategy_lines} lines")
    typer.echo(f"  iterations:  {iterations}")
    if rows:
        typer.echo("  last 5 results:")
        for r in rows:
            typer.echo(
                f"    {r.get('commit', '')[:12]:<12}  score={r.get('score', '?'):<10}"
                f"  status={r.get('status', '?'):<10}  {r.get('description', '')}"
            )
    typer.echo("\n--- program.md (first 500 chars) ---")
    typer.echo(program[:500])


@strategy_app.command("path")
def strategy_path(name: str = typer.Argument(...)):
    """Print the strategy directory path. Useful for `cd $(hl strategy path foo)`."""
    typer.echo(str(strategies_root() / name))


@strategy_app.command("prepare")
def strategy_prepare(
    name: str = typer.Argument(...),
    symbols: Optional[str] = typer.Option(None, "--symbols", help="Comma-separated symbol list (e.g. BTC,BTCSWP,GOLD)"),
):
    """Download/prepare backtest data (delegates to prepare.py inside the strategy dir)."""
    strategy_dir = strategies_root() / name
    if not strategy_dir.exists():
        typer.echo(f"error: {strategy_dir} not found", err=True)
        raise typer.Exit(code=2)
    runner = _python_runner(strategy_dir)
    cmd = runner + ["prepare.py"]
    if symbols:
        # prepare.py takes space-separated --symbols; split the comma list.
        cmd += ["--symbols"] + [s.strip() for s in symbols.split(",") if s.strip()]
    proc = subprocess.run(cmd, cwd=strategy_dir)
    raise typer.Exit(code=proc.returncode)


def _best_score(strategy_dir: Path) -> Optional[float]:
    """Read the best (max) score from results.tsv — the drift baseline."""
    results_tsv = strategy_dir / "results.tsv"
    if not results_tsv.exists():
        return None
    best: Optional[float] = None
    for line in results_tsv.read_text().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 2:
            try:
                s = float(parts[1])
            except ValueError:
                continue
            if best is None or s > best:
                best = s
    return best


@strategy_app.command("load")
def strategy_load(
    name: str = typer.Argument(..., help="Strategy project name (created via `hl strategy new`)"),
    instrument: str = typer.Option(
        "ETH-PERP", "--instrument", "-i",
        help="Instrument to trade (ETH-PERP, BTC-PERP, BTCSWP-USDYP, xyz commodity instruments)",
    ),
    tick_interval: float = typer.Option(10.0, "--tick", "-t", help="Seconds between ticks"),
    ticks_per_hour: int = typer.Option(
        360, "--ticks-per-hour",
        help="Ticks folded into one on_bar boundary (360 = 1h @ 10s). Lower for fast tests.",
    ),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run the bridge but place no real orders"),
    mock: bool = typer.Option(False, "--mock", help="Use mock market data (no HL connection)"),
    max_ticks: int = typer.Option(0, "--max-ticks", help="Stop after N ticks (0 = forever)"),
    resume: bool = typer.Option(True, "--resume/--fresh", help="Resume from saved state or start fresh"),
    data_dir: Optional[str] = typer.Option(None, "--data-dir", help="State + trade log dir (default data/cli/<name>)"),
    strategy_path: Optional[Path] = typer.Option(
        None, "--strategy-path",
        help="Override path to strategy.py (default ~/.nunchi/strategies/<name>/strategy.py)",
    ),
):
    """Run an autoresearch strategy LIVE via the on_bar -> on_tick bridge.

    Wraps the project's hourly `on_bar` strategy in AutoBarStrategy (rolling
    tick->bar buffer, signed-USD target -> order translation, drift detection,
    reduce_only/safe_mode guardrails) and runs it through the standard
    TradingEngine — the same loop `hl run` uses.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.config import TradingConfig
    from cli.strategy_registry import resolve_instrument
    from sdk.strategy_sdk.autobar_adapter import AutoBarStrategy

    strat_dir = strategies_root() / name
    strat_py = strategy_path.expanduser().resolve() if strategy_path else (strat_dir / "strategy.py")
    if not strat_py.exists():
        typer.echo(f"error: strategy.py not found at {strat_py}", err=True)
        typer.echo(f"hint:  hl strategy new {name}", err=True)
        raise typer.Exit(code=2)

    cfg = TradingConfig()
    cfg.strategy = name
    cfg.instrument = resolve_instrument(instrument)
    cfg.tick_interval = tick_interval
    cfg.mainnet = mainnet
    cfg.dry_run = dry_run
    cfg.max_ticks = max_ticks
    cfg.data_dir = data_dir or f"data/cli/{name}"

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Network guard: prevent wrong-chain accidents (mirrors run.py) ──
    if cfg.mainnet:
        if os.environ.get("HL_TESTNET", "true").lower() == "true":
            typer.echo(
                "FATAL: --mainnet set but HL_TESTNET=true in environment. Refusing to start.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if os.environ.get("HL_TESTNET", "true").lower() == "false":
            typer.echo(
                "FATAL: testnet mode but HL_TESTNET=false in environment. Pass --mainnet or fix env.",
                err=True,
            )
            raise typer.Exit(code=1)

    # Drift baseline: best kept backtest score from results.tsv.
    backtest_score = _best_score(strat_dir)

    def _retrain_hook(reason: str, stats: dict) -> None:
        # Retrain-trigger hook: the runner just logs + drops a marker file other
        # tooling (or an autoresearch supervisor) can poll. Kept real but simple.
        logging.getLogger("autobar").warning(
            "RETRAIN TRIGGER [%s]: %s | %s", name, reason, json.dumps(stats),
        )
        try:
            marker = strat_dir / "retrain.flag"
            marker.write_text(json.dumps({"reason": reason, **stats}, indent=2))
            typer.echo(f"  ↳ wrote retrain marker: {marker}")
        except OSError:
            pass

    strategy_instance = AutoBarStrategy(
        strategy_id=name,
        strategy_path=str(strat_py),
        ticks_per_hour=ticks_per_hour,
        backtest_score=backtest_score,
        retrain_callback=_retrain_hook,
    )

    # Build HL adapter (mirrors run.py).
    if mock or dry_run:
        from cli.hl_adapter import DirectMockProxy
        hl = DirectMockProxy()
        typer.echo(f"Mode: {'DRY RUN' if dry_run else 'MOCK'}")
    else:
        from cli.hl_adapter import DirectHLProxy
        from parent.hl_proxy import HLProxy
        private_key = cfg.get_private_key()
        raw_hl = HLProxy(private_key=private_key, testnet=not cfg.mainnet)
        hl = DirectHLProxy(raw_hl)
        typer.echo(f"Mode: LIVE ({'mainnet' if cfg.mainnet else 'testnet'})")

    builder_cfg = cfg.get_builder_config()
    builder_info = builder_cfg.to_builder_info()

    typer.echo(f"Strategy:    {name} (on_bar -> on_tick bridge)")
    typer.echo(f"  source:    {strat_py}")
    typer.echo(f"Instrument:  {cfg.instrument}")
    typer.echo(f"Tick:        {cfg.tick_interval}s   bar boundary every {ticks_per_hour} ticks")
    typer.echo(
        f"Drift base:  backtest score = "
        f"{backtest_score if backtest_score is not None else 'n/a (no results.tsv)'}"
    )
    if cfg.max_ticks > 0:
        typer.echo(f"Max ticks:   {cfg.max_ticks}")
    typer.echo("")

    from cli.engine import TradingEngine

    engine = TradingEngine(
        hl=hl,
        strategy=strategy_instance,
        instrument=cfg.instrument,
        tick_interval=cfg.tick_interval,
        dry_run=cfg.dry_run,
        data_dir=cfg.data_dir,
        risk_limits=cfg.to_risk_limits(),
        builder=builder_info,
    )
    engine.run(max_ticks=cfg.max_ticks, resume=resume)
