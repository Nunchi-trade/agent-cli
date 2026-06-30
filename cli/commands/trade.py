"""hl trade — place a single manual order."""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import typer


def _confirm_trade(yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        typer.echo("Refusing to trade non-interactively without --yes.", err=True)
        raise typer.Exit(2)
    if not typer.confirm("Confirm?"):
        raise typer.Exit(0)


def trade_cmd(
    instrument: str = typer.Argument(
        "ETH-PERP",
        help="Instrument (ETH-PERP, VXX-USDYP, US3M-USDYP)",
    ),
    side: str = typer.Argument(
        ...,
        help="buy or sell",
    ),
    size: float = typer.Argument(
        ...,
        help="Order size (e.g., 0.5)",
    ),
    price: float = typer.Option(
        0.0, "--price", "-p",
        help="Limit price (0 = use oracle/mid for IOC)",
    ),
    mainnet: bool = typer.Option(
        False, "--mainnet",
        help="Use mainnet (default: testnet)",
    ),
    tif: str = typer.Option(
        "Ioc", "--tif",
        help="Time in force: Ioc, Gtc, or Alo",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Submit without interactive confirmation.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the resolved order plan without submitting it.",
    ),
    max_notional_usd: Optional[float] = typer.Option(
        None, "--max-notional",
        help="Reject if size * price exceeds this USD notional cap.",
    ),
    decision_call_id: Optional[str] = typer.Option(
        None, "--decision-call-id",
        help="Optional LLM decision ID to join this trade to cost ledgers.",
    ),
    tick_index: Optional[int] = typer.Option(
        None, "--tick-index",
        help="Optional strategy tick index to join this trade to runtime/cost ledgers.",
    ),
    generation_id: Optional[str] = typer.Option(
        None, "--generation-id",
        help="Optional provider generation ID to join this trade to route ledgers.",
    ),
):
    """Place a single order on Hyperliquid."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    from cli.config import TradingConfig
    from cli.hl_adapter import DirectHLProxy
    from cli.strategy_registry import resolve_instrument
    from modules.cost_metering import ExperimentContext
    from parent.hl_proxy import HLProxy
    from parent.store import JSONLStore

    instrument = resolve_instrument(instrument)
    cfg = TradingConfig()
    private_key = cfg.get_private_key()

    raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
    hl = DirectHLProxy(raw_hl)

    # If no price given, use mid from snapshot
    if price <= 0:
        snap = hl.get_snapshot(instrument)
        if snap.mid_price <= 0:
            typer.echo("Error: could not fetch market data for price", err=True)
            raise typer.Exit(1)
        # For IOC: use mid + slippage
        if side.lower() == "buy":
            price = round(snap.ask * 1.001, 4)
        else:
            price = round(snap.bid * 0.999, 4)
        typer.echo(f"Using market price: {price}")

    network = "mainnet" if mainnet else "testnet"
    notional_usd = abs(size * price)
    notional_cap = cfg.max_notional_usd if max_notional_usd is None else max_notional_usd
    if notional_cap <= 0:
        typer.echo("Error: max notional must be positive", err=True)
        raise typer.Exit(1)
    if notional_usd > notional_cap:
        typer.echo(
            f"Refusing order: notional ${notional_usd:.2f} exceeds "
            f"max notional ${notional_cap:.2f}",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(
        f"Placing {side.upper()} {size} {instrument} @ {price} ({tif}) on {network} "
        f"(notional=${notional_usd:.2f}, max=${notional_cap:.2f})"
    )

    if dry_run:
        typer.echo("Dry run: order not submitted.")
        return

    _confirm_trade(yes)

    fill = hl.place_order(
        instrument=instrument,
        side=side.lower(),
        size=size,
        price=price,
        tif=tif,
    )

    if fill:
        typer.echo(
            f"Filled: {fill.side.upper()} {fill.quantity} {fill.instrument} "
            f"@ {fill.price} (oid={fill.oid})"
        )
        experiment = ExperimentContext.from_env("manual_trade")
        if experiment.enabled:
            data_dir = os.environ.get("NUNCHI_COST_DATA_DIR") or os.environ.get("DATA_DIR", "data/cli")
            trade_log = JSONLStore(
                os.environ.get("NUNCHI_TRADE_LEDGER_PATH") or str(Path(data_dir) / "trades.jsonl")
            )
            trade_log.append({
                **experiment.ledger_fields(),
                "ts": int(time.time() * 1000),
                "tick": tick_index,
                "tick_index": tick_index,
                "decision_call_id": decision_call_id,
                "generation_id": generation_id,
                "oid": fill.oid,
                "cloid": getattr(fill, "cloid", None),
                "instrument": fill.instrument,
                "side": fill.side,
                "price": str(fill.price),
                "quantity": str(fill.quantity),
                "notional_usd": str(fill.price * fill.quantity),
                "timestamp_ms": fill.timestamp_ms,
                "fee": str(fill.fee),
                "strategy": "manual_trade",
                "route": "cli.trade",
                "network": network,
            })
    else:
        typer.echo("No fill (order may have been rejected or not matched)")
