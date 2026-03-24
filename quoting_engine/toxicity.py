"""Toxicity scorer — detects adverse selection via fill-then-move patterns.

Tracks EMA of post-fill price moves (markouts) to estimate how toxic
the order flow is. Higher toxicity -> wider spreads via h_tox component.

Markout: after a fill at price P, measure mid_price N ticks later.
  - Buy fill markout: mid_later - P (positive = good, negative = adverse)
  - Sell fill markout: P - mid_later (positive = good, negative = adverse)

Adverse selection = consistently negative markouts -> takers are informed.

Tiered toxicity (G4):
  - T1 threshold: widen spread + cut size
  - T2 threshold: one-sided mode — cancel the adverse side entirely
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple


@dataclass
class ToxicityResponse:
    """Structured response from tiered toxicity scoring."""
    h_tox: float              # spread widening component (price units)
    size_mult: float = 1.0    # size reduction multiplier
    cancel_bids: bool = False # T2: cancel all bids (toxic buys detected)
    cancel_asks: bool = False # T2: cancel all asks (toxic sells detected)
    tier: str = "normal"      # "normal", "T1", "T2"
    cooldown_remaining: int = 0


class BaseToxicityScorer(ABC):
    """Interface for toxicity scoring."""

    @abstractmethod
    def score(self, mid: float, bid: float, ask: float, timestamp_ms: int) -> float:
        """Return toxicity component h_tox in price units.

        Higher values -> wider spreads to protect against adverse selection.
        """
        ...


class StubToxicityScorer(BaseToxicityScorer):
    """Always returns 0 — for testing or when toxicity detection is disabled."""

    def score(self, mid: float, bid: float, ask: float, timestamp_ms: int) -> float:
        return 0.0


class MarkoutToxicityScorer(BaseToxicityScorer):
    """EMA-based toxicity scorer using post-fill markout tracking.

    Usage:
        scorer = MarkoutToxicityScorer(lookback=5, ema_alpha=0.3, scale_bps=2.0)

        # On each tick:
        h_tox = scorer.score(mid, bid, ask, timestamp_ms)

        # After a fill:
        scorer.record_fill(fill_price=100.05, side="sell", tick_count=current_tick)
    """

    def __init__(
        self,
        lookback: int = 5,
        ema_alpha: float = 0.3,
        scale_bps: float = 2.0,
        max_pending: int = 100,
        t1_threshold: float = 0.4,
        t2_threshold: float = 0.7,
        t1_spread_mult: float = 1.5,
        t1_size_mult: float = 0.7,
        t2_spread_mult: float = 2.0,
        t2_size_mult: float = 0.3,
        cooldown_ticks: int = 5,
    ):
        """
        Args:
            lookback: Ticks to wait before computing markout for a fill.
            ema_alpha: EMA smoothing factor for markout average (0-1, higher = faster).
            scale_bps: Max h_tox contribution in bps when toxicity = 1.0.
            max_pending: Maximum number of pending fills to track.
            t1_threshold: Toxicity level to trigger T1 (widen + cut size).
            t2_threshold: Toxicity level to trigger T2 (one-sided mode).
            t1_spread_mult: Spread multiplier at T1.
            t1_size_mult: Size multiplier at T1.
            t2_spread_mult: Spread multiplier at T2.
            t2_size_mult: Size multiplier at T2.
            cooldown_ticks: Ticks to remain in elevated tier after toxicity drops.
        """
        self._lookback = lookback
        self._alpha = ema_alpha
        self._scale_bps = scale_bps
        self._max_pending = max_pending

        # Tiered thresholds (G4)
        self._t1_threshold = t1_threshold
        self._t2_threshold = t2_threshold
        self._t1_spread_mult = t1_spread_mult
        self._t1_size_mult = t1_size_mult
        self._t2_spread_mult = t2_spread_mult
        self._t2_size_mult = t2_size_mult
        self._cooldown_ticks = cooldown_ticks

        # Pending fills: (fill_price, side, tick_at_fill)
        self._pending: Deque[Tuple[float, str, int]] = deque(maxlen=max_pending)
        self._tick_count = 0
        self._ema_markout: float = 0.0
        self._has_data = False

        # Adverse side tracking: which side is getting picked off
        self._buy_markout_ema: float = 0.0
        self._sell_markout_ema: float = 0.0
        self._adverse_side: str = "buy"  # default

        # Cooldown state
        self._cooldown_remaining: int = 0
        self._cooldown_tier: str = "normal"

    @property
    def toxicity(self) -> float:
        """Current toxicity estimate in [0, 1]. 0 = benign, 1 = highly toxic."""
        if not self._has_data:
            return 0.0
        # Negative markout = adverse selection. Clamp to [0, 1].
        return max(0.0, min(1.0, -self._ema_markout))

    @property
    def ema_markout(self) -> float:
        """Raw EMA markout value. Negative = adverse, positive = benign."""
        return self._ema_markout

    def record_fill(self, fill_price: float, side: str, tick_count: Optional[int] = None) -> None:
        """Record a fill for markout tracking.

        Args:
            fill_price: Price at which the fill occurred.
            side: "buy" or "sell".
            tick_count: Tick number at fill time. If None, uses internal counter.
        """
        t = tick_count if tick_count is not None else self._tick_count
        self._pending.append((fill_price, side, t))

    def score(self, mid: float, bid: float, ask: float, timestamp_ms: int) -> float:
        """Compute h_tox based on recent fill markouts.

        Called once per tick. Resolves any pending fills that have matured
        (lookback ticks elapsed), updates EMA, and returns h_tox.
        """
        self._tick_count += 1

        # Resolve matured fills
        while self._pending and (self._tick_count - self._pending[0][2]) >= self._lookback:
            fill_price, side, _ = self._pending.popleft()
            # Markout: how much did the price move against us after the fill?
            # Normalized by fill_price to get a unitless ratio.
            if fill_price > 0:
                if side == "buy":
                    # For buy fills: positive markout if price went up (good for us)
                    markout = (mid - fill_price) / fill_price
                else:
                    # For sell fills: positive markout if price went down (good for us)
                    markout = (fill_price - mid) / fill_price

                # Update overall EMA
                if self._has_data:
                    self._ema_markout = self._alpha * markout + (1 - self._alpha) * self._ema_markout
                else:
                    self._ema_markout = markout
                    self._has_data = True

                # Per-side EMA tracking for adverse side detection
                if side == "buy":
                    self._buy_markout_ema = self._alpha * markout + (1 - self._alpha) * self._buy_markout_ema
                else:
                    self._sell_markout_ema = self._alpha * markout + (1 - self._alpha) * self._sell_markout_ema

                # Adverse side = the side with worse (more negative) markout
                if self._buy_markout_ema < self._sell_markout_ema:
                    self._adverse_side = "buy"
                else:
                    self._adverse_side = "sell"

        # Tick down cooldown
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        # Convert toxicity [0, 1] to h_tox in price units
        if mid <= 0:
            return 0.0
        return self.toxicity * self._scale_bps * (mid / 10_000)

    def score_full(self, mid: float, bid: float, ask: float, timestamp_ms: int) -> ToxicityResponse:
        """Tiered toxicity scoring with T1/T2 thresholds.

        Returns a ToxicityResponse with spread/size multipliers and
        one-sided cancellation flags for T2.
        """
        h_tox = self.score(mid, bid, ask, timestamp_ms)
        tox = self.toxicity

        # Determine current tier
        if tox >= self._t2_threshold:
            tier = "T2"
            self._cooldown_remaining = self._cooldown_ticks
            self._cooldown_tier = "T2"
        elif tox >= self._t1_threshold:
            tier = "T1"
            self._cooldown_remaining = self._cooldown_ticks
            self._cooldown_tier = "T1"
        elif self._cooldown_remaining > 0:
            # Stay in previous tier during cooldown
            tier = self._cooldown_tier
        else:
            tier = "normal"

        if tier == "T2":
            return ToxicityResponse(
                h_tox=h_tox * self._t2_spread_mult,
                size_mult=self._t2_size_mult,
                cancel_bids=self._adverse_side == "buy",
                cancel_asks=self._adverse_side == "sell",
                tier="T2",
                cooldown_remaining=self._cooldown_remaining,
            )
        elif tier == "T1":
            return ToxicityResponse(
                h_tox=h_tox * self._t1_spread_mult,
                size_mult=self._t1_size_mult,
                tier="T1",
                cooldown_remaining=self._cooldown_remaining,
            )
        return ToxicityResponse(h_tox=h_tox)
