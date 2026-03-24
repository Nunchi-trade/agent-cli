"""Tests for BasisArbStrategy."""
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


def _ctx(pos_qty=0.0):
    return StrategyContext(position_qty=pos_qty, round_number=1)


class TestBasisArb:
    def test_warmup_returns_empty(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(funding_window=10)
        # Only 2 ticks — needs at least 3
        for _ in range(2):
            orders = strat.on_tick(_snap(), _ctx())
        assert orders == []

    def test_no_signal_below_threshold(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=5.0, funding_window=5)
        # Very small funding rate → basis below threshold
        for _ in range(5):
            orders = strat.on_tick(_snap(funding_rate=0.0000001), _ctx())
        assert orders == []

    def test_short_on_contango(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=5.0, funding_window=3, size=1.0)
        # High positive funding → contango → short
        for _ in range(3):
            orders = strat.on_tick(_snap(funding_rate=0.001), _ctx())
        assert len(orders) == 1
        assert orders[0].side == "sell"
        assert orders[0].meta["signal"] == "short_contango"

    def test_long_on_backwardation(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=5.0, funding_window=3, size=1.0)
        # High negative funding → backwardation → long
        for _ in range(3):
            orders = strat.on_tick(_snap(funding_rate=-0.001), _ctx())
        assert len(orders) == 1
        assert orders[0].side == "buy"
        assert orders[0].meta["signal"] == "long_backwardation"

    def test_close_wrong_side_long_in_contango(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=5.0, funding_window=3)
        # Contango but we're long → close
        for _ in range(3):
            orders = strat.on_tick(_snap(funding_rate=0.001), _ctx(pos_qty=2.0))
        assert len(orders) == 1
        assert orders[0].side == "sell"
        assert orders[0].meta["signal"] == "close_wrong_side"
        assert orders[0].size == 2.0

    def test_close_wrong_side_short_in_backwardation(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=5.0, funding_window=3)
        # Backwardation but we're short → close
        for _ in range(3):
            orders = strat.on_tick(_snap(funding_rate=-0.001), _ctx(pos_qty=-1.5))
        assert len(orders) == 1
        assert orders[0].side == "buy"
        assert orders[0].meta["signal"] == "close_wrong_side"
        assert orders[0].size == 1.5

    def test_no_double_short_if_already_short(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=5.0, funding_window=3, size=1.0)
        # Contango and already short → still signals (allows adding)
        for _ in range(3):
            orders = strat.on_tick(_snap(funding_rate=0.001), _ctx(pos_qty=-1.0))
        assert len(orders) == 1
        assert orders[0].side == "sell"

    def test_zero_mid_returns_empty(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy()
        orders = strat.on_tick(_snap(mid=0), _ctx())
        assert orders == []

    def test_meta_contains_basis_info(self):
        from strategies.basis_arb import BasisArbStrategy
        strat = BasisArbStrategy(basis_threshold_bps=5.0, funding_window=3, size=1.0)
        for _ in range(3):
            orders = strat.on_tick(_snap(funding_rate=0.001), _ctx())
        assert "basis_ann_bps" in orders[0].meta
        assert "avg_funding" in orders[0].meta
