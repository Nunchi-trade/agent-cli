"""hl schedule-cancel — arm/clear Hyperliquid's dead-man's switch."""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import typer


def schedule_cancel_cmd(
    seconds: int = typer.Argument(
        60, help="Cancel all open orders this many seconds from now (HL minimum ~5s)",
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Clear any scheduled cancel instead of setting one",
    ),
    mainnet: bool = typer.Option(
        False, "--mainnet", help="Use mainnet (default: testnet)",
    ),
):
    """Arm or clear Hyperliquid's dead-man's switch.

    With no flags, schedules cancellation of ALL open orders `seconds` from now;
    re-run before then to refresh it. If the agent process dies without
    refreshing, HL cancels every resting order automatically. Use --clear to
    remove a scheduled cancel.
    """
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

    if clear:
        ok = hl.schedule_cancel(None)
        typer.echo(json.dumps({"action": "clear", "ok": ok}))
        raise typer.Exit(0 if ok else 1)

    if seconds < 5:
        typer.echo("Error: schedule must be at least 5 seconds in the future", err=True)
        raise typer.Exit(1)

    cancel_at_ms = int(time.time() * 1000) + seconds * 1000
    ok = hl.schedule_cancel(cancel_at_ms)
    typer.echo(json.dumps({
        "action": "schedule",
        "cancel_at_ms": cancel_at_ms,
        "seconds_from_now": seconds,
        "ok": ok,
    }))
    raise typer.Exit(0 if ok else 1)
