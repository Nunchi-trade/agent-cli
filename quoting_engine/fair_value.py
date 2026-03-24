"""Fair value calculation: weighted combination of price signals.

FV = w_oracle * Oracle
   + w_external * External_ref
   + w_microprice * Microprice
   + w_inventory * Inventory_term
"""
from __future__ import annotations

from typing import Optional

from quoting_engine.config import FairValueWeights


class FairValueCalculator:
    """Compute composite fair value from multiple price signals.

    Phase 1:
      - oracle = snapshot.mid_price (HIP-3 oracle)
      - external_ref = oracle (stub — falls back when 0)
      - microprice = book-imbalance-adjusted mid from bid/ask
      - inventory_term = passed in (computed externally by InventorySkewer)
    """

    def __init__(self, weights: FairValueWeights):
        self._w = weights

    def compute(
        self,
        oracle_price: float,
        bid: float,
        ask: float,
        external_ref: float = 0.0,
        inventory_term: float = 0.0,
        microprice_override: Optional[float] = None,
        oracle_weight_override: Optional[float] = None,
    ) -> float:
        """Return the blended fair value.

        Args:
            oracle_price: HIP-3 oracle mid price.
            bid: best bid from snapshot.
            ask: best ask from snapshot.
            external_ref: external reference price (pass 0 to use oracle).
            inventory_term: pre-computed inventory adjustment in price units.
            microprice_override: if provided and > 0, use this instead of
                the bid/ask imbalance proxy (e.g. from L2 depth calculator).
            oracle_weight_override: if provided, override the oracle weight
                (remaining weight distributed proportionally among other signals).

        Returns:
            Blended fair value as float.
        """
        if oracle_price <= 0:
            return 0.0

        # External ref defaults to oracle when 0
        if external_ref <= 0:
            external_ref = oracle_price

        # Microprice: use override from L2 calculator if available,
        # otherwise fall back to book-imbalance proxy.
        if microprice_override is not None and microprice_override > 0:
            microprice = microprice_override
        else:
            # Without L2 depth, use oracle position relative to bid/ask as proxy.
            spread = ask - bid
            if spread > 0 and bid > 0 and ask > 0:
                bid_weight = (ask - oracle_price) / spread
                ask_weight = (oracle_price - bid) / spread
                bid_weight = max(0.0, min(1.0, bid_weight))
                ask_weight = max(0.0, min(1.0, ask_weight))
                microprice = bid * ask_weight + ask * bid_weight
            else:
                microprice = oracle_price

        if oracle_weight_override is not None:
            # Redistribute remaining weight proportionally among other signals
            w_oracle = oracle_weight_override
            remaining = 1.0 - w_oracle
            other_total = self._w.w_external + self._w.w_microprice + self._w.w_inventory
            if other_total > 0:
                scale = remaining / other_total
                w_ext = self._w.w_external * scale
                w_micro = self._w.w_microprice * scale
                w_inv = self._w.w_inventory * scale
            else:
                w_ext = 0.0
                w_micro = 0.0
                w_inv = 0.0
            fv = (
                w_oracle * oracle_price
                + w_ext * external_ref
                + w_micro * microprice
                + w_inv * inventory_term
            )
        else:
            fv = (
                self._w.w_oracle * oracle_price
                + self._w.w_external * external_ref
                + self._w.w_microprice * microprice
                + self._w.w_inventory * inventory_term
            )
        return fv
