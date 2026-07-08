"""L2 depth-weighted microprice calculator.

Computes a volume-weighted microprice from the top levels of an L2
order book, providing a more accurate fair-value signal than the
simple (bid + ask) / 2 midpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class L2Book:
    """L2 order book snapshot.

    bids: list of (price, size) sorted descending by price (best bid first).
    asks: list of (price, size) sorted ascending by price (best ask first).
    """
    bids: List[Tuple[float, float]] = field(default_factory=list)
    asks: List[Tuple[float, float]] = field(default_factory=list)


class L2MicropriceCalculator:
    """Computes depth-weighted microprice from L2 book data.

    The microprice weights the best bid and ask by the opposing side's
    cumulative depth, so that a book with more resting bid volume pushes
    the microprice toward the ask (buying pressure).

    When depth_levels > 1, we use cumulative volume across the top N levels.
    """

    def __init__(self, depth_levels: int = 5):
        self._depth = max(1, depth_levels)

    def compute(self, l2_book: L2Book) -> float:
        """Compute depth-weighted microprice.

        Args:
            l2_book: L2 order book snapshot.

        Returns:
            Microprice as float.  Falls back to simple mid if book is
            empty or has only one side.
        """
        if not l2_book.bids or not l2_book.asks:
            if l2_book.bids:
                return l2_book.bids[0][0]
            if l2_book.asks:
                return l2_book.asks[0][0]
            return 0.0

        best_bid = l2_book.bids[0][0]
        best_ask = l2_book.asks[0][0]

        bid_vol = sum(
            size for _, size in l2_book.bids[:self._depth]
        )
        ask_vol = sum(
            size for _, size in l2_book.asks[:self._depth]
        )

        total_vol = bid_vol + ask_vol
        if total_vol <= 0:
            return (best_bid + best_ask) / 2.0

        microprice = (bid_vol * best_ask + ask_vol * best_bid) / total_vol
        return microprice
