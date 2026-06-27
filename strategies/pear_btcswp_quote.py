"""Pear-facing BTCSWP hedge quote helpers.

This is pure Agent CLI logic: no HTTP server, no signing, no Pear-side quote
dependency. The CLI/MCP surfaces can call this to return an executable hedge
order payload for Pear's BTC trade flow.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from strategies.cfi_hedge import BTCSWP_PROFILE

BTC_PERP_INSTRUMENT = "BTC-PERP"
BTCSWP_INSTRUMENT = BTCSWP_PROFILE.cfi_instrument
BTCSWP_HL_COIN = BTCSWP_PROFILE.cfi_asset_name
HedgeGoal = Literal["auto", "funding_spike", "funding_compression"]


@dataclass(frozen=True)
class PearBTCSWPQuote:
    eligible: bool
    hedge_instrument: str
    hedge_side: Optional[str]
    hedge_notional_usd: float
    hedge_size: float
    primary_notional_usd: float
    hedge_goal: HedgeGoal
    oracle: Dict[str, Any]
    risk: Dict[str, Any]
    order: Dict[str, Any]
    execution: Dict[str, Any]
    explanation: str
    pear_requirements: Dict[str, Any]
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "eligible": self.eligible,
            "hedge_instrument": self.hedge_instrument,
            "hedge_side": self.hedge_side,
            "hedge_notional_usd": self.hedge_notional_usd,
            "hedge_size": self.hedge_size,
            "primary_notional_usd": self.primary_notional_usd,
            "hedge_goal": self.hedge_goal,
            "oracle": self.oracle,
            "risk": self.risk,
            "order": self.order,
            "execution": self.execution,
            "explanation": self.explanation,
            "pear_requirements": self.pear_requirements,
            "reason": self.reason,
        }


def pear_required_fields() -> Dict[str, Any]:
    return {
        "pear_docs": "https://docs.google.com/document/d/1eIswq_dB6TSK8hS6mxqwJZ_cJTJ8LaU_Z4OBYFqdxo8/edit",
        "needed_from_pear": [
            "Choose quote-only, deep-link execution, or agent-managed execution launch path.",
            "Confirm where the BTCSWP hedge appears in Pear's BTC trade UX.",
            "Coordinate staging/testnet wallets and any sandbox account allowlisting.",
            "Confirm final public market label for BTCSWP in Pear UI.",
        ],
    }


def market_metadata() -> Dict[str, Any]:
    return {
        "instrument": BTCSWP_INSTRUMENT,
        "hl_coin": BTCSWP_HL_COIN,
        "public_label": "BTC Funding Rate Perp",
        "underlying": "BTC funding rate",
        "quote_asset": "USDYP",
        "vol_mult_l": BTCSWP_PROFILE.vol_mult_l,
        "hedge_ratio": 1.0 / BTCSWP_PROFILE.vol_mult_l,
        "baseline_b0": BTCSWP_PROFILE.baseline_b0,
        "supported_primary_instruments": [BTC_PERP_INSTRUMENT],
        "supported_hedge_goals": ["funding_spike", "funding_compression"],
        "risk_disclosure": risk_disclosure(),
        **pear_required_fields(),
    }


def risk_disclosure() -> str:
    return (
        "BTCSWP hedges can reduce BTC funding-rate exposure, but they can still "
        "lose money due to basis risk, liquidity risk, oracle risk, execution "
        "risk, and mismatch between the user's BTC trade and hedge sizing."
    )


def quote_pear_btcswp_hedge(
    *,
    primary_instrument: str = BTC_PERP_INSTRUMENT,
    primary_side: str,
    primary_notional_usd: float,
    hedge_goal: HedgeGoal = "auto",
    hedge_strength: float = 1.0,
    btcswp_mid: Optional[float] = None,
    current_funding_hr: Optional[float] = None,
    k_fixed_hr: Optional[float] = None,
    max_hedge_notional_usd: Optional[float] = None,
    max_slippage_bps: float = 20.0,
) -> PearBTCSWPQuote:
    primary_instrument = primary_instrument.upper()
    primary_side = primary_side.lower()
    hedge_goal = _normalize_goal(hedge_goal)
    primary_notional_usd = max(float(primary_notional_usd), 0.0)
    hedge_strength = _clip(float(hedge_strength), 0.0, 1.0)
    mid = float(btcswp_mid or BTCSWP_PROFILE.baseline_b0)

    oracle = {
        "timestamp_ms": int(time.time() * 1000),
        "current_funding_hr": current_funding_hr,
        "k_fixed_hr": k_fixed_hr if k_fixed_hr is not None else BTCSWP_PROFILE.fixed_leg_initial,
        "source": "agent-cli",
    }
    risk = {
        "max_slippage_bps": max_slippage_bps,
        "warnings": [risk_disclosure()],
    }

    if primary_instrument != BTC_PERP_INSTRUMENT:
        return _ineligible("unsupported primary instrument", hedge_goal, oracle, risk)
    if primary_side not in {"long", "short", "buy", "sell"}:
        return _ineligible("primary_side must be long, short, buy, or sell", hedge_goal, oracle, risk)
    if primary_notional_usd <= 0:
        return _ineligible("primary_notional_usd must be positive", hedge_goal, oracle, risk)
    if mid <= 0:
        return _ineligible("btcswp_mid must be positive", hedge_goal, oracle, risk)

    side = _hedge_side(primary_side=primary_side, hedge_goal=hedge_goal)
    hedge_notional = primary_notional_usd * (1.0 / BTCSWP_PROFILE.vol_mult_l) * hedge_strength
    if max_hedge_notional_usd is not None:
        hedge_notional = min(hedge_notional, max(float(max_hedge_notional_usd), 0.0))
    size = hedge_notional / mid
    limit_price = _limit_price(side=side, mid=mid, slippage_bps=max_slippage_bps)
    explanation = (
        "Buy BTCSWP to hedge BTC funding-rate spikes."
        if side == "buy"
        else "Sell BTCSWP to hedge BTC funding-rate compression or short-side funding exposure."
    )

    return PearBTCSWPQuote(
        eligible=True,
        hedge_instrument=BTCSWP_INSTRUMENT,
        hedge_side=side,
        hedge_notional_usd=round(hedge_notional, 2),
        hedge_size=round(size, 8),
        primary_notional_usd=round(primary_notional_usd, 2),
        hedge_goal=hedge_goal,
        oracle=oracle,
        risk=risk,
        order={
            "action": "place_order",
            "instrument": BTCSWP_INSTRUMENT,
            "side": side,
            "size": round(size, 8),
            "limit_price": limit_price,
            "order_type": "Ioc",
        },
        execution={
            "strategy": "pear_btcswp_hedge",
            "cli": "hl run pear_btcswp_hedge -i BTC-PERP --tick 10",
            "deep_link": "agent-cli://run?strategy=pear_btcswp_hedge&instrument=BTC-PERP",
        },
        explanation=explanation,
        pear_requirements=pear_required_fields(),
    )


def _hedge_side(*, primary_side: str, hedge_goal: HedgeGoal) -> str:
    if hedge_goal == "funding_spike":
        return "buy"
    if hedge_goal == "funding_compression":
        return "sell"
    return "buy" if primary_side in {"long", "buy"} else "sell"


def _normalize_goal(goal: str) -> HedgeGoal:
    if goal in {"auto", "funding_spike", "funding_compression"}:
        return goal  # type: ignore[return-value]
    return "auto"


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _limit_price(*, side: str, mid: float, slippage_bps: float) -> float:
    slip = slippage_bps / 10_000.0
    return round(mid * (1.0 + slip if side == "buy" else 1.0 - slip), 8)


def _ineligible(reason: str, hedge_goal: HedgeGoal, oracle: Dict[str, Any], risk: Dict[str, Any]) -> PearBTCSWPQuote:
    return PearBTCSWPQuote(
        eligible=False,
        hedge_instrument=BTCSWP_INSTRUMENT,
        hedge_side=None,
        hedge_notional_usd=0.0,
        hedge_size=0.0,
        primary_notional_usd=0.0,
        hedge_goal=hedge_goal,
        oracle=oracle,
        risk=risk,
        order={},
        execution={},
        explanation="No BTCSWP hedge quote produced.",
        pear_requirements=pear_required_fields(),
        reason=reason,
    )
