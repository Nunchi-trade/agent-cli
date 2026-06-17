"""hl order-status — look up a single HL order by its oid."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer


def order_status_cmd(
    oid: str = typer.Argument(..., help="Order id to look up"),
    mainnet: bool = typer.Option(
        False, "--mainnet", help="Use mainnet (default: testnet)",
    ),
):
    """Query the status of a single order by its oid."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(level=logging.WARNING)

    from cli.config import TradingConfig
    from cli.hl_adapter import DirectHLProxy
    from parent.hl_proxy import HLProxy

    cfg = TradingConfig()
    raw_hl = HLProxy(private_key=cfg.get_private_key(), testnet=not mainnet)
    hl = DirectHLProxy(raw_hl)

    status = hl.get_order_status(oid)
    if status is None:
        typer.echo(json.dumps({"oid": oid, "error": "lookup failed"}))
        raise typer.Exit(1)
    typer.echo(json.dumps(status, default=str))
