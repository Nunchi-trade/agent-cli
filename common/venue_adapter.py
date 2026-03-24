"""Venue-agnostic adapter interface — decouples engine from any specific exchange.

All venue-specific code (Hyperliquid, mock, future venues) implements this ABC.
Strategies and the engine only depend on VenueAdapter + Fill, never on venue internals.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from common.models import MarketSnapshot


@dataclass
class VenueCapabilities:
    """Declares optional features a venue supports."""
    supports_alo: bool = False
    supports_trigger_orders: bool = False
    supports_builder_fee: bool = False
    supports_cross_margin: bool = False


@dataclass
class Fill:
    """Venue-agnostic trade fill."""
    oid: str
    instrument: str
    side: str
    price: float
    quantity: float
    timestamp_ms: int
    fee: float = 0.0


class VenueAdapter(ABC):
    """Unified venue interface for market data + execution."""

    @abstractmethod
    def connect(self, private_key: str, testnet: bool = True) -> None: ...

    @abstractmethod
    def capabilities(self) -> VenueCapabilities: ...

    # --- Market Data ---

    @abstractmethod
    def get_snapshot(self, instrument: str) -> MarketSnapshot: ...

    @abstractmethod
    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]: ...

    @abstractmethod
    def get_all_markets(self) -> list: ...

    @abstractmethod
    def get_all_mids(self) -> Dict[str, str]: ...

    # --- Execution ---

    @abstractmethod
    def place_order(self, instrument: str, side: str, size: float,
                    price: float, tif: str = "Ioc",
                    builder: Optional[dict] = None) -> Optional[Fill]: ...

    @abstractmethod
    def cancel_order(self, instrument: str, oid: str) -> bool: ...

    @abstractmethod
    def get_open_orders(self, instrument: str = "") -> List[Dict]: ...

    # --- Account ---

    @abstractmethod
    def get_account_state(self) -> Dict: ...

    @abstractmethod
    def set_leverage(self, leverage: int, coin: str, is_cross: bool = True) -> None: ...

    # --- Optional (check capabilities first) ---

    def place_trigger_order(self, instrument: str, side: str, size: float,
                            trigger_price: float,
                            builder: Optional[dict] = None) -> Optional[str]:
        raise NotImplementedError("Trigger orders not supported by this venue")

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        raise NotImplementedError("Trigger orders not supported by this venue")
