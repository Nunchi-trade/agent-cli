"""Oracle freshness monitor — staleness detection and kill-switch.

Tracks the age of the oracle price update and classifies it into zones:
  Fresh   (< warning_ms)  -> normal operation
  Warning (< stale_ms)    -> widen spreads 1.5x
  Stale   (< kill_ms)     -> widen spreads 3x, force reduce-only
  Kill    (>= kill_ms)    -> halt quoting entirely
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OracleMonitorConfig:
    """Thresholds for oracle staleness zones (in milliseconds)."""
    warning_ms: int = 5_000
    stale_ms: int = 15_000
    kill_ms: int = 60_000
    enabled: bool = True


@dataclass
class OracleStatus:
    """Result of an oracle freshness check."""
    zone: str               # "fresh", "warning", "stale", "kill"
    spread_mult: float      # multiplier applied to half-spread
    reduce_only: bool       # True if staleness forces reduce-only
    halt: bool              # True if staleness forces halt
    age_ms: int             # how old the oracle price is


class OracleFreshnessMonitor:
    """Monitors oracle timestamp freshness and returns appropriate actions."""

    def __init__(self, config: OracleMonitorConfig | None = None):
        self._config = config or OracleMonitorConfig()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def check(self, oracle_timestamp_ms: int, now_ms: int) -> OracleStatus:
        """Check oracle freshness and return the appropriate status.

        Args:
            oracle_timestamp_ms: Timestamp of the last oracle price update.
            now_ms: Current wall-clock time in milliseconds.

        Returns:
            OracleStatus with zone classification and recommended actions.
        """
        if not self._config.enabled:
            return OracleStatus(
                zone="fresh", spread_mult=1.0,
                reduce_only=False, halt=False, age_ms=0,
            )

        if oracle_timestamp_ms <= 0 or now_ms <= 0:
            return OracleStatus(
                zone="fresh", spread_mult=1.0,
                reduce_only=False, halt=False, age_ms=0,
            )

        age_ms = max(0, now_ms - oracle_timestamp_ms)

        if age_ms >= self._config.kill_ms:
            return OracleStatus(
                zone="kill", spread_mult=1.0,
                reduce_only=True, halt=True, age_ms=age_ms,
            )
        elif age_ms >= self._config.stale_ms:
            return OracleStatus(
                zone="stale", spread_mult=3.0,
                reduce_only=True, halt=False, age_ms=age_ms,
            )
        elif age_ms >= self._config.warning_ms:
            return OracleStatus(
                zone="warning", spread_mult=1.5,
                reduce_only=False, halt=False, age_ms=age_ms,
            )
        else:
            return OracleStatus(
                zone="fresh", spread_mult=1.0,
                reduce_only=False, halt=False, age_ms=age_ms,
            )
