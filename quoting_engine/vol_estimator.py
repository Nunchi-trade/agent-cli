"""Rolling volatility estimator from log returns.

Extracted from AvellanedaStoikovMM._update_vol() for shared use.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Tuple


class RollingVolEstimator:
    """Estimates per-tick volatility from a rolling window of mid prices.

    Returns:
        sigma_price: std(log_returns) * mid  -- dollar-scaled vol
        sigma_log:   std(log_returns)        -- unitless, for vol binning
    """

    def __init__(self, window: int = 30, min_samples: int = 3):
        self.window = window
        self.min_samples = min_samples
        self._prices: deque[float] = deque(maxlen=window)
        self._log_returns: deque[float] = deque(maxlen=window)

    def update(self, mid: float) -> Tuple[float, float]:
        """Ingest a new mid price, return (sigma_price, sigma_log).

        If insufficient data (< min_samples log returns), returns a
        conservative fallback based on 3 bps.
        """
        if self._prices:
            prev = self._prices[-1]
            if prev > 0 and mid > 0:
                self._log_returns.append(math.log(mid / prev))
        self._prices.append(mid)

        if len(self._log_returns) < self.min_samples:
            fallback_std = 3.0 / 10_000  # 3 bps fallback
            return mid * fallback_std, fallback_std

        mean_r = sum(self._log_returns) / len(self._log_returns)
        var = sum((r - mean_r) ** 2 for r in self._log_returns) / len(self._log_returns)
        log_std = math.sqrt(max(var, 1e-12))
        return log_std * mid, log_std

    @property
    def ready(self) -> bool:
        """True if we have enough samples for a meaningful vol estimate."""
        return len(self._log_returns) >= self.min_samples

    @property
    def sample_count(self) -> int:
        return len(self._log_returns)
