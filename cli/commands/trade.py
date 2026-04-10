"""hl trade — place a single manual order."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from cli.venue_factory import build_venue_adapter, normalize_venue


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
    venue: str = typer.Option(
        "hl", "--venue", "-v",
        help="Trading venue (hl, paradex)",
    ),
    tif: str = typer.Option(
        "Ioc", "--tif",
        help="Time in force: Ioc, Gtc, or Alo",
    ),
):
    """Place a single order on the selected venue."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    from cli.config import TradingConfig
    from cli.strategy_registry import resolve_instrument

    instrument = resolve_instrument(instrument)
    try:
        cfg = TradingConfig(venue=normalize_venue(venue), mainnet=mainnet)
        execution_venue, _ = build_venue_adapter(venue=cfg.venue, mainnet=mainnet, mock=False)
    except (RuntimeError, ValueError, NotImplementedError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    # If no price given, use mid from snapshot
    if price <= 0:
        snap = execution_venue.get_snapshot(instrument)
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
    typer.echo(f"Placing {side.upper()} {size} {instrument} @ {price} ({tif}) on {cfg.venue}/{network}")

    confirm = typer.confirm("Confirm?")
    if not confirm:
        raise typer.Exit(0)

    fill = execution_venue.place_order(
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
    else:
        typer.echo("No fill (order may have been rejected or not matched)")
