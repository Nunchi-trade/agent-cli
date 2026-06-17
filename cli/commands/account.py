"""hl account — show HL account state."""
from __future__ import annotations

import json as _json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer


def account_cmd(
    mainnet: bool = typer.Option(
        False, "--mainnet",
        help="Use mainnet (default: testnet)",
    ),
    address: Optional[str] = typer.Option(
        None, "--address", "-a",
        help="Read-only: fetch this 0x address from HL public API (no key needed). "
             "Falls back to HL_VIEW_AS_USER env var.",
    ),
    json_out: bool = typer.Option(
        False, "--json",
        help="Emit machine-readable JSON instead of a human table.",
    ),
):
    """Show Hyperliquid account state (margin, balance).

    With --address (or HL_VIEW_AS_USER) the command runs read-only against
    that address using only public info endpoints — no private key required.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(level=logging.WARNING)

    from cli.display import account_table
    from cli.hl_adapter import read_only_account_state
    from cli.view_mode import view_address

    # Resolve a read-only address from --address or HL_VIEW_AS_USER.
    ro_address = view_address(address)

    if ro_address:
        # Read-only path: no key, no signing.
        state = read_only_account_state(ro_address, testnet=not mainnet)
    else:
        # Authenticated path: resolve key and query own account.
        from cli.config import TradingConfig
        from cli.hl_adapter import DirectHLProxy
        from parent.hl_proxy import HLProxy

        cfg = TradingConfig()
        private_key = cfg.get_private_key()
        raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
        hl = DirectHLProxy(raw_hl)
        state = hl.get_account_state()

    if not state:
        if json_out:
            typer.echo(_json.dumps({"error": "failed_to_fetch_account_state", "address": ro_address}))
        else:
            typer.echo("Failed to fetch account state", err=True)
        raise typer.Exit(1)

    if json_out:
        state = dict(state)
        state["network"] = "mainnet" if mainnet else "testnet"
        state["view_only"] = ro_address is not None
        typer.echo(_json.dumps(state, default=str))
    else:
        typer.echo(account_table(state))
