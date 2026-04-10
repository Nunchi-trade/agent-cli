"""Venue adapter factory for CLI commands.

Centralizes venue selection so command handlers don't instantiate Hyperliquid
classes directly. This is the transition point from HL-only wiring to
multi-venue CLI/config support.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from common.venue_adapter import VenueAdapter

SUPPORTED_VENUES = ("hl", "paradex")


def normalize_venue(venue: str) -> str:
    normalized = (venue or "hl").strip().lower()
    aliases = {
        "hyperliquid": "hl",
        "hyper-liquid": "hl",
        "pdx": "paradex",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_VENUES:
        raise ValueError(
            f"Unsupported venue '{venue}'. Supported venues: {', '.join(SUPPORTED_VENUES)}"
        )
    return normalized


def build_venue_adapter(*, venue: str, mainnet: bool = False, mock: bool = False) -> Tuple[VenueAdapter, str]:
    """Build a venue adapter for CLI execution.

    Returns `(adapter, mode_label)` where mode_label is human-readable for CLI
    output, e.g. `MOCK` or `LIVE (testnet)`.
    """
    normalized = normalize_venue(venue)

    if mock:
        from adapters.mock_adapter import MockVenueAdapter

        return MockVenueAdapter(), "MOCK"

    if normalized == "hl":
        from adapters.hl_adapter import HLVenueAdapter
        from cli.config import TradingConfig
        from cli.hl_adapter import DirectHLProxy
        from parent.hl_proxy import HLProxy

        cfg = TradingConfig(venue=normalized)
        private_key = cfg.get_private_key()
        raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
        adapter = HLVenueAdapter(DirectHLProxy(raw_hl))
        network = "mainnet" if mainnet else "testnet"
        return adapter, f"LIVE ({network})"

    if normalized == "paradex":
        from adapters.paradex_adapter import ParadexVenueAdapter
        from cli.config import TradingConfig
        from common.credentials import resolve_wallet_address
        from parent.paradex_proxy import ParadexProxy

        cfg = TradingConfig(venue=normalized)
        private_key = cfg.get_private_key()
        address = resolve_wallet_address("paradex")
        proxy = ParadexProxy(l2_private_key=private_key, l2_address=address, testnet=not mainnet)
        proxy.connect()
        adapter = ParadexVenueAdapter(proxy)
        network = "mainnet" if mainnet else "testnet"
        return adapter, f"LIVE ({network})"

    raise AssertionError(f"Unhandled venue: {normalized}")
