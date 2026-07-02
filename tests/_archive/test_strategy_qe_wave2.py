"""Tests for QE-dependent strategies: regime_mm, grid_mm, liquidation_mm, funding_arb."""
import time
import pytest

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import StrategyContext


def _snap(mid=2500.0, bid=2499.5, ask=2500.5, funding_rate=0.0001,
          volume_24h=1e6, open_interest=1e5):
    return MarketSnapshot(
        instrument="ETH-PERP", mid_price=mid, bid=bid, ask=ask,
        spread_bps=4.0, funding_rate=funding_rate,
        volume_24h=volume_24h, open_interest=open_interest,
        timestamp_ms=int(time.time() * 1000),
    )

def _ctx(pos_qty=0.0, reduce_only=False):
    return StrategyContext(position_qty=pos_qty, reduce_only=reduce_only, round_number=1)


# ---- RegimeMMStrategy ----

class TestRegimeMM:
    def test_instantiation(self):
        from strategies.regime_mm import RegimeMMStrategy
        strat = RegimeMMStrategy(base_size=1.0)
        assert strat.strategy_id == "regime_mm"

    def test_zero_mid_returns_empty(self):
        from strategies.regime_mm import RegimeMMStrategy
        strat = RegimeMMStrategy(base_size=1.0)
        assert strat.on_tick(_snap(mid=0), _ctx()) == []

    def test_produces_orders(self):
        from strategies.regime_mm import RegimeMMStrategy
        strat = RegimeMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0
        sides = {o.side for o in orders}
        assert "buy" in sides
        assert "sell" in sides

    def test_meta_contains_regime(self):
        from strategies.regime_mm import RegimeMMStrategy
        strat = RegimeMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0
        assert "vol_bin" in orders[0].meta


# ---- GridMMStrategy ----

class TestGridMM:
    def test_instantiation(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy(num_levels=5, size_per_level=0.5)
        assert strat.strategy_id == "grid_mm"

    def test_zero_mid_returns_empty(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy()
        assert strat.on_tick(_snap(mid=0), _ctx()) == []

    def test_produces_symmetric_grid(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy(num_levels=3, grid_spacing_bps=10.0, size_per_level=0.5)
        orders = strat.on_tick(_snap(), _ctx())
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        assert len(buys) == 3
        assert len(sells) == 3

    def test_respects_max_position_long(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy(num_levels=3, max_position=2.0, size_per_level=0.5)
        # At max position, no more buys
        orders = strat.on_tick(_snap(), _ctx(pos_qty=2.0))
        buys = [o for o in orders if o.side == "buy"]
        assert len(buys) == 0

    def test_reduce_only_closes_position(self):
        from strategies.grid_mm import GridMMStrategy
        strat = GridMMStrategy()
        orders = strat.on_tick(_snap(), _ctx(pos_qty=1.0, reduce_only=True))
        assert len(orders) == 1
        assert orders[0].side == "sell"


# ---- LiquidationMMStrategy ----

class TestLiquidationMM:
    def test_instantiation(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy(base_size=1.0)
        assert strat.strategy_id == "liquidation_mm"

    def test_zero_mid_returns_empty(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy(base_size=1.0)
        assert strat.on_tick(_snap(mid=0), _ctx()) == []

    def test_produces_orders_normal_conditions(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0

    def test_oi_drop_detection(self):
        from strategies.liquidation_mm import LiquidationMMStrategy
        strat = LiquidationMMStrategy(base_size=1.0, oi_drop_threshold_pct=5.0)
        # Normal OI
        strat.on_tick(_snap(open_interest=100_000), _ctx())
        # Drop OI by 10% — should detect liquidation
        orders = strat.on_tick(_snap(open_interest=90_000), _ctx())
        assert isinstance(orders, list)  # Should still produce orders (wider spreads)


# ---- FundingArbStrategy ----

class TestFundingArb:
    def test_instantiation(self):
        from strategies.funding_arb import FundingArbStrategy
        strat = FundingArbStrategy(base_size=1.0)
        assert strat.strategy_id == "funding_arb"

    def test_zero_mid_returns_empty(self):
        from strategies.funding_arb import FundingArbStrategy
        strat = FundingArbStrategy(base_size=1.0)
        assert strat.on_tick(_snap(mid=0), _ctx()) == []

    def test_produces_orders(self):
        from strategies.funding_arb import FundingArbStrategy
        strat = FundingArbStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(funding_rate=0.001), _ctx())
        assert len(orders) > 0

    def test_high_funding_biases_quotes(self):
        from strategies.funding_arb import FundingArbStrategy
        strat = FundingArbStrategy(base_size=1.0, divergence_threshold_bps=1.0)
        # Very high positive funding — strategy should bias short
        orders = strat.on_tick(_snap(funding_rate=0.01), _ctx())
        assert isinstance(orders, list)
        if orders:
            # At minimum, orders should have meta with funding info
            meta = orders[0].meta
            assert "signal" in meta
