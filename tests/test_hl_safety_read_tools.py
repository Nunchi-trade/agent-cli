"""Tests for the safety + read tools added to DirectHLProxy / DirectMockProxy:

- reduce_only flag on place_order
- schedule_cancel (dead-man's switch)
- emergency_close_all (panic close)
- get_order_status (lookup by oid)
- get_funding_rates (current funding per coin)
"""
import time

import pytest
from unittest.mock import MagicMock

from common.models import MarketSnapshot
from cli.hl_adapter import (
    DirectHLProxy,
    DirectMockProxy,
    _funding_rates_from_markets,
)


def _mock_hl_proxy():
    hl = MagicMock()
    hl._info = MagicMock()
    hl._exchange = MagicMock()
    hl._address = "0xTEST"
    hl._ensure_client = MagicMock()
    hl.get_snapshot = MagicMock(return_value=MarketSnapshot(
        instrument="ETH-PERP", mid_price=2500.0, bid=2499.5, ask=2500.5,
        spread_bps=4.0, timestamp_ms=int(time.time() * 1000),
    ))
    return hl


def _make_proxy():
    return DirectHLProxy(_mock_hl_proxy())


_FILLED = {
    "status": "ok",
    "response": {"type": "order", "data": {"statuses": [
        {"filled": {"oid": "1", "avgPx": "2500.0", "totalSz": "1.0"}}
    ]}},
}


# ---- reduce_only ----

class TestReduceOnly:
    def test_passed_when_true(self):
        proxy = _make_proxy()
        captured = {}

        def mock_order(coin, is_buy, sz, price, order_type, reduce_only=False, builder=None):
            captured["reduce_only"] = reduce_only
            return _FILLED

        proxy._exchange.order = mock_order
        proxy.place_order("ETH-PERP", "sell", 1.0, 2500.0, tif="Gtc", reduce_only=True)
        assert captured["reduce_only"] is True

    def test_absent_by_default(self):
        """Default path must not pass reduce_only — preserves the call signature
        existing callers and test doubles rely on."""
        proxy = _make_proxy()
        seen = {}

        def mock_order(coin, is_buy, sz, price, order_type, builder=None, **kw):
            seen["kw"] = kw
            return _FILLED

        proxy._exchange.order = mock_order
        proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0, tif="Gtc")
        assert "reduce_only" not in seen["kw"]


# ---- schedule_cancel ----

class TestScheduleCancel:
    def test_ok(self):
        proxy = _make_proxy()
        proxy._exchange.schedule_cancel.return_value = {"status": "ok"}
        assert proxy.schedule_cancel(1_700_000_000_000) is True
        proxy._exchange.schedule_cancel.assert_called_once_with(1_700_000_000_000)

    def test_clear_with_none(self):
        proxy = _make_proxy()
        proxy._exchange.schedule_cancel.return_value = {"status": "ok"}
        assert proxy.schedule_cancel(None) is True
        proxy._exchange.schedule_cancel.assert_called_once_with(None)

    def test_rejected(self):
        proxy = _make_proxy()
        proxy._exchange.schedule_cancel.return_value = {"status": "err", "response": "too soon"}
        assert proxy.schedule_cancel(1) is False

    def test_exception(self):
        proxy = _make_proxy()
        proxy._exchange.schedule_cancel.side_effect = Exception("network")
        assert proxy.schedule_cancel(1) is False


# ---- get_order_status ----

class TestGetOrderStatus:
    def test_returns_dict(self):
        proxy = _make_proxy()
        proxy._info.query_order_by_oid.return_value = {"order": {"oid": 7}, "status": "open"}
        out = proxy.get_order_status("7")
        assert out["status"] == "open"
        proxy._info.query_order_by_oid.assert_called_once_with("0xTEST", 7)

    def test_error_returns_none(self):
        proxy = _make_proxy()
        proxy._info.query_order_by_oid.side_effect = Exception("boom")
        assert proxy.get_order_status("7") is None


