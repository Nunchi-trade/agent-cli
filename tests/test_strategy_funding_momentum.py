"""Tests for FundingMomentumStrategy."""
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


class TestFundingMomentum:
    def test_warmup_returns_empty(self):
        from strategies.funding_momentum import FundingMomentumStrategy, MIN_HISTORY
        strat = FundingMomentumStrategy(size=1.0)
        for i in range(MIN_HISTORY - 2):
            orders = strat.on_tick(_snap(mid=2500.0 + i * 0.01), _ctx())
        assert orders == []

    def test_zero_mid_returns_empty(self):
        from strategies.funding_momentum import FundingMomentumStrategy
        strat = FundingMomentumStrategy()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_stable_funding_no_entry(self):
        """Constant funding -> z-score = 0 -> no entry."""
        from strategies.funding_momentum import FundingMomentumStrategy, MIN_HISTORY, FUNDING_LOOKBACK
        strat = FundingMomentumStrategy(size=1.0)
        n = max(MIN_HISTORY, FUNDING_LOOKBACK) + 5
        for i in range(n):
            orders = strat.on_tick(
                _snap(mid=2500.0 + i * 0.01, funding_rate=0.0001),
                _ctx(),
            )
        # Stable funding => z-score near 0 => no entry
        assert orders == []

    def test_extreme_negative_funding_with_uptrend_longs(self):
        """Very negative funding + bullish EMA -> go long."""
        from strategies.funding_momentum import FundingMomentumStrategy, MIN_HISTORY, FUNDING_LOOKBACK
        strat = FundingMomentumStrategy(size=1.0)
        n = max(MIN_HISTORY, FUNDING_LOOKBACK)
        # Build history with slightly positive funding and uptrend
        for i in range(n):
            mid = 2400.0 + i * 1.0  # uptrend
            strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5, funding_rate=0.0001), _ctx())
        # Now spike funding to very negative
        found_long = False
        for i in range(10):
            mid = 2400.0 + n + i * 1.0  # still uptrend
            orders = strat.on_tick(
                _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5, funding_rate=-0.01),
                _ctx(),
            )
            if orders and any(o.meta.get("signal") == "funding_long" for o in orders):
                found_long = True
                break
        assert found_long, "Should enter long on extreme negative funding with bullish EMA"

    def test_extreme_positive_funding_with_downtrend_shorts(self):
        """Very positive funding + bearish EMA -> go short."""
        from strategies.funding_momentum import FundingMomentumStrategy, MIN_HISTORY, FUNDING_LOOKBACK
        strat = FundingMomentumStrategy(size=1.0)
        n = max(MIN_HISTORY, FUNDING_LOOKBACK)
        # Build history with slightly negative funding and downtrend
        for i in range(n):
            mid = 2600.0 - i * 1.0  # downtrend
            strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5, funding_rate=0.0001), _ctx())
        # Now spike funding to very positive
        found_short = False
        for i in range(10):
            mid = 2600.0 - n - i * 1.0  # still downtrend
            orders = strat.on_tick(
                _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5, funding_rate=0.01),
                _ctx(),
            )
            if orders and any(o.meta.get("signal") == "funding_short" for o in orders):
                found_short = True
                break
        assert found_short, "Should enter short on extreme positive funding with bearish EMA"

    def test_meta_contains_funding_zscore(self):
        from strategies.funding_momentum import FundingMomentumStrategy, MIN_HISTORY, FUNDING_LOOKBACK
        strat = FundingMomentumStrategy(size=1.0)
        n = max(MIN_HISTORY, FUNDING_LOOKBACK)
        for i in range(n):
            mid = 2400.0 + i * 1.0
            strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5, funding_rate=0.0001), _ctx())
        for i in range(10):
            mid = 2400.0 + n + i * 1.0
            orders = strat.on_tick(
                _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5, funding_rate=-0.01),
                _ctx(),
            )
            if orders:
                assert "funding_zscore" in orders[0].meta
                assert "funding_rate" in orders[0].meta
                break

    def test_exit_on_funding_normalized(self):
        """If in position and funding z-score normalizes, should exit."""
        from strategies.funding_momentum import FundingMomentumStrategy, MIN_HISTORY, FUNDING_LOOKBACK
        strat = FundingMomentumStrategy(size=1.0)
        n = max(MIN_HISTORY, FUNDING_LOOKBACK)
        for i in range(n):
            mid = 2400.0 + i * 1.0
            strat.on_tick(_snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5, funding_rate=0.0001), _ctx())
        # Force long entry state
        strat.direction = 1
        strat.entry_price = 2500.0
        strat.peak_price = 2500.0
        strat.atr_at_entry = 5.0
        # Funding normalizes (z-score > -1.0 while long)
        mid = 2500.0
        orders = strat.on_tick(
            _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5, funding_rate=0.0001),
            _ctx(pos_qty=1.0),
        )
        # Should exit since funding z-score is near 0 (> -ZSCORE_EXIT)
        if orders:
            assert orders[0].side == "sell"
            assert orders[0].meta["signal"] == "funding_normalized"
