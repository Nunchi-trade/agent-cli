"""hl account — show HL account state."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from cli.config import TradingConfig
from cli.venue_factory import build_venue_adapter, normalize_venue


def account_cmd(
    mainnet: bool = typer.Option(
        False, "--mainnet",
        help="Use mainnet (default: testnet)",
    ),
    venue: str = typer.Option(
        "hl", "--venue", "-v",
        help="Trading venue (hl, paradex)",
    ),
):
    """Show account state for the selected venue."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(level=logging.WARNING)

    from cli.display import account_table

    try:
        cfg = TradingConfig(venue=normalize_venue(venue), mainnet=mainnet)
        execution_venue, _ = build_venue_adapter(venue=cfg.venue, mainnet=mainnet, mock=False)
    except (RuntimeError, ValueError, NotImplementedError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    state = execution_venue.get_account_state()

    if not state:
        typer.echo("Failed to fetch account state", err=True)
        raise typer.Exit(1)

    typer.echo(account_table(state))
