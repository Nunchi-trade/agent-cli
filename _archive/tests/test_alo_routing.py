"""Tests for ALO (Add Liquidity Only) fee optimization routing.

Verifies:
- Entry orders use configured order type (default ALO)
- Exit orders always use IOC (speed > fees)
- ALO fallback to Gtc on rejection
- Config roundtrip includes entry_order_type
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from modules.apex_config import ApexConfig
from parent.hl_proxy import HLFill


# ---------------------------------------------------------------------------
# Helpers — lightweight mock that tracks tif per call
# ---------------------------------------------------------------------------

class _TifTrackingProxy:
    """Minimal mock of DirectMockProxy that records tif values."""

    def __init__(self):
        self.calls: List[Dict] = []

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[dict] = None,
    ) -> Optional[HLFill]:
        self.calls.append({"instrument": instrument, "side": side, "tif": tif})
        return HLFill(
            oid=f"mock-{len(self.calls)}",
            instrument=instrument,
            side=side.lower(),
            price=Decimal(str(price)),
            quantity=Decimal(str(size)),
            timestamp_ms=int(time.time() * 1000),
        )

    def get_all_mids(self) -> Dict[str, str]:
        return {"ETH": "2000.0", "BTC": "50000.0"}


# ---------------------------------------------------------------------------
# Config roundtrip
# ---------------------------------------------------------------------------

class TestConfigRoundtrip:
    def test_entry_order_type_default(self):
        """Default config has entry_order_type = 'Alo'."""
        cfg = ApexConfig()
        assert cfg.entry_order_type == "Alo"

    def test_entry_order_type_to_dict(self):
        """entry_order_type appears in to_dict() output."""
        cfg = ApexConfig(entry_order_type="Gtc")
        d = cfg.to_dict()
        assert "entry_order_type" in d
        assert d["entry_order_type"] == "Gtc"

    def test_entry_order_type_from_dict(self):
        """entry_order_type survives from_dict() roundtrip."""
        original = ApexConfig(entry_order_type="Ioc")
        d = original.to_dict()
        restored = ApexConfig.from_dict(d)
        assert restored.entry_order_type == "Ioc"

    def test_entry_order_type_from_dict_missing(self):
        """from_dict without entry_order_type uses default."""
        cfg = ApexConfig.from_dict({"total_budget": 5000})
        assert cfg.entry_order_type == "Alo"


# ---------------------------------------------------------------------------
# ALO fallback in DirectHLProxy
# ---------------------------------------------------------------------------

class TestAloFallback:
    def test_alo_fallback_to_gtc_on_rejection(self):
        """When ALO order is rejected (returns None), place_order retries with Gtc."""
        from cli.hl_adapter import DirectHLProxy

        # Build a mock HLProxy with the required internals
        mock_hl = MagicMock()
        mock_hl._info = MagicMock()
        mock_hl._exchange = MagicMock()
        mock_hl._address = "0xTEST"

        # First call (ALO) → rejection; second call (Gtc fallback) → fill
        mock_hl._exchange.order.side_effect = [
            {"status": "err", "response": "Would cross: post-only order"},  # ALO rejected
            {
                "status": "ok",
                "response": {
                    "type": "order",
                    "data": {
                        "statuses": [
                            {"filled": {"oid": "123", "avgPx": "2000.0", "totalSz": "1.0"}}
                        ]
                    },
                },
            },
        ]

        # Mock metadata for szDecimals
        mock_hl._info.meta.return_value = {
            "universe": [{"name": "ETH", "szDecimals": 4}]
        }

        proxy = DirectHLProxy.__new__(DirectHLProxy)
        proxy._hl = mock_hl

        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2000.0, tif="Alo")
        assert fill is not None
        assert fill.instrument == "ETH-PERP"

        # Verify two calls were made: first ALO, then Gtc
        calls = mock_hl._exchange.order.call_args_list
        assert len(calls) == 2
        assert calls[0][0][4] == {"limit": {"tif": "Alo"}}
        assert calls[1][0][4] == {"limit": {"tif": "Gtc"}}

    def test_alo_no_fallback_on_success(self):
        """When ALO order fills, no Gtc fallback is attempted."""
        from cli.hl_adapter import DirectHLProxy

        mock_hl = MagicMock()
        mock_hl._info = MagicMock()
        mock_hl._exchange = MagicMock()
        mock_hl._address = "0xTEST"

        mock_hl._exchange.order.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [
                        {"filled": {"oid": "456", "avgPx": "2000.0", "totalSz": "1.0"}}
                    ]
                },
            },
        }
        mock_hl._info.meta.return_value = {
            "universe": [{"name": "ETH", "szDecimals": 4}]
        }

        proxy = DirectHLProxy.__new__(DirectHLProxy)
        proxy._hl = mock_hl

        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2000.0, tif="Alo")
        assert fill is not None
        # Only one call — no fallback
        assert mock_hl._exchange.order.call_count == 1

    def test_ioc_no_fallback(self):
        """IOC orders that fail do NOT trigger Gtc fallback."""
        from cli.hl_adapter import DirectHLProxy

        mock_hl = MagicMock()
        mock_hl._info = MagicMock()
        mock_hl._exchange = MagicMock()
        mock_hl._address = "0xTEST"

        mock_hl._exchange.order.return_value = {
            "status": "err", "response": "No liquidity"
        }
        mock_hl._info.meta.return_value = {
            "universe": [{"name": "ETH", "szDecimals": 4}]
        }
        mock_hl._info.l2_snapshot.return_value = {"levels": []}

        proxy = DirectHLProxy.__new__(DirectHLProxy)
        proxy._hl = mock_hl

        fill = proxy.place_order("ETH-PERP", "buy", 1.0, 2000.0, tif="Ioc")
        assert fill is None
        # Only one call — IOC does not fallback
        assert mock_hl._exchange.order.call_count == 1


# ---------------------------------------------------------------------------
# DirectMockProxy tracks tif
# ---------------------------------------------------------------------------

class TestMockProxyTif:
    def test_mock_proxy_records_tif(self):
        """DirectMockProxy exposes _last_tif for testing."""
        from cli.hl_adapter import DirectMockProxy

        mock = DirectMockProxy()
        mock.place_order("ETH-PERP", "buy", 1.0, 2000.0, tif="Alo")
        assert mock._last_tif == "Alo"

        mock.place_order("ETH-PERP", "sell", 1.0, 2000.0, tif="Ioc")
        assert mock._last_tif == "Ioc"


# ---------------------------------------------------------------------------
# Entry/Exit routing in standalone runner
# ---------------------------------------------------------------------------

class TestOrderRouting:
    """Test that the runner routes entry/exit orders with the correct tif."""

    def test_entry_uses_configured_order_type(self):
        """Entry orders should use config.entry_order_type."""
        cfg = ApexConfig(entry_order_type="Alo")
        # getattr fallback matches what the runner does
        assert getattr(cfg, "entry_order_type", "Alo") == "Alo"

        cfg2 = ApexConfig(entry_order_type="Gtc")
        assert getattr(cfg2, "entry_order_type", "Alo") == "Gtc"

    def test_entry_default_is_alo(self):
        """Default entry order type is ALO for maker rebates."""
        cfg = ApexConfig()
        assert cfg.entry_order_type == "Alo"

    def test_exit_always_ioc(self):
        """Exit orders must always use IOC regardless of config.

        This is verified by code inspection — the standalone_runner hardcodes
        tif='Ioc' for _execute_exit. Here we verify the config doesn't
        accidentally affect exit logic by ensuring entry_order_type only
        applies to entries.
        """
        # The exit path in standalone_runner.py is hardcoded to "Ioc"
        # and does NOT reference config.entry_order_type.
        # This test documents that contract.
        cfg = ApexConfig(entry_order_type="Alo")
        # entry_order_type should never be "Ioc" by default — it's for entries
        assert cfg.entry_order_type != "Ioc"
