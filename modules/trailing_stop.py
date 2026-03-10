"""Pure Guard (Dynamic Stop Loss) trailing stop engine.

Zero I/O. Zero HL dependency. Takes price + state in, returns updated state + action out.
Fully deterministic and testable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from modules.guard_config import GuardConfig
from modules.guard_state import GuardState


class GuardAction(Enum):
    HOLD = "hold"
    CLOSE = "close"
    TIER_CHANGED = "tier_changed"


@dataclass
class GuardResult:
    """Output of a single Guard evaluation tick."""

    action: GuardAction
    state: GuardState
    reason: str = ""
    new_tier_index: Optional[int] = None
    effective_floor: float = 0.0
    trailing_floor: float = 0.0
    tier_floor: float = 0.0
    roe_pct: float = 0.0


class TrailingStopEngine:
    """Stateless Guard evaluation engine.

    Each call to evaluate() receives the full state and returns
    a new state + action. The caller is responsible for persistence.
    """

    def __init__(self, config: GuardConfig):
        self.config = config

    def evaluate(
        self,
        price: float,
        state: GuardState,
        now_ms: Optional[int] = None,
    ) -> GuardResult:
        """Core Guard tick: given current price and state, return action + updated state.

        Args:
            price: Current market price.
            state: Current Guard state (not mutated; a copy is made internally).
            now_ms: Optional timestamp override for testability.
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        cfg = self.config
        s = state.copy()
        is_long = cfg.direction == "long"

        # 1. Update high-water mark
        if is_long:
            if price > s.high_water:
                s.high_water = price
                s.high_water_ts = now_ms
        else:
            if s.high_water == 0.0 or price < s.high_water:
                s.high_water = price
                s.high_water_ts = now_ms

        # 2. Compute ROE
        roe = self._compute_roe(price, s)
        s.current_roe = roe

        # 3. Route to phase
        if s.current_tier_index < 0:
            return self._phase1(price, s, roe, now_ms)
        else:
            return self._phase2(price, s, roe, now_ms)

    def _compute_roe(self, price: float, state: GuardState) -> float:
        """ROE = (delta / entry) * leverage * 100.

        LONG: delta = price - entry
        SHORT: delta = entry - price
        """
        entry = state.entry_price
        leverage = self.config.leverage
        if entry <= 0 or leverage <= 0:
            return 0.0
        if self.config.direction == "long":
            return (price - entry) / entry * leverage * 100.0
        else:
            return (entry - price) / entry * leverage * 100.0

    def _phase1(
        self, price: float, s: GuardState, roe: float, now_ms: int,
    ) -> GuardResult:
        """Phase 1: 'Let it breathe' — wide retrace, patient breach counting."""
        cfg = self.config
        is_long = cfg.direction == "long"

        # Check tier graduation (transition to Phase 2)
        if cfg.tiers and roe >= cfg.tiers[0].trigger_pct:
            # Graduate: activate first tier
            s.current_tier_index = 0
            s.breach_count = 0
            tier_fl = self._tier_floor_price(0, s)
            return GuardResult(
                action=GuardAction.TIER_CHANGED,
                state=s,
                reason=f"Phase 1->2: tier 0 activated (ROE {roe:.1f}% >= {cfg.tiers[0].trigger_pct}%)",
                new_tier_index=0,
                effective_floor=tier_fl,
                tier_floor=tier_fl,
                trailing_floor=0.0,
                roe_pct=roe,
            )

        # Compute Phase 1 floors
        retrace = cfg.phase1_retrace
        if is_long:
            trailing_fl = s.high_water * (1.0 - retrace)
            abs_fl = cfg.phase1_absolute_floor
            effective_fl = max(trailing_fl, abs_fl) if abs_fl > 0 else trailing_fl
            is_breach = price <= effective_fl
        else:
            trailing_fl = s.high_water * (1.0 + retrace)
            abs_fl = cfg.phase1_absolute_floor
            effective_fl = min(trailing_fl, abs_fl) if abs_fl > 0 else trailing_fl
            is_breach = price >= effective_fl

        if is_breach:
            s.breach_count += 1
            if s.breach_count >= cfg.phase1_max_breaches:
                return GuardResult(
                    action=GuardAction.CLOSE,
                    state=s,
                    reason=(
                        f"Phase 1 close: {s.breach_count}/{cfg.phase1_max_breaches} breaches, "
                        f"price={price:.4f}, floor={effective_fl:.4f}"
                    ),
                    effective_floor=effective_fl,
                    trailing_floor=trailing_fl,
                    roe_pct=roe,
                )
        else:
            s.breach_count = _decay_breach(s.breach_count, cfg.breach_decay_mode)

        return GuardResult(
            action=GuardAction.HOLD,
            state=s,
            reason=f"Phase 1: ROE={roe:.1f}%, HW={s.high_water:.4f}, breaches={s.breach_count}",
            effective_floor=effective_fl,
            trailing_floor=trailing_fl,
            roe_pct=roe,
        )

    def _phase2(
        self, price: float, s: GuardState, roe: float, now_ms: int,
    ) -> GuardResult:
        """Phase 2: 'Lock the bag' — tight retrace, tier ratcheting."""
        cfg = self.config
        is_long = cfg.direction == "long"

        # Ratchet up tiers (never go backward)
        tier_changed = False
        prev_tier = s.current_tier_index
        while (
            s.current_tier_index + 1 < len(cfg.tiers)
            and roe >= cfg.tiers[s.current_tier_index + 1].trigger_pct
        ):
            s.current_tier_index += 1
            s.breach_count = 0
            tier_changed = True

        tier = cfg.tiers[s.current_tier_index]

        # Stagnation take-profit check
        if cfg.stagnation_enabled and roe >= cfg.stagnation_min_roe:
            stale_ms = now_ms - s.high_water_ts
            if stale_ms >= cfg.stagnation_timeout_ms:
                tier_fl = self._tier_floor_price(s.current_tier_index, s)
                return GuardResult(
                    action=GuardAction.CLOSE,
                    state=s,
                    reason=(
                        f"Stagnation TP: ROE={roe:.1f}% >= {cfg.stagnation_min_roe}%, "
                        f"HW stale {stale_ms / 1000:.0f}s"
                    ),
                    effective_floor=tier_fl,
                    tier_floor=tier_fl,
                    roe_pct=roe,
                )

        # Compute floors
        tier_fl = self._tier_floor_price(s.current_tier_index, s)
        retrace = tier.retrace if tier.retrace is not None else cfg.phase2_retrace

        if is_long:
            trailing_fl = s.high_water * (1.0 - retrace)
            effective_fl = max(tier_fl, trailing_fl)
            is_breach = price <= effective_fl
        else:
            trailing_fl = s.high_water * (1.0 + retrace)
            effective_fl = min(tier_fl, trailing_fl)
            is_breach = price >= effective_fl

        # Return tier change event before breach check (tier upgrade is higher priority info)
        if tier_changed:
            return GuardResult(
                action=GuardAction.TIER_CHANGED,
                state=s,
                reason=(
                    f"Tier upgrade: {prev_tier}->{s.current_tier_index} "
                    f"(ROE {roe:.1f}% >= {tier.trigger_pct}%)"
                ),
                new_tier_index=s.current_tier_index,
                effective_floor=effective_fl,
                trailing_floor=trailing_fl,
                tier_floor=tier_fl,
                roe_pct=roe,
            )

        # Breach detection
        max_breaches = tier.max_breaches if tier.max_breaches is not None else cfg.phase2_max_breaches

        if is_breach:
            s.breach_count += 1
            if s.breach_count >= max_breaches:
                return GuardResult(
                    action=GuardAction.CLOSE,
                    state=s,
                    reason=(
                        f"Phase 2 close: tier {s.current_tier_index}, "
                        f"{s.breach_count}/{max_breaches} breaches, "
                        f"price={price:.4f}, floor={effective_fl:.4f}"
                    ),
                    new_tier_index=s.current_tier_index,
                    effective_floor=effective_fl,
                    trailing_floor=trailing_fl,
                    tier_floor=tier_fl,
                    roe_pct=roe,
                )
        else:
            s.breach_count = _decay_breach(s.breach_count, cfg.breach_decay_mode)

        return GuardResult(
            action=GuardAction.HOLD,
            state=s,
            reason=(
                f"Phase 2: tier {s.current_tier_index}, ROE={roe:.1f}%, "
                f"HW={s.high_water:.4f}, breaches={s.breach_count}"
            ),
            effective_floor=effective_fl,
            trailing_floor=trailing_fl,
            tier_floor=tier_fl,
            roe_pct=roe,
        )

    def _tier_floor_price(self, tier_index: int, state: GuardState) -> float:
        """Calculate the price floor for a given tier.

        LONG:  entry * (1 + lockPct / 100 / leverage)
        SHORT: entry * (1 - lockPct / 100 / leverage)
        """
        tier = self.config.tiers[tier_index]
        entry = state.entry_price
        leverage = self.config.leverage
        if entry <= 0 or leverage <= 0:
            return 0.0
        if self.config.direction == "long":
            return entry * (1.0 + tier.lock_pct / 100.0 / leverage)
        else:
            return entry * (1.0 - tier.lock_pct / 100.0 / leverage)


def _decay_breach(count: int, mode: str) -> int:
    """Decay breach count on recovery above floor.

    'hard': reset to 0 (default)
    'soft': decay by 1
    """
    if count <= 0:
        return 0
    if mode == "soft":
        return count - 1
    return 0  # hard
