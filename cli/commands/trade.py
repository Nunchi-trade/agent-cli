"""hl trade — place a single manual order."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer


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
        False, "--yes",
        help="Skip interactive confirmation. Intended for trusted automation.",
    ),
    policy: Optional[Path] = typer.Option(
        None, "--policy",
        help="Session policy file (or inline JSON / NUNCHI_SESSION_POLICY env). "
             "Local guard only — no web-auth, no network.",
    ),
):
    """Place a single order on Hyperliquid."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.view_mode import require_not_view_only

    require_not_view_only()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    from cli.config import TradingConfig
    from cli.hl_adapter import DirectHLProxy
    from cli.session_policy import ACTION_TRADE, current_workspace, guard_or_exit
    from cli.strategy_registry import resolve_instrument
    from parent.hl_proxy import HLProxy

    instrument = resolve_instrument(instrument)
    network = "mainnet" if mainnet else "testnet"
    policy_path = str(policy) if policy else None

    # ── Session policy guard, part 1: action/market/network (pre-connection) ──
    # Notional + daily are enforced below, once the price is known. This first
    # call short-circuits disallowed markets/networks before we touch the key
    # or hit the network at all. Permissive no-op if no policy is configured.
    guard_or_exit(
        ACTION_TRADE,
        policy_path=policy_path,
        network=network,
        market=instrument,
    )

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

    # ── Session policy guard, part 2: per-action + daily notional ──
    # Notional is computed locally from the resolved price (size * price); no
    # extra network call. The signer address comes from the local key. On a
    # violation this exits(2) before any confirmation or order placement.
    notional_usd = abs(float(size) * float(price))
    pol = guard_or_exit(
        ACTION_TRADE,
        policy_path=policy_path,
        wallet=getattr(hl, "_address", None),
        network=network,
        market=instrument,
        notional_usd=notional_usd,
    )

    typer.echo(f"Placing {side.upper()} {size} {instrument} @ {price} ({tif}) on {network}")

    confirm = yes or typer.confirm("Confirm?")
    if not confirm:
        raise typer.Exit(0)

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
        # Record realised notional against the local daily counter so subsequent
        # orders this UTC day count toward daily_notional_limit_usd. No-op when
        # no policy / no daily limit is configured.
        if pol is not None and pol.daily_notional_limit_usd is not None:
            from cli.session_policy import PolicyCounters
            filled_notional = abs(float(fill.quantity) * float(fill.price))
            PolicyCounters().record(
                getattr(hl, "_address", None), network, current_workspace(), filled_notional,
            )
    else:
        typer.echo("No fill (order may have been rejected or not matched)")
