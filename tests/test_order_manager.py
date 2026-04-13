"""Tests for cli/order_manager.py — order lifecycle management."""
import time
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from common.models import MarketSnapshot, StrategyDecision
from cli.order_manager import OrderManager
from parent.hl_proxy import HLFill


def _snapshot(mid=2500.0):
    return MarketSnapshot(
        instrument="ETH-PERP", mid_price=mid,
        bid=mid - 0.5, ask=mid + 0.5,
        spread_bps=4.0, timestamp_ms=int(time.time() * 1000),
    )


def _mock_hl(fill_on_order=True):
    hl = MagicMock()
    hl.get_open_orders.return_value = []
    hl.cancel_order.return_value = True
    if fill_on_order:
        hl.place_order.return_value = HLFill(
            oid="mock-fill", instrument="ETH-PERP", side="buy",
            price=Decimal("2500"), quantity=Decimal("1.0"),
            timestamp_ms=int(time.time() * 1000),
        )
    else:
        hl.place_order.return_value = None
    return hl


class TestUpdate:
    def test_place_order_fills(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP")
        decisions = [StrategyDecision(
            action="place_order", side="buy", size=1.0, limit_price=2500.0,
        )]
        fills = mgr.update(decisions, _snapshot())
        assert len(fills) == 1
        assert mgr.stats["total_placed"] == 1
        assert mgr.stats["total_filled"] == 1

    def test_noop_decision_skipped(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP")
        decisions = [StrategyDecision(action="noop")]
        fills = mgr.update(decisions, _snapshot())
        assert len(fills) == 0
        assert mgr.stats["total_placed"] == 0

    def test_zero_size_filtered(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP")
        decisions = [StrategyDecision(
            action="place_order", side="buy", size=0.0, limit_price=2500.0,
        )]
        fills = mgr.update(decisions, _snapshot())
        assert len(fills) == 0
        hl.place_order.assert_not_called()

    def test_zero_price_filtered(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP")
        decisions = [StrategyDecision(
            action="place_order", side="buy", size=1.0, limit_price=0.0,
        )]
        fills = mgr.update(decisions, _snapshot())
        assert len(fills) == 0

    def test_no_fill_returns_empty(self):
        hl = _mock_hl(fill_on_order=False)
        mgr = OrderManager(hl, instrument="ETH-PERP")
        decisions = [StrategyDecision(
            action="place_order", side="buy", size=1.0, limit_price=2500.0,
        )]
        fills = mgr.update(decisions, _snapshot())
        assert len(fills) == 0
        assert mgr.stats["total_placed"] == 1
        assert mgr.stats["total_filled"] == 0

    def test_order_type_passthrough(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP")
        decisions = [StrategyDecision(
            action="place_order", side="buy", size=1.0,
            limit_price=2500.0, order_type="Alo",
        )]
        mgr.update(decisions, _snapshot())
        call_kwargs = hl.place_order.call_args
        assert call_kwargs.kwargs.get("tif") == "Alo" or call_kwargs[1].get("tif") == "Alo"

    def test_builder_fee_passthrough(self):
        hl = _mock_hl()
        builder = {"b": "0xTEST", "f": 100}
        mgr = OrderManager(hl, instrument="ETH-PERP", builder=builder)
        decisions = [StrategyDecision(
            action="place_order", side="buy", size=1.0, limit_price=2500.0,
        )]
        mgr.update(decisions, _snapshot())
        call_kwargs = hl.place_order.call_args
        assert call_kwargs.kwargs.get("builder") == builder or call_kwargs[1].get("builder") == builder

    def test_multiple_decisions(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP")
        decisions = [
            StrategyDecision(action="place_order", side="buy", size=1.0, limit_price=2500.0),
            StrategyDecision(action="place_order", side="sell", size=0.5, limit_price=2501.0),
        ]
        fills = mgr.update(decisions, _snapshot())
        assert len(fills) == 2
        assert mgr.stats["total_placed"] == 2


class TestDryRun:
    def test_dry_run_no_order_placed(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP", dry_run=True)
        decisions = [StrategyDecision(
            action="place_order", side="buy", size=1.0, limit_price=2500.0,
        )]
        fills = mgr.update(decisions, _snapshot())
        assert len(fills) == 0
        hl.place_order.assert_not_called()
        assert mgr.stats["total_placed"] == 1  # counts as placed for tracking


class TestTWAP:
    def test_twap_routing(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP")
        decisions = [StrategyDecision(
            action="place_order", side="buy", size=10.0, limit_price=2500.0,
            meta={"execution_algo": "twap", "twap_duration_ticks": 5},
        )]
        fills = mgr.update(decisions, _snapshot())
        # TWAP submits parent, no immediate fill
        assert len(fills) == 0
        assert mgr.stats["total_placed"] == 1


class TestCancelAll:
    def test_cancels_open_orders(self):
        hl = _mock_hl()
        hl.get_open_orders.return_value = [
            {"oid": "order1"}, {"oid": "order2"},
        ]
        mgr = OrderManager(hl, instrument="ETH-PERP")
        count = mgr.cancel_all()
        assert count == 2

    def test_cancel_dry_run_noop(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP", dry_run=True)
        count = mgr.cancel_all()
        assert count == 0
        hl.get_open_orders.assert_not_called()

    def test_cancel_empty_no_calls(self):
        hl = _mock_hl()
        hl.get_open_orders.return_value = []
        mgr = OrderManager(hl, instrument="ETH-PERP")
        count = mgr.cancel_all()
        assert count == 0

    def test_cancel_accepts_paradex_order_id_shapes(self):
        hl = _mock_hl()
        hl.get_open_orders.return_value = [
            {"id": "px-order-1"},
            {"order_id": "px-order-2"},
            {"client_id": "px-order-3"},
        ]
        mgr = OrderManager(hl, instrument="SOL-USD-PERP")
        count = mgr.cancel_all()
        assert count == 3
        hl.cancel_order.assert_any_call("SOL-USD-PERP", "px-order-1")
        hl.cancel_order.assert_any_call("SOL-USD-PERP", "px-order-2")
        hl.cancel_order.assert_any_call("SOL-USD-PERP", "px-order-3")


class TestStats:
    def test_initial_stats(self):
        hl = _mock_hl()
        mgr = OrderManager(hl, instrument="ETH-PERP")
        assert mgr.stats == {"total_placed": 0, "total_filled": 0}
