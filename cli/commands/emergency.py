"""hl emergency-close — cancel all orders and market-close all positions."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer


def emergency_close_cmd(
    confirm: bool = typer.Option(
        False, "--confirm",
        help="Skip the interactive prompt (required for non-interactive / MCP use)",
    ),
    mainnet: bool = typer.Option(
        False, "--mainnet", help="Use mainnet (default: testnet)",
    ),
):
    """EMERGENCY: cancel ALL open orders and market-close ALL positions (reduce-only).

    This is a kill-switch. No builder fee is attached to the closes — reliability
    matters more than a few bps on a panic exit.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(level=logging.WARNING)

    network = "mainnet" if mainnet else "testnet"
    if not confirm:
        confirm = typer.confirm(f"Close ALL positions and cancel ALL orders on {network}?")
    if not confirm:
        raise typer.Exit(0)

    from cli.config import TradingConfig
    from cli.hl_adapter import DirectHLProxy
    from parent.hl_proxy import HLProxy

    cfg = TradingConfig()
    raw_hl = HLProxy(private_key=cfg.get_private_key(), testnet=not mainnet)
    hl = DirectHLProxy(raw_hl)

    summary = hl.emergency_close_all()
    typer.echo(json.dumps(summary, default=str))
    # Non-zero exit if anything went wrong so callers can detect partial failure.
    raise typer.Exit(1 if summary.get("errors") else 0)
