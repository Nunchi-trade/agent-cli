"""Inventory skew: adjust prices and/or sizes based on current position.

Price skew: FV_skewed = FV - k_inv * (inv / inv_limit) * sigma
  - Positive inventory (long) -> FV shifts DOWN (encourage selling)
  - Negative inventory (short) -> FV shifts UP (encourage buying)

Size skew: asymmetric sizing
  - When long: reduce bid sizes, increase ask sizes
  - When short: increase bid sizes, reduce ask sizes

Soft/hard inventory caps (G5):
  - Below soft_cap: normal operation
  - Between soft and hard: reduce-only, periodic micro-clip unwinds
  - Above hard_cap: halt, aggressive unwind
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from quoting_engine.config import SkewParams


class InventorySkewer:
    """Apply inventory-aware skew to fair value and/or order sizes."""

    def __init__(self, params: SkewParams):
        self._p = params

    def price_skew(self, fv: float, inventory: float, sigma_price: float) -> float:
        """Return the inventory-adjusted fair value.

        Args:
            fv: unadjusted fair value.
            inventory: signed net position (positive = long).
            sigma_price: rolling vol in price units.

        Returns:
            Skewed fair value.
        """
        if self._p.mode == "size":
            return fv  # no price adjustment in size-only mode

        if self._p.inv_limit <= 0:
            return fv

        # Negative sign: long inventory -> lower FV to attract sells
        skew = -self._p.k_inv * (inventory / self._p.inv_limit) * sigma_price
        return fv + skew

    def size_skew(
        self,
        base_bid_size: float,
        base_ask_size: float,
        inventory: float,
    ) -> Tuple[float, float]:
        """Return (skewed_bid_size, skewed_ask_size).

        When long: shrink bids, grow asks (want to sell more, buy less).
        When short: grow bids, shrink asks.
        """
        if self._p.mode == "price":
            return base_bid_size, base_ask_size

        if self._p.inv_limit <= 0:
            return base_bid_size, base_ask_size

        # utilization in [-1, 1]
        util = max(-1.0, min(1.0, inventory / self._p.inv_limit))
        factor = self._p.size_skew_factor

        # When long (util > 0): reduce bids, increase asks
        bid_mult = max(0.0, 1.0 - factor * util)
        ask_mult = max(0.0, 1.0 + factor * util)

        return (
            round(base_bid_size * bid_mult, 6),
            round(base_ask_size * ask_mult, 6),
        )

    def effective_limit(self) -> float:
        """Return effective inventory limit (hard_cap if set, else inv_limit)."""
        if self._p.hard_cap > 0:
            return self._p.hard_cap
        return self._p.inv_limit

    def inventory_state(self, inventory: float) -> str:
        """Return 'normal', 'soft_breach', or 'hard_breach'."""
        abs_inv = abs(inventory)
        hard = self._p.hard_cap if self._p.hard_cap > 0 else self._p.inv_limit
        soft = self._p.soft_cap if self._p.soft_cap > 0 else hard
        if abs_inv >= hard:
            return "hard_breach"
        elif abs_inv >= soft:
            return "soft_breach"
        return "normal"

    def micro_clip_order(self, inventory: float, tick_count: int) -> Optional[Dict[str, object]]:
        """If in soft_breach and interval elapsed, return a clip order dict."""
        if self._p.micro_clip_size <= 0:
            return None
        if self.inventory_state(inventory) != "soft_breach":
            return None
        if tick_count % self._p.micro_clip_interval != 0:
            return None
        side = "sell" if inventory > 0 else "buy"
        size = min(self._p.micro_clip_size, abs(inventory))
        return {"side": side, "size": size}
