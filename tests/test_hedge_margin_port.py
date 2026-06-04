"""Port-verification tests for the CFI-v2 hedge verb + margin top-up.

Asserts the load-bearing math survives the port from nunchi-cli:
  - cfi_hedge sizing: hedge_notional = perp_notional / L
  - margin_math: utilization = used / value
  - margin_auto: top-up amount = (used / target) - value, with caps
  - CfiHedgeAgent: trigger + 1/L sizing + dedupe on the StrategyDecision surface
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly (`python tests/test_hedge_margin_port.py`) without
# pytest's rootdir insertion.
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.models import MarketSnapshot
from sdk.strategy_sdk.base import StrategyContext

from strategies.cfi_hedge import (
    BTCSWP_PROFILE,
    HLPositionSummary,
    build_cfi_hedge_proposal,
)
from strategies.cfi_hedge_agent import CfiHedgeAgent
from execution.margin_math import (
    free_collateral_usd,
    margin_utilization,
    maintenance_margin_ratio,
)
from execution.margin_auto import (
    AccountReading,
    DailyState,
    TopupPolicy,
    compute_topup_action,
    today_utc_iso,
)


def _btc_position(notional_usd: float) -> HLPositionSummary:
    return HLPositionSummary(
        coin="BTC",
        side="long",
        size_coin=notional_usd / 75_000.0,
        entry_px=75_000.0,
        mark_px=75_000.0,
        notional_usd=notional_usd,
        leverage=BTCSWP_PROFILE.vol_mult_l,
        unrealized_pnl_usd=0.0,
        cum_funding_usd=0.0,
    )


# ─── cfi_hedge sizing: hedge_notional = notional / L ─────────────────────────


def test_cfi_hedge_sizing_is_notional_over_L():
    notional = 150_000.0
    L = BTCSWP_PROFILE.vol_mult_l  # 15
    prop = build_cfi_hedge_proposal(
        user_address="t",
        position=_btc_position(notional),
        current_funding_hr=0.00002,
        k_fixed_hr=0.0000029,
    )
    assert prop is not None
    assert prop.hedge_notional_usd == notional / L  # 10_000.0
    assert prop.hedge_ratio == 1.0 / L
    # Total capital = perp + hedge; efficiency = perp / total.
    assert prop.total_capital_usd == notional + notional / L
    assert abs(prop.capital_efficiency - notional / (notional + notional / L)) < 1e-12
    # Hedge leg is the YEX CFI v2 market, same side as the perp.
    assert prop.legs[1].market == "yex:BTCSWP"
    assert prop.legs[1].side == "long"


def test_cfi_hedge_unknown_coin_returns_none():
    pos = HLPositionSummary(
        coin="DOGE", side="long", size_coin=1.0, entry_px=0.1, mark_px=0.1,
        notional_usd=1000.0, leverage=10, unrealized_pnl_usd=0.0, cum_funding_usd=0.0,
    )
    assert build_cfi_hedge_proposal(
        user_address="t", position=pos, current_funding_hr=0.0, k_fixed_hr=0.0,
    ) is None


# ─── margin_math: utilization = used / value ─────────────────────────────────


def test_margin_utilization_is_used_over_value():
    assert margin_utilization(7_000.0, 10_000.0) == 0.7
    assert margin_utilization(0.0, 10_000.0) == 0.0
    # Zero/negative account value is treated as 0 utilization (no divide-by-zero).
    assert margin_utilization(5_000.0, 0.0) == 0.0


def test_free_collateral_and_maint_ratio():
    assert free_collateral_usd(10_000.0, 7_000.0) == 3_000.0
    # Never negative.
    assert free_collateral_usd(5_000.0, 9_000.0) == 0.0
    assert maintenance_margin_ratio(2_000.0, 10_000.0) == 0.2


# ─── margin_auto: top-up = (used / target) - value, with caps ────────────────


def _policy(**over) -> TopupPolicy:
    base = dict(
        util_trigger=0.70,
        util_target=0.50,
        max_per_topup_usd=100_000.0,
        max_per_day_usd=1_000_000.0,
        min_interval_seconds=60,
        min_source_balance_usd=100.0,
    )
    base.update(over)
    return TopupPolicy(**base)


def test_topup_amount_brings_util_to_target():
    # util = 8000/10000 = 0.8 > 0.7 trigger.
    # required = (8000 / 0.5) - 10000 = 6000.
    reading = AccountReading(
        account_value_usd=10_000.0, total_margin_used_usd=8_000.0, spot_usdc_usd=50_000.0,
    )
    action, _ = compute_topup_action(
        reading=reading, policy=_policy(), daily=DailyState.fresh(today_utc_iso()), now_ms=1_000_000,
    )
    assert action is not None
    assert action.amount_usd == 6_000.0
    assert action.source == "spot"
    assert action.dest == "perp"


def test_topup_respects_per_action_cap():
    reading = AccountReading(
        account_value_usd=10_000.0, total_margin_used_usd=8_000.0, spot_usdc_usd=50_000.0,
    )
    action, reason = compute_topup_action(
        reading=reading,
        policy=_policy(max_per_topup_usd=1_000.0),
        daily=DailyState.fresh(today_utc_iso()),
        now_ms=1_000_000,
    )
    assert action.amount_usd == 1_000.0  # 6000 clipped to per-action cap
    assert "capped" in reason


def test_topup_skips_below_trigger():
    # util = 5000/10000 = 0.5 < 0.7 trigger -> no action.
    reading = AccountReading(
        account_value_usd=10_000.0, total_margin_used_usd=5_000.0, spot_usdc_usd=50_000.0,
    )
    action, reason = compute_topup_action(
        reading=reading, policy=_policy(), daily=DailyState.fresh(today_utc_iso()), now_ms=1_000_000,
    )
    assert action is None
    assert "skip" in reason


# ─── CfiHedgeAgent: trigger + 1/L sizing + dedupe ────────────────────────────


def _btc_snap() -> MarketSnapshot:
    return MarketSnapshot(instrument="BTC-PERP", mid_price=75_000.0, funding_rate=0.00002)


def test_agent_emits_cfi_leg_at_one_over_L():
    agent = CfiHedgeAgent()  # default notional_trigger=100k
    snap = _btc_snap()
    ctx = StrategyContext(snapshot=snap, position_qty=2.0, position_notional=150_000.0)
    decisions = agent.on_tick(snap, ctx)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.action == "place_order"
    assert d.instrument == "BTCSWP-USDYP"  # YEX CFI v2 instrument, not the perp
    assert d.side == "buy"
    assert d.order_type == "Ioc"
    # hedge notional = 150000 / 15 = 10000; size = 10000 / baseline_b0 (75000).
    assert d.meta["hedge_notional_usd"] == 10_000.0
    assert abs(d.size - 10_000.0 / BTCSWP_PROFILE.baseline_b0) < 1e-6


def test_agent_dedupes_and_respects_trigger():
    agent = CfiHedgeAgent()
    snap = _btc_snap()
    ctx = StrategyContext(snapshot=snap, position_qty=2.0, position_notional=150_000.0)
    assert len(agent.on_tick(snap, ctx)) == 1
    # Second tick: same coin already hedged this session -> no new decision.
    assert agent.on_tick(snap, ctx) == []
    # Fresh agent, notional below trigger -> no decision.
    below = CfiHedgeAgent()
    ctx_small = StrategyContext(snapshot=snap, position_qty=0.5, position_notional=37_500.0)
    assert below.on_tick(snap, ctx_small) == []


def test_agent_skips_non_cfi_coin():
    agent = CfiHedgeAgent()
    snap = MarketSnapshot(instrument="DOGE-PERP", mid_price=0.1, funding_rate=0.0001)
    ctx = StrategyContext(snapshot=snap, position_qty=1e6, position_notional=200_000.0)
    assert agent.on_tick(snap, ctx) == []


if __name__ == "__main__":
    # Plain-python fallback when pytest isn't available.
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
