"""Tests for OIDivergenceStrategy."""
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


class TestOIDivergence:
    def test_warmup_returns_empty(self):
        from strategies.oi_divergence import OIDivergenceStrategy, MIN_HISTORY
        strat = OIDivergenceStrategy(size=1.0)
        for i in range(MIN_HISTORY - 2):
            orders = strat.on_tick(_snap(mid=2500.0 + i * 0.01), _ctx())
        assert orders == []

    def test_zero_mid_returns_empty(self):
        from strategies.oi_divergence import OIDivergenceStrategy
        strat = OIDivergenceStrategy()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_stable_market_no_entry(self):
        """Flat price and OI -> no entry signal."""
        from strategies.oi_divergence import OIDivergenceStrategy, MIN_HISTORY
        strat = OIDivergenceStrategy(size=1.0)
        for i in range(MIN_HISTORY + 5):
            orders = strat.on_tick(
                _snap(mid=2500.0, open_interest=1e5, volume_24h=1e6),
                _ctx(),
            )
        assert orders == []

    def test_price_up_oi_up_volume_surge_goes_long(self):
        """Price up + OI up + volume above average -> long entry."""
        from strategies.oi_divergence import OIDivergenceStrategy, MIN_HISTORY, LOOKBACK
        strat = OIDivergenceStrategy(size=1.0)
        # Build history with stable prices/OI and low volume
        for i in range(MIN_HISTORY):
            strat.on_tick(
                _snap(mid=2500.0, bid=2499.5, ask=2500.5,
                      open_interest=1e5, volume_24h=1e5),
                _ctx(),
            )
        # Now: price rises, OI rises, volume surges
        found_long = False
        for i in range(LOOKBACK + 5):
            mid = 2500.0 + i * 2.0  # strong uptrend
            oi = 1e5 + i * 1000     # OI rising
            orders = strat.on_tick(
                _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5,
                      open_interest=oi, volume_24h=5e5),
                _ctx(),
            )
            if orders and any(o.meta.get("signal") == "oi_agreement_long" for o in orders):
                found_long = True
                break
        assert found_long, "Should enter long on price up + OI up + volume surge"

    def test_meta_fields_present(self):
        from strategies.oi_divergence import OIDivergenceStrategy, MIN_HISTORY, LOOKBACK
        strat = OIDivergenceStrategy(size=1.0)
        for i in range(MIN_HISTORY):
            strat.on_tick(
                _snap(mid=2500.0, open_interest=1e5, volume_24h=1e5),
                _ctx(),
            )
        for i in range(LOOKBACK + 5):
            mid = 2500.0 + i * 2.0
            oi = 1e5 + i * 1000
            orders = strat.on_tick(
                _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5,
                      open_interest=oi, volume_24h=5e5),
                _ctx(),
            )
            if orders:
                meta = orders[0].meta
                assert "price_return" in meta
                assert "oi_change" in meta
                assert "vol_above_avg" in meta
                assert "rsi" in meta
                break

    def test_exit_on_oi_divergence(self):
        """If long and OI starts falling, should exit."""
        from strategies.oi_divergence import OIDivergenceStrategy, MIN_HISTORY
        strat = OIDivergenceStrategy(size=1.0)
        for i in range(MIN_HISTORY):
            strat.on_tick(
                _snap(mid=2500.0, open_interest=1e5, volume_24h=1e5),
                _ctx(),
            )
        # Force long position state
        strat.direction = 1
        strat.entry_price = 2500.0
        strat.peak_price = 2500.0
        strat.atr_at_entry = 5.0
        # Feed data where OI is declining (oi_down)
        found_exit = False
        for i in range(30):
            oi = 1e5 - i * 2000  # OI dropping
            mid = 2500.0 + i * 0.1  # price stable-ish
            orders = strat.on_tick(
                _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5,
                      open_interest=max(oi, 1000), volume_24h=1e5),
                _ctx(pos_qty=1.0),
            )
            if orders and any(o.meta.get("signal") == "oi_divergence" for o in orders):
                found_exit = True
                break
        assert found_exit, "Should exit on OI divergence when long"

    def test_order_type_is_ioc(self):
        from strategies.oi_divergence import OIDivergenceStrategy, MIN_HISTORY, LOOKBACK
        strat = OIDivergenceStrategy(size=1.0)
        for i in range(MIN_HISTORY):
            strat.on_tick(
                _snap(mid=2500.0, open_interest=1e5, volume_24h=1e5),
                _ctx(),
            )
        for i in range(LOOKBACK + 5):
            mid = 2500.0 + i * 2.0
            oi = 1e5 + i * 1000
            orders = strat.on_tick(
                _snap(mid=mid, bid=mid - 0.5, ask=mid + 0.5,
                      open_interest=oi, volume_24h=5e5),
                _ctx(),
            )
            if orders:
                assert orders[0].order_type == "Ioc"
                break