# ---- get_funding_rates ----

class TestGetFundingRates:
    _MARKETS = [
        {"universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "BAD"}]},
        [{"funding": "0.0001"}, {"funding": "-0.0002"}, {"funding": None}],
    ]

    def test_all_coins(self):
        proxy = _make_proxy()
        proxy.get_all_markets = lambda: self._MARKETS
        rates = proxy.get_funding_rates()
        assert rates["BTC"] == pytest.approx(0.0001)
        assert rates["ETH"] == pytest.approx(-0.0002)
        assert rates["BAD"] == 0.0  # malformed funding -> 0.0, never raises

    def test_single_coin_with_instrument_suffix(self):
        proxy = _make_proxy()
        proxy.get_all_markets = lambda: self._MARKETS
        assert proxy.get_funding_rates("ETH-PERP") == {"ETH": pytest.approx(-0.0002)}

    def test_helper_handles_empty(self):
        assert _funding_rates_from_markets(None) == {}
        assert _funding_rates_from_markets([]) == {}


# ---- emergency_close_all ----

class TestEmergencyCloseAll:
    def test_cancels_orders_and_closes_positions(self):
        proxy = _make_proxy()
        proxy.get_open_orders = lambda instrument="": [
            {"coin": "ETH", "oid": 11}, {"coin": "BTC", "oid": 12},
        ]
        proxy.get_account_state = lambda: {"positions": [
            {"position": {"coin": "ETH", "szi": "0.5"}},
            {"position": {"coin": "BTC", "szi": "-0.1"}},
            {"position": {"coin": "SOL", "szi": "0"}},  # flat -> skipped
        ]}
        proxy._exchange.cancel.return_value = {"status": "ok"}
        proxy._exchange.market_close.return_value = {"status": "ok"}

        summary = proxy.emergency_close_all()

        assert summary["cancelled_orders"] == 2
        closed = {c["coin"] for c in summary["closed_positions"]}
        assert closed == {"ETH", "BTC"}
        assert all(c["ok"] for c in summary["closed_positions"])
        assert proxy._exchange.market_close.call_count == 2
        assert summary["errors"] == []

    def test_records_close_failure(self):
        proxy = _make_proxy()
        proxy.get_open_orders = lambda instrument="": []
        proxy.get_account_state = lambda: {"positions": [
            {"position": {"coin": "ETH", "szi": "1.0"}},
        ]}
        proxy._exchange.market_close.side_effect = Exception("rejected")

        summary = proxy.emergency_close_all()

        assert summary["cancelled_orders"] == 0
        assert summary["closed_positions"][0]["ok"] is False
        assert any("close ETH" in e for e in summary["errors"])

    def test_no_positions_no_orders(self):
        proxy = _make_proxy()
        proxy.get_open_orders = lambda instrument="": []
        proxy.get_account_state = lambda: {"positions": []}
        summary = proxy.emergency_close_all()
        assert summary == {"cancelled_orders": 0, "closed_positions": [], "errors": []}


# ---- DirectMockProxy parity ----

class TestMockProxyParity:
    def test_schedule_cancel(self):
        m = DirectMockProxy()
        assert m.schedule_cancel(123) is True
        assert m.schedule_cancel(None) is True

    def test_emergency_close_all(self):
        m = DirectMockProxy()
        s = m.emergency_close_all()
        assert set(s) == {"cancelled_orders", "closed_positions", "errors"}

    def test_get_order_status(self):
        m = DirectMockProxy()
        assert m.get_order_status("1")["status"] == "mock"

    def test_get_funding_rates(self):
        m = DirectMockProxy()
        rates = m.get_funding_rates()
        assert isinstance(rates, dict) and len(rates) > 0  # mock markets carry funding

    def test_place_order_reduce_only_flag(self):
        m = DirectMockProxy()
        fill = m.place_order("ETH-PERP", "sell", 1.0, 2500.0, reduce_only=True)
        assert fill is not None
        assert m._last_reduce_only is True
