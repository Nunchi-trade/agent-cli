"""hl journal — structured trade journal with reasoning."""
from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import Optional

import typer

journal_app = typer.Typer(no_args_is_help=True)


@journal_app.command("view")
def journal_view(
    date: Optional[str] = typer.Option(None, "--date", "-d", help="Filter by date (YYYY-MM-DD)"),
    limit: int = typer.Option(20, "--limit", "-n"),
    data_dir: str = typer.Option("data/apex", "--data-dir"),
    json_out: bool = typer.Option(
        False, "--json",
        help="Emit machine-readable JSON instead of a human table.",
    ),
):
    """View trade journal entries with reasoning."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.journal_guard import JournalGuard

    guard = JournalGuard(data_dir=data_dir)
    entries = guard.read_entries(date=date, limit=limit)

    if json_out:
        typer.echo(_json.dumps({
            "entries": [e.to_dict() for e in entries],
            "total": len(entries),
            "date": date,
        }, default=str))
        return

    if not entries:
        typer.echo("No journal entries found.")
        raise typer.Exit()

    typer.echo(f"{'ID':<30} {'Dir':<6} {'PnL':>10} {'ROE':>8} {'Quality':<6} {'Exit Reason':<20}")
    typer.echo("-" * 85)
    for e in entries:
        typer.echo(
            f"{e.entry_id:<30} {e.direction:<6} "
            f"${e.pnl:>+8.2f} {e.roe_pct:>+6.1f}% "
            f"{e.signal_quality:<6} {e.close_reason:<20}"
        )


@journal_app.command("entry")
def journal_entry(
    entry_id: str = typer.Argument(..., help="Journal entry ID"),
    data_dir: str = typer.Option("data/apex", "--data-dir"),
):
    """Show full detail of a journal entry including reasoning."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.journal_guard import JournalGuard

    guard = JournalGuard(data_dir=data_dir)
    e = guard.get_entry(entry_id)

    if not e:
        typer.echo(f"Entry not found: {entry_id}")
        raise typer.Exit(1)

    hours = e.holding_ms / 3_600_000
    typer.echo(f"ID:        {e.entry_id}")
    typer.echo(f"Instrument: {e.instrument}")
    typer.echo(f"Direction:  {e.direction}")
    typer.echo(f"Entry:      ${e.entry_price:.4f} via {e.entry_source} (score {e.entry_signal_score:.0f})")
    typer.echo(f"Exit:       ${e.exit_price:.4f} — {e.close_reason}")
    typer.echo(f"PnL:        ${e.pnl:+.4f} ({e.roe_pct:+.2f}%)")
    typer.echo(f"Holding:    {hours:.1f}h")
    typer.echo(f"Quality:    {e.signal_quality}")
    typer.echo("")
    typer.echo(f"Entry Reasoning: {e.entry_reasoning}")
    typer.echo(f"Exit Reasoning:  {e.exit_reasoning}")
    typer.echo(f"Retrospective:   {e.retrospective}")
