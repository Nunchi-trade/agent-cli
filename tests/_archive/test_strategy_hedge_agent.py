"""Tests for HedgeAgent strategy."""
import os
import sys
import time

import pytest

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import StrategyContext


def _snap(mid=2500.0, bid=2499.5, ask=2500.5, spread_bps=4.0,
          funding_rate=0.0001, volume_24h=1e6, open_interest=1e5):
    return MarketSnapshot(
        instrument="ETH-PERP", mid_price=mid, bid=bid, ask=ask,
        spread_bps=spread_bps, funding_rate=funding_rate,
        volume_24h=volume_24h, open_interest=open_interest,
        timestamp_ms=int(time.time() * 1000),
    )


def _ctx(pos_qty=0.0, upnl=0.0, rpnl=0.0, reduce_only=False, safe_mode=False, round_num=1):
    return StrategyContext(
        position_qty=pos_qty, unrealized_pnl=upnl, realized_pnl=rpnl,
        reduce_only=reduce_only, safe_mode=safe_mode, round_number=round_num,
    )


class TestHedgeAgent:
    def test_zero_mid_returns_empty(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_below_threshold_returns_empty(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=3.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=2.0))
        assert orders == []

    def test_at_threshold_returns_empty(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=3.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=3.0))
        assert orders == []

    def test_long_above_threshold_sells(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=3.0, urgency_factor=0.5)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=5.0))
        assert len(orders) == 1
        assert orders[0].side == "sell"
        assert orders[0].meta["signal"] == "hedge_sell"

    def test_short_above_threshold_buys(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=3.0, urgency_factor=0.5)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=-5.0))
        assert len(orders) == 1
        assert orders[0].side == "buy"
        assert orders[0].meta["signal"] == "hedge_buy"

    def test_hedge_size_calculation(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=3.0, urgency_factor=0.5, max_hedge_size=5.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=7.0))
        # excess = |7| - 3 = 4, hedge_size = min(4 * 0.5, 5.0) = 2.0
        assert orders[0].size == 2.0

    def test_max_hedge_size_cap(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=1.0, urgency_factor=1.0, max_hedge_size=2.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=100.0))
        # excess = 99, hedge_size = min(99 * 1.0, 2.0) = 2.0
        assert orders[0].size == 2.0

    def test_slippage_applied(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=1.0, slippage_bps=10.0)
        # Long -> sell with price below mid
        orders = strat.on_tick(_snap(mid=2500.0), _ctx(pos_qty=5.0))
        slip = 2500.0 * (10.0 / 10_000)
        expected_price = round(2500.0 - slip, 2)
        assert orders[0].limit_price == expected_price

    def test_slippage_applied_short(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=1.0, slippage_bps=10.0)
        # Short -> buy with price above mid
        orders = strat.on_tick(_snap(mid=2500.0), _ctx(pos_qty=-5.0))
        slip = 2500.0 * (10.0 / 10_000)
        expected_price = round(2500.0 + slip, 2)
        assert orders[0].limit_price == expected_price

    def test_meta_fields_present(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=1.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=5.0))
        meta = orders[0].meta
        assert "signal" in meta
        assert "inventory" in meta
        assert "excess" in meta
        assert "urgency" in meta

    def test_no_context_returns_empty(self):
        """Without context, position_qty defaults to 0 -> below threshold."""
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=3.0)
        orders = strat.on_tick(_snap())
        assert orders == []

    def test_order_type_is_ioc(self):
        from strategies.hedge_agent import HedgeAgent
        strat = HedgeAgent(inventory_threshold=1.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=5.0))
        assert orders[0].order_type == "Ioc"
