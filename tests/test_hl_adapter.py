"""Tests for cli/hl_adapter.py — DirectHLProxy adapter."""
import math
import pytest
import time
from decimal import Decimal
from unittest.mock import MagicMock, PropertyMock, patch

from parent.hl_proxy import HLFill, MockHLProxy
from common.models import MarketSnapshot
from cli.hl_adapter import (
    DirectHLProxy,
    DirectMockProxy,
    APICircuitBreakerOpen,
    SLIPPAGE_FACTOR,
    SIG_FIGS,
    CIRCUIT_BREAKER_THRESHOLD,
    MAX_RATE_LIMIT_RETRIES,
    _to_hl_coin,
    _validate_builder_info,
)


# ---- Helpers ----

def _mock_hl_proxy():
    """Create a mock HLProxy with the attributes DirectHLProxy expects."""
    hl = MagicMock()
    hl._info = MagicMock()
    hl._exchange = MagicMock()
    hl._address = "0xTEST"
    hl._ensure_client = MagicMock()
    hl.get_snapshot = MagicMock(return_value=MarketSnapshot(
        instrument="ETH-PERP",
        mid_price=2500.0,
        bid=2499.5,
        ask=2500.5,
        spread_bps=4.0,
        timestamp_ms=int(time.time() * 1000),
    ))
    return hl


def _make_proxy():
    """Create a DirectHLProxy with mock internals."""
    hl = _mock_hl_proxy()
    return DirectHLProxy(hl)


# ---- Tests ----

class TestCoinMapping:
    def test_standard_perp(self):
        assert _to_hl_coin("ETH-PERP") == "ETH"
        assert _to_hl_coin("BTC-PERP") == "BTC"

    def test_lowercase_perp(self):
        assert _to_hl_coin("sol-perp") == "sol"


class TestRoundPrice:
    def test_btc_price(self):
        proxy = _make_proxy()
        # BTC at 60000: tick = 1.0 (5 sig figs → magnitude 4, tick = 10^(4-5+1) = 1.0)
        result = proxy._round_price(60123.4, "BTC")
        assert result == 60123.0

    def test_eth_price(self):
        proxy = _make_proxy()
        # ETH at 2500: tick = 0.1 (5 sig figs → magnitude 3, tick = 10^(3-5+1) = 0.1)
        result = proxy._round_price(2500.37, "ETH")
        assert result == 2500.4

    def test_small_price(self):
        proxy = _make_proxy()
        # DOGE at 0.15: magnitude = -1, tick = 10^(-1-5+1) = 1e-5
        result = proxy._round_price(0.15123, "DOGE")
        # Should be rounded to nearest tick (1e-5)
        assert abs(result - 0.15123) < 1e-4  # within one tick

    def test_zero_price_returns_default(self):
        proxy = _make_proxy()
        result = proxy._round_price(0.0, "ETH")
        assert result == 0.0

    def test_negative_price_returns_default(self):
        proxy = _make_proxy()
        result = proxy._round_price(-100.0, "ETH")
        # tick defaults to 0.1 for price <= 0
        assert result == -100.0


class TestGetSzDecimals:
    def test_returns_from_meta(self):
        proxy = _make_proxy()
        proxy._info.meta.return_value = {
            "universe": [
                {"name": "BTC", "szDecimals": 3},
                {"name": "ETH", "szDecimals": 4},
            ]
        }
        assert proxy._get_sz_decimals("BTC") == 3
        assert proxy._get_sz_decimals("ETH") == 4

    def test_returns_default_on_unknown(self):
        proxy = _make_proxy()
        proxy._info.meta.return_value = {"universe": []}
        assert proxy._get_sz_decimals("UNKNOWN") == 1

    def test_returns_default_on_meta_failure(self):
        proxy = _make_proxy()
        proxy._info.meta.side_effect = Exception("network")
        assert proxy._get_sz_decimals("ETH") == 1


