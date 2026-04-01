"""Market→Strategy mapping."""
from __future__ import annotations

from typing import Dict, List

MARKET_STRATEGY_MAP: Dict[str, List[str]] = {
    "VXX-USDYP": ["mean_reversion", "simplified_ensemble"],
    "BTCSWP-USDYP": ["funding_arb", "funding_momentum", "basis_arb"],
    "US3M-USDYP": ["trend_follower", "simplified_ensemble"],
}


def get_strategies_for_market(instrument: str) -> List[str]:
    return MARKET_STRATEGY_MAP.get(instrument, [])


def has_strategy_mapping(instruments: List[str]) -> bool:
    return any(inst in MARKET_STRATEGY_MAP for inst in instruments)
