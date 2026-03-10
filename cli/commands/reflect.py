"""hl reflect — REFLECT performance review commands."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer

reflect_app = typer.Typer(no_args_is_help=True)


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
    from modules.reflect_reporter import ReflectReporter

    trades = _load_trades(data_dir, since)

    if not trades:
        typer.echo("No trades found. Run some trades first, then come back.")
        raise typer.Exit()

    typer.echo(f"Analyzing {len(trades)} trades...")

    engine = ReflectEngine()
    metrics = engine.compute(trades)
    reporter = ReflectReporter()

    today = datetime.now().strftime("%Y-%m-%d")
    report = reporter.generate(metrics, date=today)
    summary = reporter.distill(metrics)

    # Save report
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report_file = out_path / f"{today}.md"
    report_file.write_text(report)

    typer.echo(f"\n{summary}")
    typer.echo(f"\nFull report saved to: {report_file}")


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
