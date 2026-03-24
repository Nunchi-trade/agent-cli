"""Ladder builder: generate multi-level bid/ask quotes from FV + half-spread.

Level i:
    bid_price = FV_skewed - (h + i * delta)
    ask_price = FV_skewed + (h + i * delta)
    bid_size  = S0 * exp(-lambda * i) * bid_size_mult
    ask_size  = S0 * exp(-lambda * i) * ask_size_mult
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from quoting_engine.config import LadderParams


@dataclass
class LadderLevel:
    """Single level in the quote ladder."""
    level: int
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float


class LadderBuilder:
    """Build a multi-level quote ladder around a skewed fair value."""

    def __init__(self, params: LadderParams, tick_size: float = 0.01):
        self._p = params
        self._tick_size = tick_size

    def build(
        self,
        fv: float,
        half_spread: float,
        mid: float,
        bid_size_mult: float = 1.0,
        ask_size_mult: float = 1.0,
        num_levels_override: Optional[int] = None,
    ) -> List[LadderLevel]:
        """Generate the quote ladder.

        Args:
            fv: skewed fair value (already inventory-adjusted).
            half_spread: half-spread in price units from SpreadCalculator.
            mid: reference mid for delta_bps conversion.
            bid_size_mult: multiplicative size skew for bids.
            ask_size_mult: multiplicative size skew for asks.
            num_levels_override: if set, overrides configured num_levels.

        Returns:
            List of LadderLevel, one per configured level.
        """
        if fv <= 0 or mid <= 0:
            return []

        delta = self._p.delta_bps * mid / 10_000
        levels: List[LadderLevel] = []
        num_levels = num_levels_override if num_levels_override is not None else self._p.num_levels

        for i in range(num_levels):
            offset = half_spread + i * delta
            base_size = self._p.s0 * math.exp(-self._p.lam * i)
            # Smooth depth: enforce minimum size at outer levels
            min_size = self._p.min_size_ratio * self._p.s0
            base_size = max(base_size, min_size)

            bid_price = self._round_to_tick(fv - offset)
            ask_price = self._round_to_tick(fv + offset)
            bid_size = round(base_size * bid_size_mult, 6)
            ask_size = round(base_size * ask_size_mult, 6)

            if bid_size <= 0 and ask_size <= 0:
                continue

            levels.append(LadderLevel(
                level=i,
                bid_price=bid_price,
                ask_price=ask_price,
                bid_size=max(0.0, bid_size),
                ask_size=max(0.0, ask_size),
            ))

        return levels

    def _round_to_tick(self, price: float) -> float:
        """Round price to nearest tick size."""
        if self._tick_size <= 0:
            return round(price, 8)
        return round(round(price / self._tick_size) * self._tick_size, 8)
