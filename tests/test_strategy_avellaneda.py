"""Tests for AvellanedaStoikovMM strategy."""
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


class TestAvellanedaStoikov:
    def test_produces_two_sided_orders(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM(base_size=1.0)
        orders = strat.on_tick(_snap(), _ctx())
        assert len(orders) == 2
        buys = [o for o in orders if o.side == "buy"]
        sells = [o for o in orders if o.side == "sell"]
        assert len(buys) == 1
        assert len(sells) == 1

    def test_zero_mid_returns_empty(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_inventory_skews_reservation_price(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM(base_size=1.0, gamma=0.1)
        # Warm up vol estimator
        for i in range(5):
            strat.on_tick(_snap(mid=2500.0 + i * 0.1), _ctx())

        # Long inventory -> reservation price shifts DOWN -> bid is lower
        long_orders = strat.on_tick(_snap(mid=2500.0), _ctx(pos_qty=5.0))
        strat2 = AvellanedaStoikovMM(base_size=1.0, gamma=0.1)
        for i in range(5):
            strat2.on_tick(_snap(mid=2500.0 + i * 0.1), _ctx())
        flat_orders = strat2.on_tick(_snap(mid=2500.0), _ctx(pos_qty=0.0))

        long_bid = [o for o in long_orders if o.side == "buy"][0]
        flat_bid = [o for o in flat_orders if o.side == "buy"][0]
        # With long inventory, reservation price is lower -> bid is lower
        assert long_bid.limit_price < flat_bid.limit_price

    def test_reduce_only_long_only_sells(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM()
        orders = strat.on_tick(_snap(), _ctx(pos_qty=3.0, reduce_only=True))
        assert len(orders) == 1
        assert orders[0].side == "sell"
        assert orders[0].meta["signal"] == "reduce_only_sell"

    def test_reduce_only_short_only_buys(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM()
        orders = strat.on_tick(_snap(), _ctx(pos_qty=-3.0, reduce_only=True))
        assert len(orders) == 1
        assert orders[0].side == "buy"
        assert orders[0].meta["signal"] == "reduce_only_buy"

    def test_reduce_only_flat_returns_empty(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM()
        orders = strat.on_tick(_snap(), _ctx(pos_qty=0.0, reduce_only=True))
        assert orders == []

    def test_size_scales_with_inventory(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM(base_size=1.0, max_inventory=10.0)
        # No inventory -> full size
        orders_flat = strat.on_tick(_snap(), _ctx(pos_qty=0.0))
        # High inventory -> reduced size
        orders_loaded = strat.on_tick(_snap(), _ctx(pos_qty=8.0))
        flat_size = orders_flat[0].size
        loaded_size = orders_loaded[0].size
        assert loaded_size < flat_size

    def test_meta_fields_present(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM()
        orders = strat.on_tick(_snap(), _ctx())
        for o in orders:
            assert "signal" in o.meta
            assert "reservation_price" in o.meta
            assert "spread" in o.meta
            assert "sigma" in o.meta
            assert "inventory" in o.meta

    def test_spread_clamped_to_min(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM(min_spread_bps=50.0, gamma=0.0001)
        orders = strat.on_tick(_snap(mid=1000.0), _ctx())
        buy = [o for o in orders if o.side == "buy"][0]
        sell = [o for o in orders if o.side == "sell"][0]
        actual_spread = sell.limit_price - buy.limit_price
        min_spread = 1000.0 * (50.0 / 10_000)
        assert actual_spread >= min_spread - 0.02  # rounding tolerance

    def test_vol_warmup_uses_default(self):
        from strategies.avellaneda_mm import AvellanedaStoikovMM
        strat = AvellanedaStoikovMM(min_spread_bps=10.0)
        # First tick -> insufficient vol data -> uses default
        orders = strat.on_tick(_snap(mid=1000.0), _ctx())
        assert len(orders) == 2
