"""Pear-style BTC + BTCSWP pair trade planning.

The pure module builds a two-leg order plan. Execution, signing, builder fees,
and persistence live in `cli.commands.pair`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from strategies.pear_btcswp_quote import (
    BTC_PERP_INSTRUMENT,
    BTCSWP_INSTRUMENT,
    quote_pear_btcswp_hedge,
)

PairExecutionType = Literal["MARKET", "SYNC"]


@dataclass(frozen=True)
class PairLegPlan:
    role: str
    instrument: str
    side: str
    notional_usd: float
    size: float
    limit_price: float
    order_type: str
    target_weight: float

    def as_order(self) -> Dict[str, Any]:
        return {
            "action": "place_order",
            "instrument": self.instrument,
            "side": self.side,
            "size": self.size,
            "limit_price": self.limit_price,
            "order_type": self.order_type,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "target_weight": self.target_weight,
            **self.as_order(),
            "notional_usd": self.notional_usd,
        }


@dataclass(frozen=True)
class PearPairTradePlan:
    pair_position_id: str
    eligible: bool
    execution_type: PairExecutionType
    primary_leg: Optional[PairLegPlan]
    hedge_leg: Optional[PairLegPlan]
    usd_value: float
    slippage: float
    leverage: float
    long_assets: list[Dict[str, Any]]
    short_assets: list[Dict[str, Any]]
    builder: Optional[Dict[str, Any]]
    risk: Dict[str, Any]
    pear_requirements: Dict[str, Any]
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pair_position_id": self.pair_position_id,
            "eligible": self.eligible,
            "execution_type": self.execution_type,
            "usd_value": self.usd_value,
            "slippage": self.slippage,
            "leverage": self.leverage,
            "long_assets": self.long_assets,
            "short_assets": self.short_assets,
            "orders": [
                leg.as_dict()
                for leg in (self.primary_leg, self.hedge_leg)
                if leg is not None
            ],
            "builder": self.builder,
            "risk": self.risk,
            "pear_requirements": self.pear_requirements,
            "reason": self.reason,
        }


def build_btc_btcswp_pair_plan(
    *,
    primary_side: str,
    primary_notional_usd: float,
    btc_mid: float,
    btcswp_mid: float,
    hedge_goal: str = "auto",
    hedge_strength: float = 1.0,
    slippage: float = 0.01,
    leverage: float = 1.0,
    execution_type: PairExecutionType = "SYNC",
    builder: Optional[Dict[str, Any]] = None,
    now_ms: Optional[int] = None,
) -> PearPairTradePlan:
    now_ms = now_ms or int(time.time() * 1000)
    pair_position_id = f"PAIR-BTC-BTCSWP-{now_ms}"
    primary_side = primary_side.lower()
    primary_notional_usd = max(float(primary_notional_usd), 0.0)
    btc_mid = float(btc_mid)
    btcswp_mid = float(btcswp_mid)
    slippage = max(0.001, min(float(slippage), 0.1))

    pear_requirements = {
        "pear_docs": "https://docs.google.com/document/d/1eIswq_dB6TSK8hS6mxqwJZ_cJTJ8LaU_Z4OBYFqdxo8/edit",
        "campaign_decisions": [
            "Execute campaign trades through Pear backend so Pear synthetic positions, UI, PnL, history, and competition tracking stay correct.",
            "Use Pear builder attribution by default for Pear-native campaign trades.",
            "Use a dedicated wallet because Pear does not support subaccounts and mixed basket/perp trading can confuse position display.",
        ],
        "remaining_questions": [
            "Confirm the exact BTCSWP asset identifier Pear expects in longAssets / shortAssets.",
        ],
    }
    risk = {
        "partial_fill_policy": "If leg 1 fills and leg 2 fails, Agent CLI attempts an IOC reduce-only repair close of leg 1.",
        "execution_note": "Use venue=pear for Pear-native basket execution; direct Hyperliquid execution submits back-to-back Agent CLI-managed legs.",
    }

    if primary_side not in {"long", "short", "buy", "sell"}:
        return _ineligible(pair_position_id, execution_type, primary_notional_usd, slippage, leverage, builder, risk, pear_requirements, "primary_side must be long, short, buy, or sell")
    if primary_notional_usd <= 0:
        return _ineligible(pair_position_id, execution_type, primary_notional_usd, slippage, leverage, builder, risk, pear_requirements, "primary_notional_usd must be positive")
    if btc_mid <= 0 or btcswp_mid <= 0:
        return _ineligible(pair_position_id, execution_type, primary_notional_usd, slippage, leverage, builder, risk, pear_requirements, "btc_mid and btcswp_mid must be positive")

    primary_order_side = "buy" if primary_side in {"long", "buy"} else "sell"
    primary_limit = _limit_price(side=primary_order_side, mid=btc_mid, slippage=slippage)
    primary_leg = PairLegPlan(
        role="primary",
        instrument=BTC_PERP_INSTRUMENT,
        side=primary_order_side,
        notional_usd=round(primary_notional_usd, 2),
        size=round(primary_notional_usd / btc_mid, 8),
        limit_price=primary_limit,
        order_type="Ioc",
        target_weight=0.0,
    )

    hedge = quote_pear_btcswp_hedge(
        primary_side=primary_side,
        primary_notional_usd=primary_notional_usd,
        hedge_goal=hedge_goal,
        hedge_strength=hedge_strength,
        btcswp_mid=btcswp_mid,
    )
    if not hedge.eligible:
        return _ineligible(pair_position_id, execution_type, primary_notional_usd, slippage, leverage, builder, risk, pear_requirements, hedge.reason or "hedge quote failed")
    hedge_order = hedge.order
    hedge_leg = PairLegPlan(
        role="funding_hedge",
        instrument=BTCSWP_INSTRUMENT,
        side=hedge_order["side"],
        notional_usd=hedge.hedge_notional_usd,
        size=hedge_order["size"],
        limit_price=_limit_price(side=hedge_order["side"], mid=btcswp_mid, slippage=slippage),
        order_type="Ioc",
        target_weight=0.0,
    )

    total_notional = primary_leg.notional_usd + hedge_leg.notional_usd
    primary_leg = _with_weight(primary_leg, primary_leg.notional_usd / total_notional)
    hedge_leg = _with_weight(hedge_leg, hedge_leg.notional_usd / total_notional)

    long_assets, short_assets = _assets_from_legs(primary_leg, hedge_leg)
    return PearPairTradePlan(
        pair_position_id=pair_position_id,
        eligible=True,
        execution_type=execution_type,
        primary_leg=primary_leg,
        hedge_leg=hedge_leg,
        usd_value=round(total_notional, 2),
        slippage=slippage,
        leverage=leverage,
        long_assets=long_assets,
        short_assets=short_assets,
        builder=builder,
        risk=risk,
        pear_requirements=pear_requirements,
    )


def _limit_price(*, side: str, mid: float, slippage: float) -> float:
    return round(mid * (1.0 + slippage if side == "buy" else 1.0 - slippage), 8)


def _with_weight(leg: PairLegPlan, weight: float) -> PairLegPlan:
    return PairLegPlan(
        role=leg.role,
        instrument=leg.instrument,
        side=leg.side,
        notional_usd=leg.notional_usd,
        size=leg.size,
        limit_price=leg.limit_price,
        order_type=leg.order_type,
        target_weight=round(weight, 8),
    )


def _assets_from_legs(*legs: PairLegPlan) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    longs: list[Dict[str, Any]] = []
    shorts: list[Dict[str, Any]] = []
    for leg in legs:
        asset = {
            "asset": _pear_asset_symbol(leg.instrument),
            "weight": leg.target_weight,
            "role": leg.role,
            "notional_usd": leg.notional_usd,
        }
        if leg.side == "buy":
            longs.append(asset)
        else:
            shorts.append(asset)
    return longs, shorts


def _pear_asset_symbol(instrument: str) -> str:
    from cli.pear_config import PEAR_BTCSWP_ASSET

    if instrument == BTC_PERP_INSTRUMENT:
        return "BTC"
    if instrument == BTCSWP_INSTRUMENT:
        return PEAR_BTCSWP_ASSET
    return instrument.split("-", 1)[0]


def _ineligible(
    pair_position_id: str,
    execution_type: PairExecutionType,
    usd_value: float,
    slippage: float,
    leverage: float,
    builder: Optional[Dict[str, Any]],
    risk: Dict[str, Any],
    pear_requirements: Dict[str, Any],
    reason: str,
) -> PearPairTradePlan:
    return PearPairTradePlan(
        pair_position_id=pair_position_id,
        eligible=False,
        execution_type=execution_type,
        primary_leg=None,
        hedge_leg=None,
        usd_value=usd_value,
        slippage=slippage,
        leverage=leverage,
        long_assets=[],
        short_assets=[],
        builder=builder,
        risk=risk,
        pear_requirements=pear_requirements,
        reason=reason,
    )
