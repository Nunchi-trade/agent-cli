"""Tests for SimpleMMStrategy."""
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


class TestSimpleMM:
    def test_produces_two_sided_orders(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy(size=1.0, spread_bps=10.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) == 2
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        assert len(buys) == 1
        assert len(sells) == 1

    def test_zero_mid_returns_empty(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_negative_mid_returns_empty(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy()
        orders = strat.on_tick(_snap(mid=-1.0, bid=-1.5, ask=-0.5), _ctx())
        assert orders == []

    def test_spread_calculation(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy(spread_bps=100.0, size=1.0)
        snap = _snap(mid=1000.0)
        orders = strat.on_tick(snap, _ctx())
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        # half_spread = 1000 * (100/10000) / 2 = 5.0
        assert buys[0].limit_price == pytest.approx(995.0)
        assert sells[0].limit_price == pytest.approx(1005.0)

    def test_order_size_matches_config(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy(size=2.5)
        orders = strat.on_tick(_snap(), _ctx())
        for o in orders:
            assert o.size == 2.5

    def test_instrument_passthrough(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy()
        snap = _snap()
        snap.instrument = "BTC-PERP"
        orders = strat.on_tick(snap, _ctx())
        for o in orders:
            assert o.instrument == "BTC-PERP"

    def test_symmetric_quotes_around_mid(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy(spread_bps=20.0, size=1.0)
        snap = _snap(mid=2000.0)
        orders = strat.on_tick(snap, _ctx())
        buy = [o for o in orders if o.side == "buy"][0]
        sell = [o for o in orders if o.side == "sell"][0]
        # Both should be equidistant from mid
        assert pytest.approx(2000.0 - buy.limit_price) == pytest.approx(sell.limit_price - 2000.0)

    def test_no_context_still_works(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy()
        orders = strat.on_tick(_snap())
        assert len(orders) == 2

    def test_action_is_place_order(self):
        from strategies.simple_mm import SimpleMMStrategy
        strat = SimpleMMStrategy()
        orders = strat.on_tick(_snap(), _ctx())
        for o in orders:
            assert o.action == "place_order"
