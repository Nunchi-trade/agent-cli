"""Tests for TrendFollowerStrategy."""
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


class TestTrendFollower:
    def test_warmup_returns_empty(self):
        from strategies.trend_follower import TrendFollowerStrategy, MIN_HISTORY
        strat = TrendFollowerStrategy(size=1.0)
        # Feed less than MIN_HISTORY ticks
        for i in range(MIN_HISTORY - 2):
            orders = strat.on_tick(_snap(mid=2500.0 + i * 0.01), _ctx())
        assert orders == []

    def test_zero_mid_returns_empty(self):
        from strategies.trend_follower import TrendFollowerStrategy
        strat = TrendFollowerStrategy()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_stable_prices_no_entry(self):
        """Flat prices should not trigger crossover entry."""
        from strategies.trend_follower import TrendFollowerStrategy, MIN_HISTORY
        strat = TrendFollowerStrategy(size=1.0)
        for i in range(MIN_HISTORY + 5):
            orders = strat.on_tick(_snap(mid=2500.0), _ctx())
        # Stable price -> no EMA crossover -> no orders
        assert orders == []

    def test_entry_on_uptrend(self):
        """Strong uptrend should eventually produce a long entry."""
        from strategies.trend_follower import TrendFollowerStrategy, MIN_HISTORY
        strat = TrendFollowerStrategy(size=1.0)
        # Start with downtrend to establish fast < slow
        for i in range(MIN_HISTORY):
            mid = 2600.0 - i * 2.0  # declining
            strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5), _ctx())
        # Then sharp uptrend to cause crossover
        found_long = False
        for i in range(50):
            mid = 2500.0 + i * 5.0
            orders = strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5), _ctx())
            if orders and any(o.meta.get("signal") == "trend_long" for o in orders):
                found_long = True
                break
        assert found_long, "Should enter long on strong uptrend with EMA crossover"

    def test_meta_contains_adx_and_ema(self):
        """Orders should contain adx, ema_fast, ema_slow in meta."""
        from strategies.trend_follower import TrendFollowerStrategy, MIN_HISTORY
        strat = TrendFollowerStrategy(size=1.0)
        # Build declining then rising to trigger entry
        for i in range(MIN_HISTORY):
            mid = 2600.0 - i * 2.0
            strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5), _ctx())
        for i in range(50):
            mid = 2500.0 + i * 5.0
            orders = strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5), _ctx())
            if orders:
                assert "adx" in orders[0].meta
                assert "ema_fast" in orders[0].meta
                assert "ema_slow" in orders[0].meta
                break

    def test_exit_on_opposing_cross(self):
        """If direction is long and crossover_down fires, should exit."""
        from strategies.trend_follower import TrendFollowerStrategy, MIN_HISTORY
        strat = TrendFollowerStrategy(size=1.0)
        # Force direction=1 and prev_ema_cross=1
        # Then feed declining prices to trigger crossover_down
        for i in range(MIN_HISTORY):
            mid = 2400.0 + i * 2.0
            strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5), _ctx())

        # Manually set state as if we entered long
        strat.direction = 1
        strat.prev_ema_cross = 1
        strat.entry_price = 2500.0
        strat.peak_price = 2500.0
        strat.atr_at_entry = 5.0

        # Feed sharply declining to cause fast EMA to cross below slow
        found_exit = False
        for i in range(80):
            mid = 2500.0 - i * 5.0
            orders = strat.on_tick(
                _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5),
                _ctx(pos_qty=1.0),
            )
            if orders and any(o.meta.get("signal") in ("ema_cross_exit", "adx_weak", "atr_trailing_stop") for o in orders):
                found_exit = True
                break
        assert found_exit, "Should exit on opposing EMA cross or trailing stop"

    def test_order_type_is_ioc(self):
        from strategies.trend_follower import TrendFollowerStrategy, MIN_HISTORY
        strat = TrendFollowerStrategy(size=1.0)
        for i in range(MIN_HISTORY):
            mid = 2600.0 - i * 2.0
            strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5), _ctx())
        for i in range(50):
            mid = 2500.0 + i * 5.0
            orders = strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5), _ctx())
            if orders:
                assert orders[0].order_type == "Ioc"
                break
