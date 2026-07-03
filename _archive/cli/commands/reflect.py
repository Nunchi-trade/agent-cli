"""hl reflect — REFLECT performance review commands."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import typer

reflect_app = typer.Typer(no_args_is_help=True)


def _metrics_recommendations(metrics) -> List[str]:
    """Extract the human-readable parameter recommendations from metrics."""
    return list(getattr(metrics, "recommendations", []) or [])


def _write_reflect_artifacts(metrics, output_dir: str, data_dir: str, date: str,
                             trigger: str):
    """Write the report + recommendations artifacts and append an events.jsonl line.

    Returns (report_file, recs_file, summary). Shared by `run` and `schedule` so
    every REFLECT pass produces the same machine-readable surface.
    """
    from modules.reflect_reporter import ReflectReporter
    from cli.events import append_event

    reporter = ReflectReporter()
    report = reporter.generate(metrics, date=date)
    summary = reporter.distill(metrics)
    recs = _metrics_recommendations(metrics)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report_file = out_path / f"{date}.md"
    report_file.write_text(report)

    recs_payload = {
        "date": date,
        "trigger": trigger,
        "win_rate": metrics.win_rate,
        "net_pnl": metrics.net_pnl,
        "fdr": metrics.fdr,
        "round_trips": metrics.total_round_trips,
        "recommendations": recs,
    }
    recs_file = out_path / f"{date}-recommendations.json"
    recs_file.write_text(json.dumps(recs_payload, indent=2, sort_keys=True))

    append_event(data_dir, {
        "type": "reflect_run",
        "trigger": trigger,
        "date": date,
        "round_trips": metrics.total_round_trips,
        "win_rate": round(metrics.win_rate, 2),
        "net_pnl": round(metrics.net_pnl, 4),
        "fdr": round(metrics.fdr, 2),
        "recommendation_count": len(recs),
        "report": str(report_file),
        "recommendations_artifact": str(recs_file),
    })

    return report_file, recs_file, summary


def _load_trades(data_dir: str, since: Optional[str] = None):
    """Load trades from trades.jsonl, optionally filtered by date."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.reflect_engine import TradeRecord

    trades_path = Path(data_dir) / "trades.jsonl"
    if not trades_path.exists():
        return []

    since_ms = 0
    if since:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d")
            since_ms = int(since_dt.timestamp() * 1000)
        except ValueError:
            typer.echo(f"Invalid date format: {since}. Use YYYY-MM-DD.", err=True)
            raise typer.Exit(1)

    trades = []
    with open(trades_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                tr = TradeRecord.from_dict(d)
                if since_ms and tr.timestamp_ms < since_ms:
                    continue
                trades.append(tr)
            except (json.JSONDecodeError, KeyError):
                continue

    return trades


@reflect_app.command("run")
def reflect_run(
    since: Optional[str] = typer.Option(None, "--since", "-s",
                                        help="Only include trades after this date (YYYY-MM-DD)"),
    data_dir: str = typer.Option("data/cli", "--data-dir"),
    output_dir: str = typer.Option("data/reflect", "--output-dir"),
):
    """Run REFLECT performance analysis and generate report."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.reflect_engine import ReflectEngine

    trades = _load_trades(data_dir, since)

    if not trades:
        typer.echo("No trades found. Run some trades first, then come back.")
        raise typer.Exit()

    typer.echo(f"Analyzing {len(trades)} trades...")

    engine = ReflectEngine()
    metrics = engine.compute(trades)

    today = datetime.now().strftime("%Y-%m-%d")
    report_file, recs_file, summary = _write_reflect_artifacts(
        metrics, output_dir=output_dir, data_dir=data_dir, date=today, trigger="run",
    )

    typer.echo(f"\n{summary}")
    typer.echo(f"\nFull report saved to: {report_file}")
    typer.echo(f"Recommendations: {recs_file}")


@reflect_app.command("report")
def reflect_report(
    date: Optional[str] = typer.Option(None, "--date", "-d",
                                       help="Report date (YYYY-MM-DD, default: today)"),
    output_dir: str = typer.Option("data/reflect", "--output-dir"),
):
    """View a REFLECT report."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    report_file = Path(output_dir) / f"{date}.md"

    if not report_file.exists():
        typer.echo(f"No report found for {date}. Run 'hl reflect run' first.")
        raise typer.Exit()

    typer.echo(report_file.read_text())


@reflect_app.command("history")
def reflect_history(
    output_dir: str = typer.Option("data/reflect", "--output-dir"),
    limit: int = typer.Option(10, "--limit", "-n"),
):
    """Show REFLECT report history with trend."""
    out_path = Path(output_dir)
    if not out_path.exists():
        typer.echo("No REFLECT reports found.")
        raise typer.Exit()

    reports = sorted(out_path.glob("*.md"), reverse=True)[:limit]

    if not reports:
        typer.echo("No REFLECT reports found.")
        raise typer.Exit()

    typer.echo(f"{'Date':<12} {'Summary'}")
    typer.echo("-" * 60)

    for report_file in reports:
        date = report_file.stem
        # Extract net PnL and win rate from report
        content = report_file.read_text()
        net_pnl = "?"
        win_rate = "?"
        for line in content.split("\n"):
            if "**Net PnL**" in line:
                parts = line.split("$")
                if len(parts) >= 2:
                    net_pnl = "$" + parts[-1].rstrip("** |")
            if "Win Rate" in line and "%" in line:
                for part in line.split("|"):
                    if "%" in part and "Win Rate" not in part:
                        win_rate = part.strip()
                        break
        typer.echo(f"{date:<12} WR: {win_rate:<20} PnL: {net_pnl}")


@reflect_app.command("proof")
def reflect_proof(
    data_dir: str = typer.Option("data/reflect", "--data-dir"),
    json_output: bool = typer.Option(True, "--json/--no-json",
                                     help="Emit the proof artifact as JSON to stdout"),
):
    """Run the REFLECT analysis pipeline on a fixed fixture — NO live orders.

    Drives the pure ReflectEngine over a deterministic trade fixture so the
    output is byte-stable. Reads/writes nothing live; writes the proof artifact
    to <data_dir>/proof/reflect-proof.json and a summary line to events.jsonl.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.reflect_engine import ReflectEngine, TradeRecord
    from modules.reflect_reporter import ReflectReporter
    from modules import proof_fixtures as fx
    from cli.events import append_event

    trades = [TradeRecord.from_dict(t) for t in fx.reflect_proof_trades()]
    metrics = ReflectEngine().compute(trades)
    summary = ReflectReporter().distill(metrics)
    recs = _metrics_recommendations(metrics)

    artifact = {
        "proof": "reflect",
        "deterministic": True,
        "live_orders": False,
        "trade_count": len(trades),
        "metrics": {
            "total_round_trips": metrics.total_round_trips,
            "win_count": metrics.win_count,
            "loss_count": metrics.loss_count,
            "win_rate": metrics.win_rate,
            "gross_pnl": metrics.gross_pnl,
            "total_fees": metrics.total_fees,
            "net_pnl": metrics.net_pnl,
            "fdr": metrics.fdr,
            "long_count": metrics.long_count,
            "short_count": metrics.short_count,
            "long_pnl": metrics.long_pnl,
            "short_pnl": metrics.short_pnl,
            "orphan_trade_count": metrics.orphan_trade_count,
            "best_trade_pnl": metrics.best_trade_pnl,
            "worst_trade_pnl": metrics.worst_trade_pnl,
        },
        "recommendations": recs,
    }

    proof_dir = Path(data_dir) / "proof"
    proof_dir.mkdir(parents=True, exist_ok=True)
    proof_file = proof_dir / "reflect-proof.json"
    proof_file.write_text(json.dumps(artifact, indent=2, sort_keys=True))

    append_event(data_dir, {
        "type": "reflect_proof",
        "round_trips": metrics.total_round_trips,
        "win_rate": round(metrics.win_rate, 2),
        "net_pnl": round(metrics.net_pnl, 4),
        "recommendation_count": len(recs),
        "live_orders": False,
        "artifact": str(proof_file),
    })

    if json_output:
        typer.echo(json.dumps(artifact, indent=2, sort_keys=True))
    else:
        typer.echo("REFLECT proof OK — no live orders.")
        typer.echo(summary)
        typer.echo(f"Artifact: {proof_file}")


def _seconds_until_utc(target_hhmm: str) -> float:
    """Seconds from now until the next occurrence of HH:MM UTC."""
    try:
        hh, mm = (int(x) for x in target_hhmm.split(":"))
    except ValueError:
        raise typer.BadParameter(f"--utc must be HH:MM (got {target_hhmm!r})")
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise typer.BadParameter(f"--utc out of range (got {target_hhmm!r})")
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _run_reflect_pass(trades_dir: str, output_dir: str, data_dir: str, trigger: str,
                      use_fixture: bool):
    """Compute metrics + write artifacts for one scheduled pass. Returns summary or None."""
    from modules.reflect_engine import ReflectEngine, TradeRecord

    if use_fixture:
        from modules import proof_fixtures as fx
        trades = [TradeRecord.from_dict(t) for t in fx.reflect_proof_trades()]
    else:
        trades = _load_trades(trades_dir, None)
        if not trades:
            return None

    metrics = ReflectEngine().compute(trades)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_file, recs_file, summary = _write_reflect_artifacts(
        metrics, output_dir=output_dir, data_dir=data_dir, date=date, trigger=trigger,
    )
    return report_file, recs_file, summary


@reflect_app.command("schedule")
def reflect_schedule(
    utc: str = typer.Option("04:00", "--utc",
                            help="Daily run time in UTC, HH:MM (e.g. 04:00)"),
    once: bool = typer.Option(False, "--once",
                              help="Run a single pass now and exit (for testing — no waiting)"),
    dry: bool = typer.Option(False, "--dry",
                             help="Use the deterministic fixture instead of live trades (implies --once)"),
    trades_dir: str = typer.Option("data/cli", "--trades-dir",
                                   help="Directory containing trades.jsonl"),
    output_dir: str = typer.Option("data/reflect", "--output-dir",
                                   help="Where REFLECT report + recommendation artifacts land"),
    data_dir: str = typer.Option("data/reflect", "--data-dir",
                                 help="Where events.jsonl is appended"),
):
    """Schedule the REFLECT pass to run daily at a UTC time.

    Writes the report (.md) + parameter recommendations (.json) under the reflect
    output dir and appends a summary line to events.jsonl on each pass. Use
    --once (or --dry) to run a single pass immediately without waiting for the
    scheduled time.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # --dry implies a single fixture-backed pass so it never needs live trades.
    single = once or dry
    trigger = "schedule_dry" if dry else ("schedule_once" if once else "schedule")

    if single:
        result = _run_reflect_pass(trades_dir, output_dir, data_dir, trigger, use_fixture=dry)
        if result is None:
            typer.echo("No trades found. Run some trades first, or use --dry for a fixture pass.")
            raise typer.Exit()
        report_file, recs_file, summary = result
        typer.echo(f"\n{summary}")
        typer.echo(f"\nReport: {report_file}")
        typer.echo(f"Recommendations: {recs_file}")
        return

    # Long-running daemon: sleep until the next UTC target, run, repeat.
    typer.echo(f"REFLECT scheduler started — daily at {utc} UTC. Ctrl-C to stop.")
    while True:
        wait_s = _seconds_until_utc(utc)
        typer.echo(f"Next run in {wait_s / 3600:.1f}h (at {utc} UTC).")
        time.sleep(wait_s)
        result = _run_reflect_pass(trades_dir, output_dir, data_dir, "schedule", use_fixture=False)
        if result is None:
            typer.echo("REFLECT pass skipped — no trades found.")
        else:
            report_file, _recs_file, summary = result
            typer.echo(f"REFLECT pass complete: {report_file}")
        # Avoid double-firing within the same minute window.
        time.sleep(61)
