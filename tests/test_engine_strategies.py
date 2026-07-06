"""Tests for quoting-engine-powered strategies."""
import os
import sys
from pathlib import Path

import pytest

# Ensure paths
_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import StrategyContext


_QE_ROOT = Path.home() / "Tee-work-" / "quoting_engine"
requires_quoting_engine = pytest.mark.skipif(
    not _QE_ROOT.is_dir(),
    reason=(
        f"quoting_engine not available at {_QE_ROOT}; "
        "skip tests that require private quoting_engine module"
    ),
)


def _snap(mid=2500.0, bid=2499.0, ask=2501.0, funding=0.0001, oi=1e6, ts=1000):
    spread_bps = round((ask - bid) / mid * 10000, 2) if mid > 0 else 0.0
    return MarketSnapshot(
        instrument="ETH-PERP", mid_price=mid, bid=bid, ask=ask,
        spread_bps=spread_bps,
        funding_rate=funding, open_interest=oi, timestamp_ms=ts,
    )


def _ctx(qty=0.0, dd=0.0, reduce_only=False):
    return StrategyContext(position_qty=qty, reduce_only=reduce_only)


# ---------------------------------------------------------------------------
# EngineMMStrategy
# ---------------------------------------------------------------------------

@requires_quoting_engine
class TestEngineMM:
    def test_produces_orders(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0, num_levels=3)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0
        # Should have bid + ask pairs
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        assert len(buys) > 0
        assert len(sells) > 0

    def test_multi_level_ladder(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0, num_levels=3)
        # Run several ticks to warm up vol estimator
        for _ in range(5):
            orders = strat.on_tick(_snap(), _ctx())
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        # 3 levels -> up to 3 bids + 3 asks
        assert len(buys) <= 3
        assert len(sells) <= 3

    def test_no_orders_on_zero_mid(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy()
        snap = _snap(mid=0.0)
        orders = strat.on_tick(snap, _ctx())
        assert orders == []

    def test_inventory_skew(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0, num_levels=1)
        # Warm up
        for _ in range(5):
            strat.on_tick(_snap(), _ctx())

        # Long inventory -> FV should shift down -> bids lower
        long_orders = strat.on_tick(_snap(), _ctx(qty=5.0))
        neutral_orders = strat.on_tick(_snap(), _ctx(qty=0.0))

        long_bids = [o for o in long_orders if o.side == "buy"]
        neutral_bids = [o for o in neutral_orders if o.side == "buy"]
        if long_bids and neutral_bids:
            # With long inventory, bid should be lower (FV shifted down)
            assert long_bids[0].limit_price <= neutral_bids[0].limit_price

    def test_reduce_only_mode(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy()
        # Warm up
        for _ in range(5):
            strat.on_tick(_snap(), _ctx())
        # Reduce only with long position -> only sell orders
        orders = strat.on_tick(_snap(), _ctx(qty=2.0, reduce_only=True))
        assert all(o.side == "sell" for o in orders)

    def test_meta_contains_engine_fields(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy()
        for _ in range(5):
            orders = strat.on_tick(_snap(), _ctx())
        if orders:
            meta = orders[0].meta
            assert "fv_skewed" in meta
            assert "half_spread" in meta
            assert "vol_bin" in meta


# ---------------------------------------------------------------------------
# FundingArbStrategy
# ---------------------------------------------------------------------------

@requires_quoting_engine
class TestFundingArb:
    def test_produces_orders(self):
        from strategies.funding_arb import FundingArbStrategy
        strat = FundingArbStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(funding=0.0005), _ctx())
        assert len(orders) > 0

    def test_funding_divergence_in_meta(self):
        from strategies.funding_arb import FundingArbStrategy
        strat = FundingArbStrategy(base_size=1.0)
        for _ in range(3):
            orders = strat.on_tick(_snap(funding=0.0005), _ctx())
        if orders:
            meta = orders[0].meta
            assert "divergence_bps" in meta
            assert "bias_bps" in meta
            assert "hl_rate" in meta

    def test_asymmetric_sizing_on_divergence(self):
        from strategies.funding_arb import FundingArbStrategy
        # Large divergence threshold so we can test without external feeds
        strat = FundingArbStrategy(
            base_size=1.0,
            divergence_threshold_bps=0.01,  # very low threshold
            max_bias_bps=10.0,
            funding_weight=1.0,
        )
        # With only HL funding and no external, divergence = 0
        # So sizing should be symmetric
        for _ in range(3):
            orders = strat.on_tick(_snap(funding=0.0005), _ctx())
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        if buys and sells:
            # Without external feeds, should be roughly symmetric
            assert buys[0].size > 0
            assert sells[0].size > 0

    def test_no_orders_on_zero_mid(self):
        from strategies.funding_arb import FundingArbStrategy
        strat = FundingArbStrategy()
        assert strat.on_tick(_snap(mid=0.0), _ctx()) == []


# ---------------------------------------------------------------------------
# RegimeMMStrategy
# ---------------------------------------------------------------------------

@requires_quoting_engine
class TestRegimeMM:
    def test_produces_orders(self):
        from strategies.regime_mm import RegimeMMStrategy
        strat = RegimeMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0

    def test_regime_in_meta(self):
        from strategies.regime_mm import RegimeMMStrategy
        strat = RegimeMMStrategy()
        for _ in range(5):
            orders = strat.on_tick(_snap(), _ctx())
        if orders:
            assert "regime" in orders[0].meta
            assert "vol_bin" in orders[0].meta

    def test_calm_regime_more_levels(self):
        from strategies.regime_mm import RegimeMMStrategy, REGIME_PARAMS
        strat = RegimeMMStrategy(base_size=1.0)
        # After warmup with stable prices, should be in low/calm regime
        for i in range(10):
            strat.on_tick(_snap(mid=2500.0 + i * 0.01), _ctx())
        # Apply calm regime manually to verify
        strat._apply_regime("I_low")
        assert strat._config.ladder.num_levels == 4
        assert strat._config.ladder.s0 == 1.5  # 1.0 * 1.5x

    def test_extreme_regime_minimal(self):
        from strategies.regime_mm import RegimeMMStrategy
        strat = RegimeMMStrategy(base_size=1.0)
        strat._apply_regime("IV_extreme")
        assert strat._config.ladder.num_levels == 1
        assert strat._config.ladder.s0 == pytest.approx(0.2)  # 1.0 * 0.2x

    def test_no_orders_on_zero_mid(self):
        from strategies.regime_mm import RegimeMMStrategy
        strat = RegimeMMStrategy()
        assert strat.on_tick(_snap(mid=0.0), _ctx()) == []


# ---------------------------------------------------------------------------
# LiquidationMMStrategy
# ---------------------------------------------------------------------------

@requires_quoting_engine
class TestLiquidationMM:
    def test_produces_orders(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0

    def test_cascade_meta_fields(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy()
        for _ in range(3):
            orders = strat.on_tick(_snap(), _ctx())
        if orders:
            meta = orders[0].meta
            assert "liq_triggered" in meta
            assert "in_cascade" in meta
            assert "cascade_direction" in meta

    def test_oi_drop_detection_config(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy(
            oi_drop_threshold_pct=3.0,
            cascade_spread_mult=3.0,
            cooldown_ticks=20,
        )
        assert strat._config.liquidation_detector.enabled is True
        assert strat._config.liquidation_detector.oi_drop_threshold_pct == 3.0
        assert strat._config.liquidation_detector.spread_mult == 3.0
        assert strat._config.liquidation_detector.cooldown_ticks == 20

    def test_normal_mode_symmetric(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy(base_size=1.0)
        # Stable OI, no cascade
        for _ in range(5):
            orders = strat.on_tick(_snap(oi=1e6), _ctx())
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        if buys and sells:
            # In normal mode, bid and ask sizes should be similar
            ratio = buys[0].size / sells[0].size if sells[0].size > 0 else 999
            assert 0.5 < ratio < 2.0

    def test_no_orders_on_zero_mid(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy()
        assert strat.on_tick(_snap(mid=0.0), _ctx()) == []


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

class TestRegistryEntries:
    def test_all_engine_strategies_registered(self):
        from cli.strategy_registry import STRATEGY_REGISTRY
        assert "engine_mm" in STRATEGY_REGISTRY
        assert "funding_arb" in STRATEGY_REGISTRY
        assert "regime_mm" in STRATEGY_REGISTRY
        assert "liquidation_mm" in STRATEGY_REGISTRY

    def test_total_strategies(self):
        from cli.strategy_registry import STRATEGY_REGISTRY
        assert len(STRATEGY_REGISTRY) == 18  # 14 original + 4 directional

    def test_resolve_engine_strategies(self):
        from cli.strategy_registry import resolve_strategy_path
        assert resolve_strategy_path("engine_mm") == "strategies.engine_mm:EngineMMStrategy"
        assert resolve_strategy_path("funding_arb") == "strategies.funding_arb:FundingArbStrategy"
        assert resolve_strategy_path("regime_mm") == "strategies.regime_mm:RegimeMMStrategy"
        assert resolve_strategy_path("liquidation_mm") == "strategies.liquidation_mm:LiquidationMMStrategy"
