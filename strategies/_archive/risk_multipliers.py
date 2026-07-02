"""Volatility regime binning and drawdown-linked spread amplifiers.

Layer 2 (vol binning) and Layer 3 (DD amplification) from RISK_FRAMEWORK.tex.
These are agent-side computations — no external I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

# Annualisation: 20s rounds → per-year
T_ROUND_SEC: float = 20.0
ANNUALIZE: float = math.sqrt(365 * 24 * 3600 / T_ROUND_SEC)

# Vol bin definitions: (upper_threshold_exclusive, multiplier, name)
VOL_BINS: List[Tuple[float, float, str]] = [
    (0.15, 1.0, "I_low"),
    (0.40, 1.5, "II_normal"),
    (0.80, 2.5, "III_high"),
    (float("inf"), 5.0, "IV_extreme"),
]

# DD bin definitions: (upper_threshold_exclusive_pct, multiplier, name)
DD_BINS: List[Tuple[float, float, str]] = [
    (0.5, 1.0, "green"),
    (1.5, 1.5, "yellow"),
    (2.5, 2.0, "orange"),
    (float("inf"), float("inf"), "red"),
]

HYSTERESIS_ROUNDS: int = 3


@dataclass
class VolBinClassifier:
    """Classifies realized volatility into regime bins with hysteresis.

    Upward transitions (wider spread) are immediate.
    Downward transitions require HYSTERESIS_ROUNDS consecutive rounds
    in the lower bin before dropping.
    """
    _current_bin_idx: int = 0
    _downward_candidate_idx: int = -1
    _downward_rounds: int = 0

    def annualize(self, sigma_log_std: float) -> float:
        """Convert per-round log-return std to annualized vol."""
        return sigma_log_std * ANNUALIZE

    def classify(self, sigma_log_std: float) -> Tuple[float, str]:
        """Update bin state and return (m_vol, bin_name).

        sigma_log_std: population std of log-returns (unitless).
        """
        sigma_ann = self.annualize(sigma_log_std)
        target_idx = self._find_bin_idx(sigma_ann)

        if target_idx > self._current_bin_idx:
            # Immediate upward transition
            self._current_bin_idx = target_idx
            self._downward_candidate_idx = -1
            self._downward_rounds = 0
        elif target_idx < self._current_bin_idx:
            # Count toward downward transition
            if target_idx == self._downward_candidate_idx:
                self._downward_rounds += 1
            else:
                self._downward_candidate_idx = target_idx
                self._downward_rounds = 1
            if self._downward_rounds >= HYSTERESIS_ROUNDS:
                self._current_bin_idx = target_idx
                self._downward_candidate_idx = -1
                self._downward_rounds = 0
        else:
            # Same bin — reset downward counter
            self._downward_candidate_idx = -1
            self._downward_rounds = 0

        _, m_vol, name = VOL_BINS[self._current_bin_idx]
        return m_vol, name

    def _find_bin_idx(self, sigma_ann: float) -> int:
        for i, (threshold, _, _) in enumerate(VOL_BINS):
            if sigma_ann < threshold:
                return i
        return len(VOL_BINS) - 1


def dd_multiplier(daily_drawdown_pct: float) -> Tuple[float, str]:
    """Return (m_dd, bin_name) for a given drawdown percentage (0.0-100.0)."""
    for threshold, mult, name in DD_BINS:
        if daily_drawdown_pct < threshold:
            return mult, name
    return float("inf"), "red"
