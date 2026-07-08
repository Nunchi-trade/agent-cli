"""Tests for VenueAdapter interface and implementations."""
from __future__ import annotations

import pytest

from common.venue_adapter import Fill, VenueAdapter, VenueCapabilities


class TestFill:
    def test_creation(self):
        fill = Fill(oid="123", instrument="ETH-PERP", side="buy",
                    price=2500.0, quantity=1.0, timestamp_ms=1000)
        assert fill.oid == "123"
        assert fill.fee == 0.0

    def test_with_fee(self):
        fill = Fill(oid="456", instrument="BTC-PERP", side="sell",
                    price=60000.0, quantity=0.1, timestamp_ms=2000, fee=1.5)
        assert fill.fee == 1.5


class TestVenueCapabilities:
    def test_defaults_all_false(self):
        caps = VenueCapabilities()
        assert not caps.supports_alo
        assert not caps.supports_trigger_orders
        assert not caps.supports_builder_fee
        assert not caps.supports_cross_margin

    def test_hl_capabilities(self):
        caps = VenueCapabilities(
            supports_alo=True, supports_trigger_orders=True,
            supports_builder_fee=True, supports_cross_margin=True,
        )
        assert caps.supports_alo
        assert caps.supports_trigger_orders


class TestVenueAdapterABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            VenueAdapter()

    def test_trigger_order_default_raises(self):
        class MinimalAdapter(VenueAdapter):
            def connect(self, private_key, testnet=True): pass
            def capabilities(self): return VenueCapabilities()
            def get_snapshot(self, instrument): pass
            def get_candles(self, coin, interval, lookback_ms): return []
            def get_all_markets(self): return []
            def get_all_mids(self): return {}
            def place_order(self, instrument, side, size, price, tif="Ioc", builder=None): return None
            def cancel_order(self, instrument, oid): return False
            def get_open_orders(self, instrument=""): return []
            def get_account_state(self): return {}
            def set_leverage(self, leverage, coin, is_cross=True): pass

        adapter = MinimalAdapter()
        with pytest.raises(NotImplementedError):
            adapter.place_trigger_order("ETH-PERP", "sell", 1.0, 2400.0)
        with pytest.raises(NotImplementedError):
            adapter.cancel_trigger_order("ETH-PERP", "abc")


class TestHLVenueAdapter:
    def test_import_and_interface(self):
        from adapters.hl_adapter import HLVenueAdapter
        assert issubclass(HLVenueAdapter, VenueAdapter)
        required = {
            'connect', 'capabilities', 'get_snapshot', 'get_candles',
            'get_all_markets', 'get_all_mids', 'place_order', 'cancel_order',
            'get_open_orders', 'get_account_state', 'set_leverage',
        }
        for m in required:
            assert m in dir(HLVenueAdapter), f"Missing: {m}"


class TestMockVenueAdapter:
    def test_import_and_instantiate(self):
        from adapters.mock_adapter import MockVenueAdapter
        adapter = MockVenueAdapter()
        assert isinstance(adapter, VenueAdapter)

    def test_capabilities(self):
        from adapters.mock_adapter import MockVenueAdapter
        caps = MockVenueAdapter().capabilities()
        assert caps.supports_alo
        assert caps.supports_trigger_orders

    def test_get_snapshot(self):
        from adapters.mock_adapter import MockVenueAdapter
        snap = MockVenueAdapter().get_snapshot("ETH-PERP")
        assert snap.instrument == "ETH-PERP"
        assert snap.mid_price > 0

    def test_place_order_returns_fill(self):
        from adapters.mock_adapter import MockVenueAdapter
        fill = MockVenueAdapter().place_order("ETH-PERP", "buy", 1.0, 2500.0)
        assert fill is not None
        assert isinstance(fill, Fill)
        assert fill.side == "buy"

    def test_cancel_order(self):
        from adapters.mock_adapter import MockVenueAdapter
        assert MockVenueAdapter().cancel_order("ETH-PERP", "fake") is True

    def test_get_all_mids(self):
        from adapters.mock_adapter import MockVenueAdapter
        assert isinstance(MockVenueAdapter().get_all_mids(), dict)
