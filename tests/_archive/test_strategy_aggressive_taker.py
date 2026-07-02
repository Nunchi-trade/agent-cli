"""Tests for AggressiveTaker strategy."""
import os
import sys
import time
import math

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


class TestAggressiveTaker:
    def test_produces_two_orders(self):
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker(size=2.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) == 2
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        assert len(buys) == 1
        assert len(sells) == 1

    def test_zero_mid_returns_empty(self):
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_crosses_spread(self):
        """Buy limit should be above ask, sell limit below bid."""
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker(size=2.0)
        snap = _snap(bid=2499.5, ask=2500.5)
        orders = strat.on_tick(snap, _ctx())
        buy = [o for o in orders if o.side == "buy"][0]
        sell = [o for o in orders if o.side == "sell"][0]
        assert buy.limit_price > snap.ask  # crosses above ask
        assert sell.limit_price < snap.bid  # crosses below bid

    def test_sinusoidal_bias(self):
        """Sizes should alternate due to sinusoidal bias."""
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker(size=2.0, bias_amplitude=0.35, bias_period=4)
        sizes_buy = []
        sizes_sell = []
        for _ in range(4):
            orders = strat.on_tick(_snap(), _ctx())
            sizes_buy.append([o for o in orders if o.side == "buy"][0].size)
            sizes_sell.append([o for o in orders if o.side == "sell"][0].size)
        # Over a full cycle, buy and sell sizes should vary
        assert max(sizes_buy) > min(sizes_buy)
        assert max(sizes_sell) > min(sizes_sell)

    def test_skip_ticks(self):
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker(size=2.0, skip_ticks=2)
        # tick 1: _tick_count=1, 1 % 3 != 0 -> skip
        orders1 = strat.on_tick(_snap(), _ctx())
        assert orders1 == []
        # tick 2: _tick_count=2, 2 % 3 != 0 -> skip
        orders2 = strat.on_tick(_snap(), _ctx())
        assert orders2 == []
        # tick 3: _tick_count=3, 3 % 3 == 0 -> trade
        orders3 = strat.on_tick(_snap(), _ctx())
        assert len(orders3) == 2

    def test_order_type_is_ioc(self):
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker()
        orders = strat.on_tick(_snap(), _ctx())
        for o in orders:
            assert o.order_type == "Ioc"

    def test_meta_contains_bias(self):
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker()
        orders = strat.on_tick(_snap(), _ctx())
        for o in orders:
            assert "bias" in o.meta
            assert "signal" in o.meta

    def test_min_size_enforced(self):
        """Even with extreme bias, sizes should be at least 0.01."""
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker(size=0.02, bias_amplitude=0.49)
        for _ in range(20):
            orders = strat.on_tick(_snap(), _ctx())
            for o in orders:
                assert o.size >= 0.01

    def test_total_size_approximately_matches(self):
        """Buy + sell sizes should sum to approximately total size."""
        from strategies.aggressive_taker import AggressiveTaker
        strat = AggressiveTaker(size=2.0, bias_amplitude=0.35)
        orders = strat.on_tick(_snap(), _ctx())
        buy = [o for o in orders if o.side == "buy"][0]
        sell = [o for o in orders if o.side == "sell"][0]
        # buy_frac + sell_frac = 1.0, so sizes should sum to ~2.0
        assert buy.size + sell.size == pytest.approx(2.0, abs=0.01)