class TestPlaceOrder:
    def test_filled_order_returns_fill(self):
        proxy = _make_proxy()
        proxy._exchange.order.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [
                        {"filled": {"oid": "123", "avgPx": "2500.0", "totalSz": "1.0"}}
                    ]
                }
            }
        }
        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0, tif="Gtc")
        assert fill is not None
        assert fill.oid == "123"
        assert fill.side == "buy"
        assert fill.price == Decimal("2500.0")

    def test_rejected_order_returns_none(self):
        proxy = _make_proxy()
        proxy._exchange.order.return_value = {
            "status": "err",
            "response": "Insufficient margin"
        }
        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0)
        assert fill is None

    def test_resting_order_returns_none(self):
        proxy = _make_proxy()
        proxy._exchange.order.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [
                        {"resting": {"oid": "456"}}
                    ]
                }
            }
        }
        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0, tif="Gtc")
        assert fill is None

    def test_error_status_returns_none(self):
        proxy = _make_proxy()
        proxy._exchange.order.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [{"error": "Cross would make position too large"}]
                }
            }
        }
        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0)
        assert fill is None

    def test_string_status_returns_none(self):
        proxy = _make_proxy()
        proxy._exchange.order.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": ["WaitingForFill"]}
            }
        }
        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0)
        assert fill is None

    def test_empty_statuses_returns_none(self):
        proxy = _make_proxy()
        proxy._exchange.order.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": []}
            }
        }
        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0)
        assert fill is None

    def test_invalid_builder_metadata_fails_before_exchange_order(self):
        proxy = _make_proxy()
        with pytest.raises(RuntimeError, match="builder-code validation failed"):
            proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0, builder={"b": "", "f": 0})
        proxy._exchange.order.assert_not_called()

    def test_validate_builder_info_accepts_hl_builder_shape(self):
        builder = {"b": "0x0000000000000000000000000000000000000001", "f": 10}
        assert _validate_builder_info(builder) == builder


class TestALOFallback:
    def test_alo_rejection_falls_back_to_gtc(self):
        proxy = _make_proxy()
        call_count = [0]

        def mock_order(coin, is_buy, sz, price, order_type, builder=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call (ALO) — rejected
                return {
                    "status": "ok",
                    "response": {"type": "order", "data": {"statuses": [{"error": "Would cross"}]}}
                }
            else:
                # Second call (Gtc fallback) — filled
                return {
                    "status": "ok",
                    "response": {"type": "order", "data": {"statuses": [
                        {"filled": {"oid": "789", "avgPx": "2500.0", "totalSz": "1.0"}}
                    ]}}
                }

        proxy._exchange.order = mock_order
        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0, tif="Alo")
        assert fill is not None
        assert call_count[0] == 2


class TestIOCSlippage:
    def test_buy_ioc_pushes_above_ask(self):
        proxy = _make_proxy()
        # Track what price gets passed to _send_order
        original_send = proxy._send_order
        sent_prices = []

        def spy_send(coin, instrument, side, is_buy, size, price, tif, builder):
            sent_prices.append(price)
            return None

        proxy._send_order = spy_send
        proxy.place_order("ETH-PERP", "buy", 1.0, 2400.0, tif="Ioc")

        # Price should be pushed up to ask * SLIPPAGE_FACTOR
        assert len(sent_prices) == 1
        assert sent_prices[0] >= 2500.5 * 0.999  # roughly at ask * slippage

    def test_sell_ioc_pushes_below_bid(self):
        proxy = _make_proxy()
        sent_prices = []

        def spy_send(coin, instrument, side, is_buy, size, price, tif, builder):
            sent_prices.append(price)
            return None

        proxy._send_order = spy_send
        proxy.place_order("ETH-PERP", "sell", 1.0, 2600.0, tif="Ioc")

        assert len(sent_prices) == 1
        assert sent_prices[0] <= 2499.5 * 1.001  # roughly at bid * (2-slippage)


class TestRateLimitRetry:
    def test_retries_on_429(self):
        proxy = _make_proxy()
        call_count = [0]

        def mock_order(coin, is_buy, sz, price, order_type, builder=None):
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("429 Too Many Requests")
            return {
                "status": "ok",
                "response": {"type": "order", "data": {"statuses": [
                    {"filled": {"oid": "999", "avgPx": "2500.0", "totalSz": "1.0"}}
                ]}}
            }

        proxy._exchange.order = mock_order
        with patch("time.sleep"):  # skip actual sleeping
            fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0, tif="Gtc")

        assert fill is not None
        assert call_count[0] == 3

    def test_raises_after_max_retries(self):
        proxy = _make_proxy()

        def always_429(coin, is_buy, sz, price, order_type, builder=None):
            raise Exception("429 Too Many Requests")

        proxy._exchange.order = always_429
        with patch("time.sleep"):
            fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2500.0, tif="Gtc")
        # Should return None (exception caught in outer handler)
        assert fill is None


