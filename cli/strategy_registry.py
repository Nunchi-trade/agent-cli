"""Maps short strategy names to module:class paths."""
from __future__ import annotations

from typing import Any, Dict

STRATEGY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "cfi_hedge": {
        "path": "strategies.cfi_hedge_agent:CfiHedgeAgent",
        "description": "CFI-v2 funding-cost hedge",
        "params": {
            "notional_trigger": 100000.0,
            "max_hedge_notional": 50000.0,
            "max_per_day": 5,
            "min_interval_seconds": 300,
        },
    },
}

# YEX market definitions — Nunchi HIP-3 yield perpetuals (testnet)
YEX_MARKETS: Dict[str, Dict[str, str]] = {
    "VXX-USDYP": {
        "hl_coin": "yex:VXX",
        "description": "Volatility index (VXX) yield perpetual",
    },
    "US3M-USDYP": {
        "hl_coin": "yex:US3M",
        "description": "US 3-month Treasury rate yield perpetual",
    },
    "BTCSWP-USDYP": {
        "hl_coin": "yex:BTCSWP",
        "description": "BTC interest rate swap yield perpetual (YEX testnet)",
    },
}


# OSRS swap perp (testnet HIP-3 dex, explicit instrument)
SWAP_MARKETS: Dict[str, Dict[str, str]] = {
    "BTCSWP-OSRS": {
        "hl_coin": "osrs:BTCSWP",
        "description": "BTC interest rate swap perp (OSRS testnet)",
    },
}

# Paragon mainnet HIP-3 markets
PARA_MARKETS: Dict[str, Dict[str, str]] = {
    "BTCSWP-PARA": {
        "hl_coin": "para:BTCSWP",
        "description": "BTC interest rate swap yield perpetual (Paragon mainnet)",
    },
}


def resolve_strategy_path(name_or_path: str) -> str:
    """Resolve a short name to a full module:class path.

    Accepts either a short name ('cfi_hedge') or
    a full path ('strategies.cfi_hedge_agent:CfiHedgeAgent').
    """
    if ":" in name_or_path:
        return name_or_path
    entry = STRATEGY_REGISTRY.get(name_or_path)
    if entry is None:
        available = ", ".join(sorted(STRATEGY_REGISTRY.keys()))
        raise ValueError(f"Unknown strategy '{name_or_path}'. Available: {available}")
    return entry["path"]


def resolve_instrument(name: str, mainnet: bool | None = None) -> str:
    """Resolve an instrument name to the canonical form."""
    from common.models import BTCSWP_ASSET, coin_to_instrument, is_mainnet

    normalized = name.strip()
    lower = normalized.lower()

    for markets in (PARA_MARKETS, SWAP_MARKETS, YEX_MARKETS):
        for name_key, info in markets.items():
            if lower == info["hl_coin"].lower():
                return name_key

    if lower.startswith("yex:") or lower.startswith("para:") or lower.startswith("osrs:"):
        return coin_to_instrument(normalized, mainnet=mainnet)

    if lower == BTCSWP_ASSET.lower():
        return "BTCSWP-PARA" if is_mainnet(mainnet) else "BTCSWP-USDYP"

    return normalized
