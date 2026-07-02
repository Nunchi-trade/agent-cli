"""Market→Strategy mapping."""
from __future__ import annotations

from typing import Dict, List

MARKET_STRATEGY_MAP: Dict[str, List[str]] = {
    "BTCSWP-USDYP": ["cfi_hedge"],
    "BTCSWP-OSRS": ["cfi_hedge"],
    "BTCSWP-PARA": ["cfi_hedge"],
}


def get_strategies_for_market(instrument: str) -> List[str]:
    return MARKET_STRATEGY_MAP.get(instrument, [])


def has_strategy_mapping(instruments: List[str]) -> bool:
    return any(inst in MARKET_STRATEGY_MAP for inst in instruments)
