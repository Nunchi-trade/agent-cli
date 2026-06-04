"""hl funding — show current Hyperliquid funding rates."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer


def funding_cmd(
    coin: str = typer.Argument(
        None, help="Optional coin/instrument filter (e.g. ETH or ETH-PERP)",
    ),
    mainnet: bool = typer.Option(
        False, "--mainnet", help="Use mainnet (default: testnet)",
    ),
):
    """Show current (hourly) funding rates for all perps, or a single coin."""
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

    rates = hl.get_funding_rates(coin)
    typer.echo(json.dumps(rates, default=str))
