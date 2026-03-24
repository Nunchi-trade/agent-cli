"""Quoting-engine-level KPI tracking.

Tracks four core KPIs from the HFT MM spec:
  1. Two-sided uptime  — fraction of ticks with two-sided quotes
  2. TOB share         — fraction of ticks at top-of-book
  3. Markout curves    — adverse selection at 1/5/30 tick horizons
  4. Effective spread  — realised spread capture per fill
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class FillRecord:
    """Record of a single fill for markout and spread-capture analysis."""
    tick_index: int
    side: str           # "buy" or "sell"
    fill_price: float
    fill_size: float
    mid_at_fill: float


class QuotingMetrics:
    """Accumulates per-tick and per-fill KPI data.

    Typical usage::

        metrics = QuotingMetrics()
        for tick in ...:
            result = engine.tick(...)
            metrics.on_tick(result.levels, result.halted, mid, best_bid, best_ask)
        # After fills:
        metrics.on_fill("buy", 99.95, 0.5, 100.0, tick_index=42)
        print(metrics.snapshot())
    """

    def __init__(self, markout_horizons: Sequence[int] = (1, 5, 30)):
        self._markout_horizons = tuple(markout_horizons)
        self._max_horizon = max(markout_horizons) if markout_horizons else 30
        self.reset()

    def reset(self) -> None:
        """Clear all accumulated data."""
        self._total_ticks: int = 0
        self._quoting_ticks: int = 0
        self._tob_ticks: int = 0
        self._fills: List[FillRecord] = []
        self._mid_history: deque = deque(maxlen=self._max_horizon + 1)
        self._effective_spreads: List[float] = []

    def on_tick(
        self,
        levels: list,
        halted: bool,
        mid: float,
        best_bid: float,
        best_ask: float,
    ) -> None:
        """Call once per engine tick to update uptime and TOB metrics.

        Args:
            levels: list of LadderLevel from QuoteResult.
            halted: True if engine is halted this tick.
            mid: current market mid price.
            best_bid: current market best bid.
            best_ask: current market best ask.
        """
        self._total_ticks += 1
        self._mid_history.append(mid)

        if halted or not levels:
            return

        # Two-sided uptime: at least one bid and one ask
        has_bid = any(lv.bid_size > 0 for lv in levels)
        has_ask = any(lv.ask_size > 0 for lv in levels)
        if has_bid and has_ask:
            self._quoting_ticks += 1

        # TOB share: our best bid >= market best bid OR our best ask <= market best ask
        if best_bid > 0 and best_ask > 0:
            our_best_bid = max((lv.bid_price for lv in levels if lv.bid_size > 0), default=0.0)
            our_best_ask = min((lv.ask_price for lv in levels if lv.ask_size > 0), default=float("inf"))
            if our_best_bid >= best_bid or our_best_ask <= best_ask:
                self._tob_ticks += 1

    def on_fill(
        self,
        side: str,
        fill_price: float,
        fill_size: float,
        mid_at_fill: float,
        tick_index: int,
    ) -> None:
        """Record a fill for markout and effective-spread calculations.

        Args:
            side: "buy" or "sell".
            fill_price: execution price.
            fill_size: execution size.
            mid_at_fill: market mid at the time of the fill.
            tick_index: monotonic tick counter for markout alignment.
        """
        self._fills.append(FillRecord(
            tick_index=tick_index,
            side=side,
            fill_price=fill_price,
            fill_size=fill_size,
            mid_at_fill=mid_at_fill,
        ))
        # Effective spread capture: 2 * (fill_price - mid) * side_sign
        side_sign = 1.0 if side == "sell" else -1.0
        eff_spread = 2.0 * (fill_price - mid_at_fill) * side_sign
        self._effective_spreads.append(eff_spread)

    def compute_markouts(self) -> Dict[int, Optional[float]]:
        """Compute average markout at each horizon.

        Markout for a buy fill: mid[t+h] - fill_price  (positive = adverse)
        Markout for a sell fill: fill_price - mid[t+h]  (positive = adverse)

        Returns:
            Dict mapping horizon -> average markout (None if insufficient data).
        """
        mid_list = list(self._mid_history)
        results: Dict[int, Optional[float]] = {}

        for h in self._markout_horizons:
            markouts = []
            for f in self._fills:
                future_idx = f.tick_index + h
                if 0 <= future_idx < len(mid_list):
                    future_mid = mid_list[future_idx]
                    if f.side == "buy":
                        markouts.append(future_mid - f.fill_price)
                    else:
                        markouts.append(f.fill_price - future_mid)
            results[h] = sum(markouts) / len(markouts) if markouts else None

        return results

    @property
    def uptime(self) -> float:
        """Two-sided quoting uptime ratio."""
        if self._total_ticks == 0:
            return 0.0
        return self._quoting_ticks / self._total_ticks

    @property
    def tob_share(self) -> float:
        """Top-of-book share ratio."""
        if self._total_ticks == 0:
            return 0.0
        return self._tob_ticks / self._total_ticks

    @property
    def effective_spread(self) -> Optional[float]:
        """Average effective spread capture across all fills."""
        if not self._effective_spreads:
            return None
        return sum(self._effective_spreads) / len(self._effective_spreads)

    def snapshot(self) -> Dict[str, Any]:
        """Return a full KPI snapshot as a dict."""
        return {
            "total_ticks": self._total_ticks,
            "quoting_ticks": self._quoting_ticks,
            "uptime": round(self.uptime, 4),
            "tob_share": round(self.tob_share, 4),
            "total_fills": len(self._fills),
            "effective_spread": self.effective_spread,
            "markouts": self.compute_markouts(),
        }
