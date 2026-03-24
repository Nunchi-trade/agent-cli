"""Mock VenueAdapter — wraps existing DirectMockProxy for testing/dry-run.

No real exchange connection. All methods delegate to the existing mock classes
in cli/hl_adapter.py and parent/hl_proxy.py.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from cli.hl_adapter import DirectMockProxy
from common.models import MarketSnapshot
from common.venue_adapter import Fill, VenueAdapter, VenueCapabilities
from parent.hl_proxy import HLFill

log = logging.getLogger("adapters.mock")


def _hl_fill_to_fill(hf: HLFill) -> Fill:
    """Convert an HLFill (Decimal fields) to a venue-agnostic Fill (float fields)."""
    return Fill(
        oid=hf.oid,
        instrument=hf.instrument,
        side=hf.side,
        price=float(hf.price),
        quantity=float(hf.quantity),
        timestamp_ms=hf.timestamp_ms,
        fee=float(hf.fee),
    )


class MockVenueAdapter(VenueAdapter):
    """VenueAdapter backed by DirectMockProxy — for testing and dry-run."""

    def __init__(self, proxy: Optional[DirectMockProxy] = None):
        self._proxy = proxy or DirectMockProxy()

    # --- Connection ---

    def connect(self, private_key: str, testnet: bool = True) -> None:
        """No-op — mock needs no connection."""
        pass

    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            supports_alo=True,
            supports_trigger_orders=True,
            supports_builder_fee=True,
            supports_cross_margin=True,
        )

    # --- Market Data ---

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        return self._proxy.get_snapshot(instrument)

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        return self._proxy.get_candles(coin, interval, lookback_ms)

    def get_all_markets(self) -> list:
        return self._proxy.get_all_markets()

    def get_all_mids(self) -> Dict[str, str]:
        return self._proxy.get_all_mids()

    # --- Execution ---

    def place_order(self, instrument: str, side: str, size: float,
                    price: float, tif: str = "Ioc",
                    builder: Optional[dict] = None) -> Optional[Fill]:
        hf = self._proxy.place_order(instrument, side, size, price, tif, builder)
        return _hl_fill_to_fill(hf) if hf is not None else None

    def cancel_order(self, instrument: str, oid: str) -> bool:
        return self._proxy.cancel_order(instrument, oid)

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        return self._proxy.get_open_orders(instrument)

    # --- Account ---

    def get_account_state(self) -> Dict:
        return self._proxy.get_account_state()

    def set_leverage(self, leverage: int, coin: str, is_cross: bool = True) -> None:
        """No-op — mock has no leverage concept."""
        pass

    # --- Optional: Trigger Orders ---

    def place_trigger_order(self, instrument: str, side: str, size: float,
                            trigger_price: float,
                            builder: Optional[dict] = None) -> Optional[str]:
        # DirectMockProxy.place_trigger_order doesn't accept builder param
        return self._proxy.place_trigger_order(instrument, side, size, trigger_price)

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        return self._proxy.cancel_trigger_order(instrument, oid)
