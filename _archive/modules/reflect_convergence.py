"""REFLECT convergence tracker and adaptive hysteresis.

Tracks whether REFLECT auto-adjustments are actually improving performance
over multiple cycles, and prevents parameter oscillation through directional
hysteresis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("reflect_convergence")


@dataclass
class CycleSnapshot:
    """Metrics snapshot from a single REFLECT cycle."""
    cycle_id: int
    win_rate: float
    net_pnl: float
    fdr: float
    total_round_trips: int
    adjustments_made: int


@dataclass
class ConvergenceTracker:
    """Tracks REFLECT performance across cycles to detect non-convergence.

    Flags "not converging" if N consecutive cycles show no improvement
    in the target metric (win_rate or net_pnl).
    """
    lookback_cycles: int = 3  # Number of cycles to compare
    _history: List[CycleSnapshot] = field(default_factory=list)
    _cycle_counter: int = 0

    def record_cycle(self, win_rate: float, net_pnl: float, fdr: float,
                     total_round_trips: int, adjustments_made: int) -> None:
        """Record metrics from a completed REFLECT cycle."""
        self._cycle_counter += 1
        self._history.append(CycleSnapshot(
            cycle_id=self._cycle_counter,
            win_rate=win_rate,
            net_pnl=net_pnl,
            fdr=fdr,
            total_round_trips=total_round_trips,
            adjustments_made=adjustments_made,
        ))
        # Keep only recent history
        if len(self._history) > self.lookback_cycles * 2:
            self._history = self._history[-self.lookback_cycles * 2:]

    def is_converging(self) -> Tuple[bool, str]:
        """Check if recent cycles show improvement.

        Returns (is_converging, reason).
        Converging means at least one of: win_rate improving, net_pnl improving,
        or fdr decreasing. Not converging means none of these improved over
        the lookback window.
        """
        if len(self._history) < self.lookback_cycles + 1:
            return True, "insufficient data for convergence check"

        recent = self._history[-self.lookback_cycles:]
        baseline = self._history[-(self.lookback_cycles + 1)]

        # Check if any metric improved
        wr_improved = any(c.win_rate > baseline.win_rate for c in recent)
        pnl_improved = any(c.net_pnl > baseline.net_pnl for c in recent)
        fdr_improved = any(c.fdr < baseline.fdr for c in recent)

        if wr_improved or pnl_improved or fdr_improved:
            return True, "metrics improving"

        # Check if adjustments were being made (stale if no adjustments)
        total_adj = sum(c.adjustments_made for c in recent)
        if total_adj == 0:
            return True, "no adjustments made (stable)"

        return False, (
            f"not converging after {self.lookback_cycles} cycles: "
            f"win_rate {baseline.win_rate:.1f}%→{recent[-1].win_rate:.1f}%, "
            f"net_pnl ${baseline.net_pnl:.2f}→${recent[-1].net_pnl:.2f}, "
            f"fdr {baseline.fdr:.1f}%→{recent[-1].fdr:.1f}%"
        )


@dataclass
class DirectionalHysteresis:
    """Prevents parameter oscillation by requiring consecutive signals.

    Tracks the last adjustment direction for each parameter. A flip
    (tighten→relax or relax→tighten) is only applied if the same
    direction is signaled for `required_consecutive` cycles in a row.
    """
    required_consecutive: int = 2
    _direction_history: Dict[str, List[str]] = field(default_factory=dict)

    def should_apply(self, param: str, direction: str) -> bool:
        """Check if an adjustment should be applied given hysteresis rules.

        Args:
            param: Parameter name (e.g., "radar_score_threshold")
            direction: "up" or "down"

        Returns:
            True if the adjustment should proceed.
        """
        history = self._direction_history.get(param, [])

        if not history:
            # First adjustment for this param — always allow
            self._direction_history[param] = [direction]
            return True

        last_direction = history[-1]

        if direction == last_direction:
            # Same direction — allow and record
            history.append(direction)
            return True

        # Direction flip — check if we have enough consecutive signals
        # Count how many times this NEW direction has been signaled
        history.append(direction)
        self._direction_history[param] = history

        consecutive = 0
        for d in reversed(history):
            if d == direction:
                consecutive += 1
            else:
                break

        if consecutive >= self.required_consecutive:
            return True

        log.debug("Hysteresis blocked flip for %s: %s→%s (need %d consecutive, have %d)",
                  param, last_direction, direction, self.required_consecutive, consecutive)
        return False

    def reset(self, param: Optional[str] = None) -> None:
        """Reset hysteresis state for a param (or all params)."""
        if param is None:
            self._direction_history.clear()
        else:
            self._direction_history.pop(param, None)
