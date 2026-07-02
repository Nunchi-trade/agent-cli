"""Tests for RFQAgent strategy."""
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


class TestRFQAgent:
    def test_produces_two_sided_orders(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent(min_size=0.5, spread_bps=15.0, max_position=15.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) == 2
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        assert len(buys) == 1
        assert len(sells) == 1

    def test_zero_mid_returns_empty(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_at_max_position_returns_empty(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent(min_size=0.5, max_position=5.0)
        # Position at max -> remaining = 5 - 5 = 0 < min_size
        orders = strat.on_tick(_snap(), _ctx(pos_qty=5.0))
        assert orders == []

    def test_near_max_position_returns_empty(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent(min_size=0.5, max_position=5.0)
        # remaining = 5 - 4.8 = 0.2 < 0.5
        orders = strat.on_tick(_snap(), _ctx(pos_qty=4.8))
        assert orders == []

    def test_spread_calculation(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent(min_size=0.5, spread_bps=100.0, max_position=15.0)
        snap = _snap(mid=1000.0)
        orders = strat.on_tick(snap, _ctx())
        buy = [o for o in orders if o.side == "buy"][0]
        sell = [o for o in orders if o.side == "sell"][0]
        # half_spread = 1000 * (100/10000) / 2 = 5.0
        assert buy.limit_price == pytest.approx(995.0)
        assert sell.limit_price == pytest.approx(1005.0)

    def test_reduce_only_long_only_sells(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent(min_size=0.5, max_position=15.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=3.0, reduce_only=True))
        # reduce_only=True, q>0: buy skipped (not reduce_only or q<0 is False),
        # sell allowed (not reduce_only or q>0 is True)
        assert len(orders) == 1
        assert orders[0].side == "sell"

    def test_reduce_only_short_only_buys(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent(min_size=0.5, max_position=15.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=-3.0, reduce_only=True))
        assert len(orders) == 1
        assert orders[0].side == "buy"

    def test_meta_contains_capacity(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent(min_size=0.5, max_position=15.0)
        orders = strat.on_tick(_snap(), _ctx())
        for o in orders:
            assert "capacity" in o.meta
            assert o.meta["capacity"] == 15.0

    def test_meta_signal_names(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent()
        orders = strat.on_tick(_snap(), _ctx())
        signals = {o.meta["signal"] for o in orders}
        assert "rfq_bid" in signals
        assert "rfq_ask" in signals

    def test_size_capped_at_remaining(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent(min_size=2.0, max_position=5.0)
        # remaining = 5 - 4 = 1 < min_size=2 -> but size = min(min_size, remaining) = min(2,1)=1
        # Actually remaining=1 < min_size=2 -> returns empty
        orders = strat.on_tick(_snap(), _ctx(pos_qty=4.0))
        assert orders == []

    def test_no_context_still_works(self):
        from strategies.rfq_agent import RFQAgent
        strat = RFQAgent()
        orders = strat.on_tick(_snap())
        assert len(orders) == 2
