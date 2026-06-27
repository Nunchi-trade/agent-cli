"""Tests for the Pear Protocol VenueAdapter."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import pytest
import requests

from adapters.pear_adapter import (
    BASE_URL,
    BUILDER_ADDRESS,
    DEFAULT_CLIENT_ID,
    PearAuth,
    PearVenueAdapter,
    _legs_for_side,
    _parse_pair,
    _position_response_to_fill,
)
from common.venue_adapter import Fill, VenueAdapter, VenueCapabilities


# ---------------------------------------------------------------------------
# Fake HTTP transport
# ---------------------------------------------------------------------------

class FakeHTTP:
    """Records every request and returns canned responses by (method, path)."""

    def __init__(self):
        self.calls: List[Tuple[str, str, Dict[str, Any]]] = []
        self.routes: Dict[Tuple[str, str], Any] = {}

    def add(self, method: str, path: str, response: Any) -> None:
        self.routes[(method.upper(), path)] = response

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        key = (method.upper(), path)
        self.calls.append((method.upper(), path, {"params": params, "json": json, "headers": headers}))
        if key not in self.routes:
            raise AssertionError(f"unexpected request: {method} {path}")
        resp = self.routes[key]
        if isinstance(resp, Exception):
            raise resp
        if callable(resp):
            return resp({"params": params, "json": json, "headers": headers})
        return resp


def _stub_auth_response(expires_in: int = 900) -> Dict[str, Any]:
    return {
        "accessToken": "access-tok",
        "refreshToken": "refresh-tok",
        "tokenType": "Bearer",
        "expiresIn": expires_in,
        "address": "0xabc",
        "clientId": DEFAULT_CLIENT_ID,
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_base_url(self):
        assert BASE_URL == "https://hl-v2.pearprotocol.io"

    def test_builder_address(self):
        assert BUILDER_ADDRESS == "0xA47D4d99191db54A4829cdf3de2417E527c3b042"
        assert len(BUILDER_ADDRESS) == 42

    def test_default_client_id(self):
        assert DEFAULT_CLIENT_ID == "NUNCHI"


# ---------------------------------------------------------------------------
# Pair parsing & leg flipping
# ---------------------------------------------------------------------------

class TestPairParsing:
    def test_valid_pair(self):
        assert _parse_pair("ETH-BTC") == ("ETH", "BTC")

    @pytest.mark.parametrize("bad", ["ETH", "ETH-", "-BTC", "", "ETH-BTC-SOL", "ETHBTC"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError, match="pair-format"):
            _parse_pair(bad)


class TestLegFlip:
    def test_buy_long_first_leg(self):
        longs, shorts = _legs_for_side("ETH-BTC", "buy")
        assert longs == [{"asset": "ETH", "weight": 1.0}]
        assert shorts == [{"asset": "BTC", "weight": 1.0}]

    def test_sell_flips(self):
        longs, shorts = _legs_for_side("ETH-BTC", "sell")
        assert longs == [{"asset": "BTC", "weight": 1.0}]
        assert shorts == [{"asset": "ETH", "weight": 1.0}]

    def test_invalid_side(self):
        with pytest.raises(ValueError, match="side"):
            _legs_for_side("ETH-BTC", "hold")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestPearAuth:
    def test_bootstrap_with_api_key(self):
        http = FakeHTTP()
        http.add("POST", "/auth/authenticate", _stub_auth_response())
        auth = PearAuth(http, address="0xABC")
        auth.bootstrap_with_api_key("my-api-key")
        assert auth.headers() == {"Authorization": "Bearer access-tok"}
        sent = http.calls[0][2]["json"]
        assert sent["method"] == "api_key"
        assert sent["address"] == "0xabc"
        assert sent["clientId"] == DEFAULT_CLIENT_ID
        assert sent["details"] == {"apiKey": "my-api-key"}

    def test_headers_without_bootstrap_raises(self):
        auth = PearAuth(FakeHTTP(), address="0xabc")
        with pytest.raises(RuntimeError, match="bootstrap"):
            auth.headers()

    def test_refresh_triggered_when_expired(self):
        http = FakeHTTP()
        http.add("POST", "/auth/authenticate", _stub_auth_response(expires_in=1))
        http.add(
            "POST",
            "/auth/refresh",
            {"accessToken": "new-tok", "refreshToken": "new-refresh",
             "tokenType": "Bearer", "expiresIn": 900},
        )
        auth = PearAuth(http, address="0xabc")
        auth.bootstrap_with_api_key("k")
        time.sleep(0.01)
        # Force expiry by mutating internal state instead of waiting 60s.
        auth._tokens.expires_at = time.time() - 1
        assert auth.headers() == {"Authorization": "Bearer new-tok"}
        methods_paths = [(m, p) for m, p, _ in http.calls]
        assert ("POST", "/auth/refresh") in methods_paths

    def test_no_refresh_when_fresh(self):
        http = FakeHTTP()
        http.add("POST", "/auth/authenticate", _stub_auth_response(expires_in=900))
        auth = PearAuth(http, address="0xabc")
        auth.bootstrap_with_api_key("k")
        for _ in range(3):
            auth.headers()
        assert not any(p == "/auth/refresh" for _, p, _ in http.calls)

    def test_create_api_key(self):
        http = FakeHTTP()
        http.add("POST", "/auth/authenticate", _stub_auth_response())
        http.add("POST", "/api-keys", {"id": "i", "apiKey": "secret", "createdAt": "t"})
        auth = PearAuth(http, address="0xabc")
        auth.bootstrap_with_api_key("k")
        assert auth.create_api_key(name="bot") == "secret"
        last = http.calls[-1]
        assert last[1] == "/api-keys"
        assert last[2]["json"] == {"name": "bot"}
        assert last[2]["headers"] == {"Authorization": "Bearer access-tok"}


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

@pytest.fixture
def authed_adapter():
    http = FakeHTTP()
    http.add("POST", "/auth/authenticate", _stub_auth_response())
    auth = PearAuth(http, address="0xabc")
    auth.bootstrap_with_api_key("k")
    return PearVenueAdapter(http=http, auth=auth), http


class TestPearVenueAdapter:
    def test_is_venue_adapter(self):
        assert issubclass(PearVenueAdapter, VenueAdapter)

    def test_capabilities(self):
        a = PearVenueAdapter()
        caps = a.capabilities()
        assert isinstance(caps, VenueCapabilities)
        assert not caps.supports_alo
        assert caps.supports_trigger_orders
        assert caps.supports_builder_fee
        assert not caps.supports_cross_margin

    def test_unauthenticated_raises(self):
        a = PearVenueAdapter(http=FakeHTTP())
        with pytest.raises(RuntimeError, match="unauthenticated"):
            a.get_account_state()


class TestPlaceOrder:
    def test_market_order_happy_path(self, authed_adapter):
        a, http = authed_adapter
        http.add(
            "POST",
            "/positions",
            {
                "orderId": "ord-1",
                "fills": [
                    {"size": 0.5, "price": 60000.0, "externalFeePaid": 0.3,
                     "builderFeePaid": 0.05, "fillTime": 1700000000000},
                    {"size": 0.5, "price": 60100.0, "externalFeePaid": 0.3,
                     "builderFeePaid": 0.05, "fillTime": 1700000001000},
                ],
            },
        )
        fill = a.place_order("ETH-BTC", "buy", 1.0, 60050.0, tif="Ioc")
        assert fill is not None
        assert isinstance(fill, Fill)
        assert fill.oid == "ord-1"
        assert fill.instrument == "ETH-BTC"
        assert fill.side == "buy"
        assert fill.quantity == 1.0
        assert fill.price == pytest.approx(60050.0)
        assert fill.fee == pytest.approx(0.7)
        assert fill.timestamp_ms == 1700000001000

        sent = next(c for c in http.calls if c[0] == "POST" and c[1] == "/positions")
        body = sent[2]["json"]
        assert body["executionType"] == "MARKET"
        assert body["usdValue"] == pytest.approx(60050.0)
        assert body["leverage"] == 1
        assert 0.001 <= body["slippage"] <= 0.1
        assert body["longAssets"] == [{"asset": "ETH", "weight": 1.0}]
        assert body["shortAssets"] == [{"asset": "BTC", "weight": 1.0}]
        assert sent[2]["headers"] == {"Authorization": "Bearer access-tok"}

    def test_sell_flips_legs(self, authed_adapter):
        a, http = authed_adapter
        http.add("POST", "/positions", {"orderId": "ord-2", "fills": []})
        a.place_order("ETH-BTC", "sell", 1.0, 60000.0)
        body = http.calls[-1][2]["json"]
        assert body["longAssets"] == [{"asset": "BTC", "weight": 1.0}]
        assert body["shortAssets"] == [{"asset": "ETH", "weight": 1.0}]

    def test_no_fills_returns_none(self, authed_adapter):
        a, http = authed_adapter
        http.add("POST", "/positions", {"orderId": "ord-3", "fills": []})
        assert a.place_order("ETH-BTC", "buy", 1.0, 60000.0) is None

    def test_rejects_alo(self, authed_adapter):
        a, _ = authed_adapter
        with pytest.raises(NotImplementedError, match="MARKET-only"):
            a.place_order("ETH-BTC", "buy", 1.0, 60000.0, tif="Alo")

    def test_rejects_below_minimum(self, authed_adapter):
        a, _ = authed_adapter
        with pytest.raises(ValueError, match="minimum"):
            a.place_order("ETH-BTC", "buy", 0.0001, 0.5)

    def test_rejects_bad_pair(self, authed_adapter):
        a, _ = authed_adapter
        with pytest.raises(ValueError, match="pair-format"):
            a.place_order("ETHBTC", "buy", 1.0, 60000.0)

    def test_uses_staged_leverage(self, authed_adapter):
        a, http = authed_adapter
        http.add("POST", "/positions", {"orderId": "ord", "fills": []})
        a.set_leverage(5, "ETH")
        a.place_order("ETH-BTC", "buy", 1.0, 60000.0)
        assert http.calls[-1][2]["json"]["leverage"] == 5


class TestCancelOrder:
    def test_cancel_success(self, authed_adapter):
        a, http = authed_adapter
        http.add("DELETE", "/orders/ord-1/cancel", {"orderId": "ord-1", "status": "CANCELLED"})
        assert a.cancel_order("ETH-BTC", "ord-1") is True

    def test_cancel_http_error_returns_false(self, authed_adapter):
        a, http = authed_adapter
        http.add("DELETE", "/orders/missing/cancel", requests.HTTPError("404"))
        assert a.cancel_order("ETH-BTC", "missing") is False

    def test_cancel_without_instrument(self, authed_adapter):
        a, http = authed_adapter
        http.add("DELETE", "/orders/x/cancel", {"status": "CANCELLED"})
        assert a.cancel_order("", "x") is True


class TestOpenOrders:
    def test_returns_all_when_no_filter(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/orders/open", [
            {"orderId": "a", "longAssets": [{"asset": "ETH"}], "shortAssets": [{"asset": "BTC"}]},
            {"orderId": "b", "longAssets": [{"asset": "SOL"}], "shortAssets": [{"asset": "ETH"}]},
        ])
        assert len(a.get_open_orders()) == 2

    def test_filters_by_instrument_both_directions(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/orders/open", [
            {"orderId": "a", "longAssets": [{"asset": "ETH"}], "shortAssets": [{"asset": "BTC"}]},
            {"orderId": "b", "longAssets": [{"asset": "BTC"}], "shortAssets": [{"asset": "ETH"}]},
            {"orderId": "c", "longAssets": [{"asset": "SOL"}], "shortAssets": [{"asset": "ETH"}]},
        ])
        out = a.get_open_orders("ETH-BTC")
        oids = {o["orderId"] for o in out}
        assert oids == {"a", "b"}


class TestAccount:
    def test_get_account_state(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/accounts", {"agentWalletAddress": "0x1", "totalClosedTrades": 7})
        st = a.get_account_state()
        assert st["agentWalletAddress"] == "0x1"
        assert st["totalClosedTrades"] == 7

    def test_set_leverage_out_of_range(self, authed_adapter):
        a, _ = authed_adapter
        with pytest.raises(ValueError):
            a.set_leverage(0, "ETH")
        with pytest.raises(ValueError):
            a.set_leverage(101, "ETH")


class TestPositions:
    def test_returns_all_when_no_filter(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/positions", [
            {"positionId": "p1", "longAssets": [{"coin": "ETH"}], "shortAssets": [{"coin": "BTC"}]},
            {"positionId": "p2", "longAssets": [{"coin": "SOL"}], "shortAssets": [{"coin": "ETH"}]},
        ])
        assert len(a.get_positions()) == 2

    def test_filters_by_instrument_both_directions(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/positions", [
            {"positionId": "p1", "longAssets": [{"coin": "ETH"}], "shortAssets": [{"coin": "BTC"}]},
            {"positionId": "p2", "longAssets": [{"coin": "BTC"}], "shortAssets": [{"coin": "ETH"}]},
            {"positionId": "p3", "longAssets": [{"coin": "SOL"}], "shortAssets": [{"coin": "ETH"}]},
        ])
        out = a.get_positions("ETH-BTC")
        assert {p["positionId"] for p in out} == {"p1", "p2"}

    def test_empty_when_none_match(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/positions", [
            {"positionId": "p3", "longAssets": [{"coin": "SOL"}], "shortAssets": [{"coin": "ETH"}]},
        ])
        assert a.get_positions("ETH-BTC") == []

    def test_unwraps_data_key(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/positions", {"data": [{"positionId": "p1"}]})
        assert a.get_positions() == [{"positionId": "p1"}]


class TestTradeHistory:
    def test_returns_list(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/trade-history", [
            {"tradeHistoryId": "t1", "positionId": "p1", "realizedPnl": 12.5},
        ])
        out = a.get_trade_history()
        assert out == [{"tradeHistoryId": "t1", "positionId": "p1", "realizedPnl": 12.5}]

    def test_default_params_limit_only(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/trade-history", [])
        a.get_trade_history()
        params = next(c for c in http.calls if c[1] == "/trade-history")[2]["params"]
        assert params == {"limit": 100}

    def test_limit_clamped_to_100(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/trade-history", [])
        a.get_trade_history(limit=500)
        params = next(c for c in http.calls if c[1] == "/trade-history")[2]["params"]
        assert params["limit"] == 100

    def test_forwards_start_and_end(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/trade-history", [])
        a.get_trade_history(limit=10, start="1700000000000", end="1700009999999")
        params = next(c for c in http.calls if c[1] == "/trade-history")[2]["params"]
        assert params == {"limit": 10, "startDate": "1700000000000", "endDate": "1700009999999"}

    def test_unwraps_data_key(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/trade-history", {"data": [{"tradeHistoryId": "t1"}]})
        assert a.get_trade_history() == [{"tradeHistoryId": "t1"}]


class TestMarketData:
    def test_get_all_markets(self, authed_adapter):
        a, http = authed_adapter
        http.add("GET", "/markets", {"markets": [{"id": "ETH-BTC"}], "total": 1})
        out = a.get_all_markets()
        assert out == [{"id": "ETH-BTC"}]

    def test_snapshot_raises(self, authed_adapter):
        a, _ = authed_adapter
        with pytest.raises(NotImplementedError):
            a.get_snapshot("ETH-BTC")

    def test_candles_raises(self, authed_adapter):
        a, _ = authed_adapter
        with pytest.raises(NotImplementedError):
            a.get_candles("ETH-BTC", "1m", 60_000)

    def test_mids_raises(self, authed_adapter):
        a, _ = authed_adapter
        with pytest.raises(NotImplementedError):
            a.get_all_mids()


class TestTriggerOrders:
    def test_place_trigger(self, authed_adapter):
        a, http = authed_adapter
        http.add("POST", "/positions", {"orderId": "trig-1"})
        oid = a.place_trigger_order("ETH-BTC", "buy", 1.0, 60000.0)
        assert oid == "trig-1"
        body = http.calls[-1][2]["json"]
        assert body["executionType"] == "TRIGGER"
        assert body["triggerType"] == "PRICE"
        assert body["triggerValue"] == 60000.0
        assert body["direction"] == "MORE_THAN"

    def test_trigger_sell_uses_less_than(self, authed_adapter):
        a, http = authed_adapter
        http.add("POST", "/positions", {"orderId": "trig-2"})
        a.place_trigger_order("ETH-BTC", "sell", 1.0, 60000.0)
        assert http.calls[-1][2]["json"]["direction"] == "LESS_THAN"

    def test_cancel_trigger_delegates(self, authed_adapter):
        a, http = authed_adapter
        http.add("DELETE", "/orders/trig-1/cancel", {"status": "CANCELLED"})
        assert a.cancel_trigger_order("ETH-BTC", "trig-1") is True


class TestPositionResponseToFill:
    def test_aggregates_multiple_fills(self):
        fill = _position_response_to_fill(
            {"orderId": "x", "fills": [
                {"size": 1.0, "price": 100.0, "externalFeePaid": 0.1, "fillTime": 1000},
                {"size": 2.0, "price": 110.0, "externalFeePaid": 0.2, "fillTime": 2000},
            ]},
            "ETH-BTC",
            "buy",
        )
        assert fill is not None
        assert fill.quantity == 3.0
        assert fill.price == pytest.approx((100 + 220) / 3.0)
        assert fill.fee == pytest.approx(0.3)
        assert fill.timestamp_ms == 2000

    def test_no_fills_returns_none(self):
        assert _position_response_to_fill({"orderId": "x", "fills": []}, "ETH-BTC", "buy") is None
