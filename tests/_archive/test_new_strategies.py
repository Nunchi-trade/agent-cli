"""Tests for momentum_breakout, grid_mm, basis_arb strategies."""
import os
import sys

import pytest

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import StrategyContext


def _snap(mid=2500.0, bid=2499.0, ask=2501.0, funding=0.0001, oi=1e6, ts=1000):
    spread_bps = round((ask - bid) / mid * 10000, 2) if mid > 0 else 0.0
    return MarketSnapshot(
        instrument="ETH-PERP", mid_price=mid, bid=bid, ask=ask,
        spread_bps=spread_bps,
        funding_rate=funding, open_interest=oi, timestamp_ms=ts,
    )


def _ctx(qty=0.0, reduce_only=False):
    return StrategyContext(position_qty=qty, reduce_only=reduce_only)


# ---------------------------------------------------------------------------
# MomentumBreakoutStrategy
# ---------------------------------------------------------------------------

class TestMomentumBreakout:
    def test_warmup_no_orders(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(lookback=5, size=1.0)
        # Only 3 ticks — not enough for lookback
        for _ in range(3):
            orders = strat.on_tick(_snap(), _ctx())
        assert orders == []

    def test_no_orders_on_zero_mid(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(lookback=5)
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_breakout_long_on_surge(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, breakout_threshold_bps=10.0,
            volume_surge_mult=1.5, size=1.0,
        )
        # Build up history at 2500
        for i in range(5):
            strat.on_tick(_snap(mid=2500, bid=2499, ask=2501, oi=1000, ts=i * 1000))
        # Price jumps up with high OI (volume surge)
        orders = strat.on_tick(
            _snap(mid=2520, bid=2519, ask=2521, oi=5000, ts=6000), _ctx()
        )
        # Should detect breakout
        buys = [o for o in orders if o.side == "buy"]
        assert len(buys) > 0
        assert buys[0].meta.get("signal") == "breakout_long"

    def test_trailing_stop_exits(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, trailing_stop_bps=100.0, size=1.0,
        )
        # Warmup
        for i in range(5):
            strat.on_tick(_snap(mid=2500, ts=i * 1000))
        # With long position, bid drops below trailing stop
        orders = strat.on_tick(
            _snap(mid=2400, bid=2370, ask=2401, ts=6000),
            _ctx(qty=1.0),
        )
        sells = [o for o in orders if o.side == "sell"]
        assert len(sells) > 0
        assert "trailing_stop" in sells[0].meta.get("signal", "")

    def test_meta_fields(self):
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strat = MomentumBreakoutStrategy(
            lookback=5, breakout_threshold_bps=10.0,
            volume_surge_mult=1.0, size=1.0,
        )
        for i in range(5):
            strat.on_tick(_snap(mid=2500, bid=2499, ask=2501, oi=1000, ts=i * 1000))
        orders = strat.on_tick(
            _snap(mid=2520, bid=2519, ask=2521, oi=2000, ts=6000), _ctx()
        )
        if orders:
            assert "signal" in orders[0].meta


# ---------------------------------------------------------------------------
# GridMMStrategy
# ---------------------------------------------------------------------------

class TestGridMM:
    def test_produces_grid_orders(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy(num_levels=3, size_per_level=0.5)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        assert len(buys) == 3
        assert len(sells) == 3

    def test_no_orders_on_zero_mid(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_grid_spacing(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy(grid_spacing_bps=10.0, num_levels=2, size_per_level=0.5)
        orders = strat.on_tick(_snap(mid=1000.0, bid=999.0, ask=1001.0), _ctx())
        buys = sorted([o for o in orders if o.side == "buy"], key=lambda o: -o.limit_price)
        sells = sorted([o for o in orders if o.side == "sell"], key=lambda o: o.limit_price)
        # Level 1 bid = 1000 - 1000*10/10000 = 999.0
        # Level 2 bid = 1000 - 2*1.0 = 998.0
        assert buys[0].limit_price == 999.0
        assert buys[1].limit_price == 998.0
        assert sells[0].limit_price == 1001.0
        assert sells[1].limit_price == 1002.0

    def test_reduce_only_closes(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy()
        orders = strat.on_tick(_snap(), _ctx(qty=1.0, reduce_only=True))
        assert len(orders) == 1
        assert orders[0].side == "sell"
        assert orders[0].meta.get("signal") == "reduce_only_close"

    def test_meta_contains_level(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy(num_levels=2, size_per_level=0.5)
        orders = strat.on_tick(_snap(), _ctx())
        for o in orders:
            assert "level" in o.meta or "signal" in o.meta


# ---------------------------------------------------------------------------
# BasisArbStrategy
# ---------------------------------------------------------------------------

class TestBasisArb:
    def test_warmup_no_orders(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(funding_window=5)
        # Only 2 ticks — need at least 3
        for _ in range(2):
            orders = strat.on_tick(_snap(funding=0.001), _ctx())
        assert orders == []

    def test_no_orders_on_zero_mid(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_short_on_positive_funding(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=1.0, funding_window=3, size=1.0)
        # High positive funding → short to collect
        for _ in range(3):
            orders = strat.on_tick(_snap(funding=0.01), _ctx())
        assert len(orders) > 0
        sells = [o for o in orders if o.side == "sell"]
        assert len(sells) > 0
        assert "contango" in sells[0].meta.get("signal", "")

    def test_long_on_negative_funding(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=1.0, funding_window=3, size=1.0)
        # Negative funding → long to collect
        for _ in range(3):
            orders = strat.on_tick(_snap(funding=-0.01), _ctx())
        assert len(orders) > 0
        buys = [o for o in orders if o.side == "buy"]
        assert len(buys) > 0
        assert "backwardation" in buys[0].meta.get("signal", "")

    def test_no_action_on_low_funding(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=500.0, funding_window=3, size=1.0)
        # Very low funding — annualized basis = 0.00001 * 365 * 3 * 10000 = 109.5 bps < 500
        for _ in range(3):
            orders = strat.on_tick(_snap(funding=0.00001), _ctx())
        assert orders == []

    def test_closes_wrong_side(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=1.0, funding_window=3, size=1.0)
        # Positive funding but we're long → should close
        for _ in range(3):
            orders = strat.on_tick(_snap(funding=0.01), _ctx(qty=1.0))
        sells = [o for o in orders if o.side == "sell"]
        assert len(sells) > 0
        assert "close_wrong_side" in sells[0].meta.get("signal", "")

    def test_meta_fields(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=1.0, funding_window=3)
        for _ in range(3):
            orders = strat.on_tick(_snap(funding=0.01), _ctx())
        if orders:
            assert "basis_ann_bps" in orders[0].meta


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestNewStrategyRegistry:
    def test_new_strategies_registered(self):
        from cli.strategy_registry import STRATEGY_REGISTRY
        assert "momentum_breakout" in STRATEGY_REGISTRY
        assert "grid_mm" in STRATEGY_REGISTRY
        assert "basis_arb" in STRATEGY_REGISTRY

    def test_resolve_new_strategies(self):
        from cli.strategy_registry import resolve_strategy_path
        for name in ["momentum_breakout", "grid_mm", "basis_arb"]:
            path = resolve_strategy_path(name)
            assert ":" in path