class TestCircuitBreaker:
    def test_trips_after_threshold_failures(self):
        proxy = _make_proxy()
        # Simulate consecutive API failures
        proxy._hl.get_snapshot = MagicMock(side_effect=Exception("timeout"))

        for i in range(CIRCUIT_BREAKER_THRESHOLD - 1):
            snap = proxy.get_snapshot("ETH-PERP")
            assert snap.mid_price == 0.0  # default empty snapshot

        # Next call should trip the circuit breaker
        with pytest.raises(APICircuitBreakerOpen):
            proxy.get_snapshot("ETH-PERP")

    def test_resets_on_success(self):
        proxy = _make_proxy()
        # Fail a few times
        proxy._hl.get_snapshot = MagicMock(side_effect=Exception("fail"))
        for _ in range(3):
            proxy.get_snapshot("ETH-PERP")

        assert proxy._api_failure_count == 3

        # Now succeed
        proxy._hl.get_snapshot = MagicMock(return_value=MarketSnapshot(
            instrument="ETH-PERP", mid_price=2500.0,
            bid=2499.5, ask=2500.5,
            timestamp_ms=int(time.time() * 1000),
        ))
        snap = proxy.get_snapshot("ETH-PERP")
        assert snap.mid_price == 2500.0
        assert proxy._api_failure_count == 0

    def test_stays_open_once_tripped(self):
        proxy = _make_proxy()
        proxy._api_failure_count = CIRCUIT_BREAKER_THRESHOLD

        with pytest.raises(APICircuitBreakerOpen):
            proxy.get_snapshot("ETH-PERP")

    def test_can_manually_reset(self):
        proxy = _make_proxy()
        proxy._api_failure_count = CIRCUIT_BREAKER_THRESHOLD

        # Manual reset
        proxy._api_failure_count = 0
        proxy._hl.get_snapshot = MagicMock(return_value=MarketSnapshot(
            instrument="ETH-PERP", mid_price=2500.0,
            bid=2499.5, ask=2500.5,
            timestamp_ms=int(time.time() * 1000),
        ))
        snap = proxy.get_snapshot("ETH-PERP")
        assert snap.mid_price == 2500.0


class TestGetAccountState:
    def test_returns_account_info(self):
        proxy = _make_proxy()
        proxy._info.user_state.return_value = {
            "marginSummary": {
                "accountValue": "10000",
                "totalMarginUsed": "500",
            },
            "withdrawable": "9500",
            "assetPositions": [],
        }
        state = proxy.get_account_state()
        assert state["account_value"] == 10000.0
        assert state["total_margin"] == 500.0
        assert state["address"] == "0xTEST"

    def test_handles_sdk_index_error(self):
        proxy = _make_proxy()
        proxy._info.user_state.side_effect = IndexError("out of bounds")
        # Fallback also fails
        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "marginSummary": {"accountValue": "5000", "totalMarginUsed": "0"},
                "withdrawable": "5000",
                "assetPositions": [],
            }
            proxy._info.base_url = "https://test.api"
            state = proxy.get_account_state()
            assert state["account_value"] == 5000.0

    def test_returns_empty_on_total_failure(self):
        proxy = _make_proxy()
        proxy._info.user_state.side_effect = Exception("dead")
        state = proxy.get_account_state()
        assert state == {}


class TestDirectMockProxy:
    def test_place_order_always_fills(self):
        mock = DirectMockProxy()
        fill = mock.place_order("ETH-PERP", "buy", 1.0, 2500.0)
        assert fill is not None
        assert fill.side == "buy"

    def test_get_snapshot_returns_data(self):
        mock = DirectMockProxy()
        snap = mock.get_snapshot("ETH-PERP")
        assert snap.mid_price > 0

    def test_get_account_state(self):
        mock = DirectMockProxy()
        state = mock.get_account_state()
        assert state["account_value"] == 100000.0

    def test_trigger_orders(self):
        mock = DirectMockProxy()
        oid = mock.place_trigger_order("ETH-PERP", "sell", 1.0, 2400.0)
        assert oid is not None
        assert mock.cancel_trigger_order("ETH-PERP", oid) is True
        assert mock.cancel_trigger_order("ETH-PERP", "nonexistent") is False


class TestConstants:
    def test_slippage_factor(self):
        assert SLIPPAGE_FACTOR == 1.005

    def test_sig_figs(self):
        assert SIG_FIGS == 5

    def test_circuit_breaker_threshold(self):
        assert CIRCUIT_BREAKER_THRESHOLD == 5

    def test_max_retries(self):
        assert MAX_RATE_LIMIT_RETRIES == 3
