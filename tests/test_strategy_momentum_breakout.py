"""Tests for MomentumBreakoutStrategy."""
import time

import pytest

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import StrategyContext


def _snap(mid=2500.0, bid=2499.5, ask=2500.5, volume_24h=1e6, open_interest=1e5):
    return MarketSnapshot(
        instrument="ETH-PERP", mid_price=mid, bid=bid, ask=ask,
        spread_bps=4.0, funding_rate=0.0001,
        volume_24h=volume_24h, open_interest=open_interest,
        timestamp_ms=int(time.time() * 1000),
    )


def _ctx(pos_qty=0.0):
    return StrategyContext(position_qty=pos_qty, round_number=1)


class TestMomentumBreakout:
    def test_warmup_returns_empty(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(lookback=20, size=1.0)
        for _ in range(10):
            orders = strat.on_tick(_snap(), _ctx())
        assert orders == []

    def test_no_breakout_stable_price(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(lookback=5, breakout_threshold_bps=50.0, size=1.0)
        # Fill with stable prices
        for _ in range(6):
            orders = strat.on_tick(_snap(mid=2500.0), _ctx())
        assert orders == []

    def test_breakout_long_on_surge(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, breakout_threshold_bps=50.0, volume_surge_mult=2.0, size=1.0
        )
        # Fill lookback at 2500
        for _ in range(5):
            strat.on_tick(_snap(mid=2500.0, bid=2499.5, ask=2500.5, volume_24h=1e6), _ctx())
        # Breakout upward: 2530 = +120 bps from 2500.5 high, with volume surge
        orders = strat.on_tick(
            _snap(mid=2530.0, bid=2529.5, ask=2530.5, volume_24h=3e6), _ctx()
        )
        assert len(orders) == 1
        assert orders[0].side == "buy"
        assert orders[0].meta["signal"] == "breakout_long"

    def test_breakout_short_on_surge(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, breakout_threshold_bps=50.0, volume_surge_mult=2.0, size=1.0
        )
        # Fill lookback at 2500
        for _ in range(5):
            strat.on_tick(_snap(mid=2500.0, bid=2499.5, ask=2500.5, volume_24h=1e6), _ctx())
        # Breakout downward: 2470 = -120 bps from 2499.5 low, with volume surge
        orders = strat.on_tick(
            _snap(mid=2470.0, bid=2469.5, ask=2470.5, volume_24h=3e6), _ctx()
        )
        assert len(orders) == 1
        assert orders[0].side == "sell"
        assert orders[0].meta["signal"] == "breakout_short"

    def test_no_breakout_without_volume_surge(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, breakout_threshold_bps=50.0, volume_surge_mult=2.0, size=1.0
        )
        for _ in range(5):
            strat.on_tick(_snap(mid=2500.0, volume_24h=1e6), _ctx())
        # Price breakout but no volume surge
        orders = strat.on_tick(
            _snap(mid=2530.0, bid=2529.5, ask=2530.5, volume_24h=1.5e6), _ctx()
        )
        assert orders == []

    def test_trailing_stop_long_exit(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, trailing_stop_bps=30.0, size=1.0
        )
        for _ in range(5):
            strat.on_tick(_snap(mid=2500.0, volume_24h=1e6), _ctx())
        # With a long position, bid at stop price → exit
        # stop_price = mid * (1 - 30/10000) = 2500 * 0.997 = 2492.5
        # bid must be <= stop_price
        orders = strat.on_tick(
            _snap(mid=2500.0, bid=2492.0, ask=2500.5, volume_24h=1e6),
            _ctx(pos_qty=1.0),
        )
        assert len(orders) == 1
        assert orders[0].side == "sell"
        assert orders[0].meta["signal"] == "trailing_stop_long"

    def test_trailing_stop_short_exit(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, trailing_stop_bps=30.0, size=1.0
        )
        for _ in range(5):
            strat.on_tick(_snap(mid=2500.0, volume_24h=1e6), _ctx())
        # With a short position, ask at stop price → exit
        # stop_price = mid * (1 + 30/10000) = 2500 * 1.003 = 2507.5
        # ask must be >= stop_price
        orders = strat.on_tick(
            _snap(mid=2500.0, bid=2499.5, ask=2508.0, volume_24h=1e6),
            _ctx(pos_qty=-1.0),
        )
        assert len(orders) == 1
        assert orders[0].side == "buy"
        assert orders[0].meta["signal"] == "trailing_stop_short"

    def test_no_entry_with_existing_position(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, breakout_threshold_bps=50.0, volume_surge_mult=2.0, size=1.0
        )
        for _ in range(5):
            strat.on_tick(_snap(mid=2500.0, volume_24h=1e6), _ctx())
        # Breakout + volume surge, but we already have a position → trailing stop logic only
        orders = strat.on_tick(
            _snap(mid=2530.0, bid=2529.5, ask=2530.5, volume_24h=3e6),
            _ctx(pos_qty=1.0),
        )
        # Should NOT produce a new entry — trailing stop hasn't triggered
        # (stop = 2530 * 0.997 = 2524.1, bid = 2529.5 > stop → no exit)
        assert orders == []

    def test_zero_mid_returns_empty(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy()
        orders = strat.on_tick(_snap(mid=0), _ctx())
        assert orders == []
