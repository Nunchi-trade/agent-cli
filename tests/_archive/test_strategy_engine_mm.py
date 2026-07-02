"""Tests for EngineMMStrategy (production quoting engine MM)."""
import time
import pytest

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import StrategyContext


def _snap(mid=2500.0, bid=2499.5, ask=2500.5, funding_rate=0.0001):
    return MarketSnapshot(
        instrument="ETH-PERP", mid_price=mid, bid=bid, ask=ask,
        spread_bps=4.0, funding_rate=funding_rate,
        volume_24h=1e6, open_interest=1e5,
        timestamp_ms=int(time.time() * 1000),
    )

def _ctx(pos_qty=0.0, reduce_only=False):
    return StrategyContext(position_qty=pos_qty, reduce_only=reduce_only, round_number=1)


class TestEngineMMStrategy:
    def test_instantiation(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0, num_levels=3)
        assert strat.strategy_id == "engine_mm"

    def test_zero_mid_returns_empty(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(mid=0), _ctx())
        assert orders == []

    def test_produces_bid_ask_levels(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0, num_levels=3)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0
        sides = {o.side for o in orders}
        assert "buy" in sides
        assert "sell" in sides

    def test_all_orders_are_place_order(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx())
        for o in orders:
            assert o.action == "place_order"
            assert o.size > 0
            assert o.limit_price > 0

    def test_meta_contains_engine_fields(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) > 0
        meta = orders[0].meta
        assert "fv_raw" in meta
        assert "half_spread" in meta
        assert "vol_bin" in meta

    def test_reduce_only_with_long_position(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=2.0, reduce_only=True))
        if orders:
            assert all(o.side == "sell" for o in orders)

    def test_reduce_only_with_short_position(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx(pos_qty=-2.0, reduce_only=True))
        if orders:
            assert all(o.side == "buy" for o in orders)

    def test_funding_rate_updates(self):
        from strategies.engine_mm import EngineMMStrategy
        strat = EngineMMStrategy(base_size=1.0)
        # First tick with funding
        strat.on_tick(_snap(funding_rate=0.001), _ctx())
        # Second tick — should still work
        orders = strat.on_tick(_snap(funding_rate=0.0005), _ctx())
        assert isinstance(orders, list)
