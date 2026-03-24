"""Rotation policy — min-hold and slot cooldown enforcement.

Prevents rapid cycling of positions by enforcing:
- min_hold_ms: Minimum time a position must be held before exit (blocks conviction/stagnation exits)
- slot_cooldown_ms: Minimum time after a slot closes before it can be reused
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class RotationPolicy:
    """Enforces position min-hold and slot cooldown timers."""

    min_hold_ms: int = 2_700_000       # 45 min — blocks early exits
    slot_cooldown_ms: int = 300_000    # 5 min — prevents slot reuse after close

    # Internal tracking: slot_id → close timestamp (ms)
    _slot_close_times: Dict[int, int] = field(default_factory=dict)
    # Internal tracking: slot_id → entry timestamp (ms)
    _slot_entry_times: Dict[int, int] = field(default_factory=dict)

    def record_entry(self, slot_id: int, now_ms: Optional[int] = None) -> None:
        """Record when a position was entered in a slot."""
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        self._slot_entry_times[slot_id] = now_ms

    def record_close(self, slot_id: int, now_ms: Optional[int] = None) -> None:
        """Record when a slot's position was closed."""
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        self._slot_close_times[slot_id] = now_ms
        self._slot_entry_times.pop(slot_id, None)

    def can_exit(self, slot_id: int, now_ms: Optional[int] = None) -> bool:
        """Check if a position has been held long enough to allow exit.

        Returns True if min_hold_ms has elapsed since entry, or if
        min_hold_ms is 0 (disabled).

        Note: Guard CLOSE (trailing stop breach) always overrides min-hold.
        This policy only blocks "soft" exits (conviction collapse, stagnation).
        """
        if self.min_hold_ms <= 0:
            return True
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        entry_ts = self._slot_entry_times.get(slot_id)
        if entry_ts is None:
            return True  # No entry recorded — allow exit
        return (now_ms - entry_ts) >= self.min_hold_ms

    def can_enter_slot(self, slot_id: int, now_ms: Optional[int] = None) -> bool:
        """Check if a slot has cooled down enough after its last close.

        Returns True if slot_cooldown_ms has elapsed since last close, or if
        slot has never been closed.
        """
        if self.slot_cooldown_ms <= 0:
            return True
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        close_ts = self._slot_close_times.get(slot_id)
        if close_ts is None:
            return True  # Never closed — allow entry
        return (now_ms - close_ts) >= self.slot_cooldown_ms

    def time_until_exit_allowed(self, slot_id: int, now_ms: Optional[int] = None) -> int:
        """Returns ms until exit is allowed, or 0 if already allowed."""
        if self.min_hold_ms <= 0:
            return 0
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        entry_ts = self._slot_entry_times.get(slot_id)
        if entry_ts is None:
            return 0
        remaining = self.min_hold_ms - (now_ms - entry_ts)
        return max(0, remaining)

    def time_until_slot_available(self, slot_id: int, now_ms: Optional[int] = None) -> int:
        """Returns ms until slot can be reused, or 0 if already available."""
        if self.slot_cooldown_ms <= 0:
            return 0
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        close_ts = self._slot_close_times.get(slot_id)
        if close_ts is None:
            return 0
        remaining = self.slot_cooldown_ms - (now_ms - close_ts)
        return max(0, remaining)

    def reset(self) -> None:
        """Clear all tracking state (e.g., on daily reset)."""
        self._slot_close_times.clear()
        self._slot_entry_times.clear()
