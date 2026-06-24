"""hl pair — Pear-style BTC + BTCSWP pair execution."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import typer

pair_app = typer.Typer(
    name="pair",
    help="Pear-style pair positions — quote, execute, and close BTC + BTCSWP pairs.",
    no_args_is_help=True,
)


def _boot_cli() -> None:
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


def _positions_path() -> Path:
    p = Path.home() / ".nunchi"
    p.mkdir(parents=True, exist_ok=True)
    return p / "pair_positions.json"


def _load_positions() -> list[dict[str, Any]]:
    path = _positions_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_positions(positions: list[dict[str, Any]]) -> None:
    _positions_path().write_text(json.dumps(positions, indent=2, default=str))


def _builder_payload(
    builder_address: Optional[str],
    builder_fee_tenths_bps: Optional[int],
    *,
    venue: str = "pear",
) -> Optional[dict[str, Any]]:
    if builder_address is None and builder_fee_tenths_bps is None and venue == "pear":
        from cli.pear_config import pear_builder_info

        return pear_builder_info()
    if builder_address is None and builder_fee_tenths_bps is None:
        from cli.builder_fee import BuilderFeeConfig

        return BuilderFeeConfig.from_env().to_builder_info()
    if not builder_address or not builder_fee_tenths_bps or builder_fee_tenths_bps <= 0:
        return None
    return {"b": builder_address, "f": builder_fee_tenths_bps}


def _open_hl(mainnet: bool):
    from cli.config import TradingConfig
    from cli.hl_adapter import DirectHLProxy
    from parent.hl_proxy import HLProxy

    cfg = TradingConfig()
    private_key = cfg.get_private_key()
    raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
    return DirectHLProxy(raw_hl)


def _open_pear():
    from adapters.pear_adapter import BASE_URL, DEFAULT_CLIENT_ID, PearAuth, PearVenueAdapter, RequestsClient
    from cli.config import TradingConfig

    http = RequestsClient(base_url=os.getenv("PEAR_BASE_URL", BASE_URL))
    api_key = os.getenv("PEAR_API_KEY")
    address = os.getenv("PEAR_ADDRESS") or os.getenv("PEAR_WALLET_ADDRESS")
    if api_key:
        if not address:
            raise RuntimeError("PEAR_ADDRESS is required when PEAR_API_KEY is set")
        auth = PearAuth(
            http,
            address=address,
            client_id=os.getenv("PEAR_CLIENT_ID", DEFAULT_CLIENT_ID),
        )
        auth.bootstrap_with_api_key(api_key)
        return PearVenueAdapter(http=http, auth=auth)

    adapter = PearVenueAdapter(http=http)
    adapter.connect(TradingConfig().get_private_key())
    return adapter


def _price_inputs(hl, btc_mid: Optional[float], btcswp_mid: Optional[float]) -> tuple[float, float]:
    if btc_mid is None:
        btc_mid = hl.get_snapshot("BTC-PERP").mid_price
    if btcswp_mid is None:
        btcswp_mid = hl.get_snapshot("BTCSWP-USDYP").mid_price
    return float(btc_mid or 0.0), float(btcswp_mid or 0.0)


def _build_plan(
    *,
    primary_side: str,
    primary_notional_usd: float,
    btc_mid: float,
    btcswp_mid: float,
    hedge_goal: str,
    hedge_strength: float,
    slippage: float,
    leverage: float,
    builder: Optional[dict[str, Any]],
):
    from strategies.pear_pair_trade import build_btc_btcswp_pair_plan

    return build_btc_btcswp_pair_plan(
        primary_side=primary_side,
        primary_notional_usd=primary_notional_usd,
        btc_mid=btc_mid,
        btcswp_mid=btcswp_mid,
        hedge_goal=hedge_goal,
        hedge_strength=hedge_strength,
        slippage=slippage,
        leverage=leverage,
        builder=builder,
    )


@pair_app.command("quote")
def quote_cmd(
    primary_side: str = typer.Option(..., "--primary-side", help="BTC side: long/short/buy/sell"),
    primary_notional_usd: float = typer.Option(..., "--primary-notional-usd", help="BTC leg notional in USD"),
    btc_mid: float = typer.Option(..., "--btc-mid", help="BTC-PERP mid price"),
    btcswp_mid: float = typer.Option(..., "--btcswp-mid", help="BTCSWP-USDYP mid price"),
    hedge_goal: str = typer.Option("auto", "--hedge-goal", help="auto, funding_spike, or funding_compression"),
    hedge_strength: float = typer.Option(1.0, "--hedge-strength", help="0..1 multiplier on the 1/L hedge"),
    slippage: float = typer.Option(0.01, "--slippage", help="Slippage tolerance decimal, e.g. 0.01 = 1%"),
    leverage: float = typer.Option(1.0, "--leverage", help="Pair-level leverage metadata"),
    builder_address: Optional[str] = typer.Option(None, "--builder-address", help="Override builder address"),
    builder_fee_tenths_bps: Optional[int] = typer.Option(None, "--builder-fee-tenths-bps", help="Override builder fee"),
    venue: str = typer.Option("pear", "--venue", help="Execution venue for defaults: pear or direct"),
):
    """Quote a BTC + BTCSWP pair position without executing."""
    _boot_cli()
    venue = _normalize_venue(venue)
    builder = _builder_payload(builder_address, builder_fee_tenths_bps, venue=venue)
    plan = _build_plan(
        primary_side=primary_side,
        primary_notional_usd=primary_notional_usd,
        btc_mid=btc_mid,
        btcswp_mid=btcswp_mid,
        hedge_goal=hedge_goal,
        hedge_strength=hedge_strength,
        slippage=slippage,
        leverage=leverage,
        builder=builder,
    )
    typer.echo(json.dumps(plan.as_dict(), indent=2))


@pair_app.command("execute")
def execute_cmd(
    primary_side: str = typer.Option(..., "--primary-side", help="BTC side: long/short/buy/sell"),
    primary_notional_usd: float = typer.Option(..., "--primary-notional-usd", help="BTC leg notional in USD"),
    btc_mid: Optional[float] = typer.Option(None, "--btc-mid", help="Optional BTC-PERP mid; fetched live if omitted"),
    btcswp_mid: Optional[float] = typer.Option(None, "--btcswp-mid", help="Optional BTCSWP mid; fetched live if omitted"),
    hedge_goal: str = typer.Option("auto", "--hedge-goal", help="auto, funding_spike, or funding_compression"),
    hedge_strength: float = typer.Option(1.0, "--hedge-strength", help="0..1 multiplier on the 1/L hedge"),
    slippage: float = typer.Option(0.01, "--slippage", help="Slippage tolerance decimal, e.g. 0.01 = 1%"),
    leverage: float = typer.Option(1.0, "--leverage", help="Pair-level leverage metadata"),
    builder_address: Optional[str] = typer.Option(None, "--builder-address", help="Override builder address"),
    builder_fee_tenths_bps: Optional[int] = typer.Option(None, "--builder-fee-tenths-bps", help="Override builder fee"),
    venue: str = typer.Option("pear", "--venue", help="Execution venue: pear or direct"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only; do not sign, submit, or persist"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirm"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet"),
    policy: Optional[Path] = typer.Option(None, "--policy", help="Session policy file or inline JSON"),
):
    """Execute both legs of a BTC + BTCSWP pair position."""
    _boot_cli()
    from cli.session_policy import ACTION_TRADE, current_workspace, guard_or_exit
    from cli.view_mode import require_not_view_only

    require_not_view_only()
    venue = _normalize_venue(venue)
    builder = _builder_payload(builder_address, builder_fee_tenths_bps, venue=venue)
    network = "mainnet" if mainnet else "testnet"
    policy_path = str(policy) if policy else None
    guard_or_exit(ACTION_TRADE, policy_path=policy_path, network=network, market="BTC-PERP")
    guard_or_exit(ACTION_TRADE, policy_path=policy_path, network=network, market="BTCSWP-USDYP")

    has_price_inputs = btc_mid is not None and btcswp_mid is not None
    hl = None if ((dry_run or venue == "pear") and has_price_inputs) else _open_hl(mainnet)
    live_btc_mid, live_btcswp_mid = _price_inputs(hl, btc_mid, btcswp_mid) if hl else (float(btc_mid), float(btcswp_mid))
    plan = _build_plan(
        primary_side=primary_side,
        primary_notional_usd=primary_notional_usd,
        btc_mid=live_btc_mid,
        btcswp_mid=live_btcswp_mid,
        hedge_goal=hedge_goal,
        hedge_strength=hedge_strength,
        slippage=slippage,
        leverage=leverage,
        builder=builder,
    )
    payload = plan.as_dict()
    typer.echo(json.dumps(payload, indent=2))
    if not plan.eligible or plan.primary_leg is None or plan.hedge_leg is None:
        raise typer.Exit(2)
    if dry_run:
        typer.echo("DRY-RUN: no orders submitted and no pair position persisted.")
        return
    if venue == "pear":
        typer.echo(
            "Pear-native campaign route: orders execute through Pear backend so "
            "synthetic positions, PnL, history, and competition tracking stay in Pear."
        )
        typer.echo(
            "Pear recommends a dedicated wallet because subaccounts are not supported; "
            "avoid mixing Pear basket trades and normal perps in the same wallet."
        )
    if not yes and not typer.confirm(_confirm_prompt(venue)):
        raise typer.Exit(0)

    if venue == "pear":
        pear = _open_pear()
        total_notional = float(payload["usd_value"])
        guard_or_exit(
            ACTION_TRADE,
            policy_path=policy_path,
            network=network,
            market="PEAR:BTC-BTCSWP",
            notional_usd=total_notional,
        )
        response = pear.create_position(
            long_assets=payload["long_assets"],
            short_assets=payload["short_assets"],
            usd_value=total_notional,
            execution_type="MARKET",
            leverage=max(1, int(round(float(payload["leverage"])))),
            slippage=float(payload["slippage"]),
            builder=builder,
        )
        record = {
            "pair_position_id": plan.pair_position_id,
            "pear_position_id": _pear_position_id(response),
            "source": "agent_cli_pair_execute",
            "venue": "pear",
            "status": "active",
            "network": network,
            "created_at_ms": int(time.time() * 1000),
            "quote": payload,
            "pear_response": response,
            "builder": builder,
            "fills": [],
        }
        positions = _load_positions()
        positions.insert(0, record)
        _save_positions(positions)
        typer.echo(f"Persisted {plan.pair_position_id} -> {_positions_path()}")
        return

    if hl is None:
        hl = _open_hl(mainnet)

    total_notional = sum(order["notional_usd"] for order in payload["orders"])
    pol = guard_or_exit(
        ACTION_TRADE,
        policy_path=policy_path,
        wallet=getattr(hl, "_address", None),
        network=network,
        market="PAIR:BTC-BTCSWP",
        notional_usd=total_notional,
    )

    fills: list[dict[str, Any]] = []
    primary_fill = _submit_leg(hl, plan.primary_leg.as_dict(), builder=builder)
    if primary_fill is None:
        typer.echo("Primary BTC leg did not fill; hedge leg not submitted.", err=True)
        raise typer.Exit(1)
    fills.append(_fill_dict(primary_fill, role="primary"))

    hedge_fill = _submit_leg(hl, plan.hedge_leg.as_dict(), builder=builder)
    if hedge_fill is None:
        repair = _repair_close(hl, plan.primary_leg.as_dict(), primary_fill, builder=builder)
        typer.echo("BTCSWP hedge leg failed; attempted repair-close of primary BTC leg.", err=True)
        if repair is not None:
            fills.append(_fill_dict(repair, role="repair_close"))
        raise typer.Exit(1)
    fills.append(_fill_dict(hedge_fill, role="funding_hedge"))

    if pol is not None and pol.daily_notional_limit_usd is not None:
        from cli.session_policy import PolicyCounters

        PolicyCounters().record(getattr(hl, "_address", None), network, current_workspace(), total_notional)

    record = {
        "pair_position_id": plan.pair_position_id,
        "source": "agent_cli_pair_execute",
        "venue": "direct",
        "status": "active",
        "network": network,
        "created_at_ms": int(time.time() * 1000),
        "quote": payload,
        "fills": fills,
    }
    positions = _load_positions()
    positions.insert(0, record)
    _save_positions(positions)
    typer.echo(f"Persisted {plan.pair_position_id} -> {_positions_path()}")


@pair_app.command("close")
def close_cmd(
    pair_position_id: Optional[str] = typer.Argument(None, help="Pair position id; defaults to latest active"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirm"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet"),
):
    """Close both legs of a persisted pair position."""
    _boot_cli()
    from cli.view_mode import require_not_view_only

    require_not_view_only()
    positions = _load_positions()
    record = _find_position(positions, pair_position_id)
    if record is None:
        typer.echo("No matching active pair position found.", err=True)
        raise typer.Exit(1)
    if record.get("venue") == "pear":
        payload = {
            "pair_position_id": record["pair_position_id"],
            "venue": "pear",
            "pear_position_id": record.get("pear_position_id"),
            "action": "close_position",
        }
        typer.echo(json.dumps(payload, indent=2))
        if dry_run:
            typer.echo("DRY-RUN: no close orders submitted.")
            return
        if not yes and not typer.confirm("Close Pear position?"):
            raise typer.Exit(0)
        pear_position_id = record.get("pear_position_id")
        if not pear_position_id:
            typer.echo("Pear position id missing from persisted record.", err=True)
            raise typer.Exit(1)
        response = _open_pear().close_position(str(pear_position_id), execution_type="MARKET")
        record["status"] = "closed"
        record["closed_at_ms"] = int(time.time() * 1000)
        record["pear_close_response"] = response
        _save_positions(positions)
        typer.echo(f"Closed Pear position {pear_position_id}.")
        return
    close_orders = [_close_order_from_fill(fill) for fill in record.get("fills", []) if fill.get("role") in {"primary", "funding_hedge"}]
    typer.echo(json.dumps({"pair_position_id": record["pair_position_id"], "close_orders": close_orders}, indent=2))
    if dry_run:
        typer.echo("DRY-RUN: no close orders submitted.")
        return
    if not yes and not typer.confirm("Sign + close both pair legs?"):
        raise typer.Exit(0)
    hl = _open_hl(mainnet)
    close_fills = []
    for order in close_orders:
        fill = _submit_leg(hl, order, builder=None, reduce_only=True)
        if fill is not None:
            close_fills.append(_fill_dict(fill, role="close"))
    record["status"] = "closed"
    record["closed_at_ms"] = int(time.time() * 1000)
    record["close_fills"] = close_fills
    _save_positions(positions)
    typer.echo(f"Closed {record['pair_position_id']} with {len(close_fills)} fills.")


def _submit_leg(hl, order: dict[str, Any], *, builder: Optional[dict[str, Any]], reduce_only: bool = False):
    return hl.place_order(
        instrument=order["instrument"],
        side=order["side"],
        size=order["size"],
        price=order["limit_price"],
        tif=order.get("order_type", "Ioc"),
        builder=builder,
        reduce_only=reduce_only,
    )


def _normalize_venue(venue: str) -> str:
    venue = venue.lower()
    if venue not in {"direct", "pear"}:
        raise typer.BadParameter("venue must be direct or pear")
    return venue


def _confirm_prompt(venue: str) -> str:
    if venue == "pear":
        return "Sign + submit via Pear backend using a dedicated wallet?"
    return "Sign + submit direct Hyperliquid legs? This is not Pear campaign-tracked."


def _repair_close(hl, order: dict[str, Any], fill, *, builder: Optional[dict[str, Any]]):
    close_side = "sell" if str(fill.side).lower() == "buy" else "buy"
    return hl.place_order(
        instrument=order["instrument"],
        side=close_side,
        size=float(fill.quantity),
        price=order["limit_price"],
        tif="Ioc",
        builder=builder,
        reduce_only=True,
    )


def _fill_dict(fill, *, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "oid": str(fill.oid),
        "instrument": fill.instrument,
        "side": fill.side,
        "price": str(fill.price),
        "quantity": str(fill.quantity),
        "timestamp_ms": fill.timestamp_ms,
        "fee": str(getattr(fill, "fee", "0")),
    }


def _pear_position_id(response: dict[str, Any]) -> Optional[str]:
    for key in ("positionId", "position_id", "id"):
        value = response.get(key)
        if value:
            return str(value)
    position = response.get("position")
    if isinstance(position, dict):
        for key in ("positionId", "position_id", "id"):
            value = position.get(key)
            if value:
                return str(value)
    return None


def _find_position(positions: list[dict[str, Any]], pair_position_id: Optional[str]) -> Optional[dict[str, Any]]:
    for pos in positions:
        if pos.get("status") != "active":
            continue
        if pair_position_id is None or pos.get("pair_position_id") == pair_position_id:
            return pos
    return None


def _close_order_from_fill(fill: dict[str, Any]) -> dict[str, Any]:
    side = "sell" if fill["side"] == "buy" else "buy"
    return {
        "instrument": fill["instrument"],
        "side": side,
        "size": float(fill["quantity"]),
        "limit_price": float(fill["price"]),
        "order_type": "Ioc",
    }
