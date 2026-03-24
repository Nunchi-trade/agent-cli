"""Production logging setup — file + stdout, structured JSON option, secret redaction."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from common.log_filter import SecretFilter


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for machine-parseable logs."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exc"] = self.formatException(record.exc_info)
        # Propagate extra fields if present (e.g., trade events, risk gate transitions)
        for key in ("event_type", "instrument", "side", "size", "price", "pnl",
                     "gate_state", "slot_id", "action"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry)


class ErrorRateTracker:
    """Tracks error rate per window and logs warnings if threshold exceeded."""

    def __init__(self, window_s: int = 300, threshold: int = 10):
        self._window_s = window_s
        self._threshold = threshold
        self._errors: list[float] = []
        self._warned = False

    def record_error(self) -> None:
        now = time.monotonic()
        self._errors.append(now)
        self._prune(now)
        if len(self._errors) >= self._threshold and not self._warned:
            logging.getLogger("error_rate").warning(
                "Error rate exceeded %d errors in %ds window (%d errors)",
                self._threshold, self._window_s, len(self._errors),
            )
            self._warned = True

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_s
        self._errors = [t for t in self._errors if t > cutoff]
        if len(self._errors) < self._threshold:
            self._warned = False

    @property
    def count(self) -> int:
        self._prune(time.monotonic())
        return len(self._errors)


# Module-level tracker
error_tracker = ErrorRateTracker()


class ErrorCountHandler(logging.Handler):
    """Feeds ERROR+ records into the ErrorRateTracker."""

    def __init__(self, tracker: Optional[ErrorRateTracker] = None):
        super().__init__(level=logging.ERROR)
        self._tracker = tracker or error_tracker

    def emit(self, record: logging.LogRecord) -> None:
        self._tracker.record_error()


def configure_logging(
    *,
    strategy_name: str = "apex",
    log_dir: str = "logs",
    data_dir: str = "data/apex",
    json_logs: bool = False,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """Configure production logging with file rotation, secret redaction, and error tracking.

    Args:
        strategy_name: Used in log filename (e.g., logs/apex-2026-03-23.log)
        log_dir: Directory for log files
        data_dir: Data directory (unused here, for caller reference)
        json_logs: If True, use structured JSON format for file handler
        level: Logging level
        max_bytes: Max size per log file before rotation
        backup_count: Number of rotated log files to keep
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Clear existing handlers to avoid duplication on re-init
    root.handlers.clear()

    # Install secret redaction filter
    secret_filter = SecretFilter()

    # --- Stdout handler (human-readable) ---
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(
        "%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    ))
    console.addFilter(secret_filter)
    root.addHandler(console)

    # --- File handler (rotating) ---
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    filename = log_path / f"{strategy_name}-{date_str}.log"

    file_handler = RotatingFileHandler(
        filename, maxBytes=max_bytes, backupCount=backup_count,
    )
    file_handler.setLevel(level)
    if json_logs:
        file_handler.setFormatter(JSONFormatter())
    else:
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    file_handler.addFilter(secret_filter)
    root.addHandler(file_handler)

    # --- Error rate tracker ---
    root.addHandler(ErrorCountHandler(error_tracker))


def log_startup_banner(
    *,
    strategy_name: str,
    mode: str,
    budget: float,
    slots: int,
    leverage: float,
    daily_loss_limit: float,
    guard_preset: str,
    obsidian_enabled: bool,
    reflect_interval: int,
    wallet_address: str = "",
) -> None:
    """Log a startup banner summarizing the active configuration."""
    log = logging.getLogger("startup")
    log.info("=" * 60)
    log.info("APEX Runner — %s", strategy_name)
    log.info("=" * 60)
    log.info("Mode:            %s", mode)
    if wallet_address:
        log.info("Wallet:          %s...%s", wallet_address[:6], wallet_address[-4:])
    log.info("Budget:          $%,.0f", budget)
    log.info("Slots:           %d", slots)
    log.info("Leverage:        %sx", leverage)
    log.info("Daily loss limit: $%,.0f", daily_loss_limit)
    log.info("Guard preset:    %s", guard_preset)
    log.info("Obsidian:        %s", "enabled" if obsidian_enabled else "disabled")
    log.info("REFLECT:         every %d ticks", reflect_interval)
    log.info("=" * 60)


def resolve_obsidian_path(configured_path: str) -> str:
    """Return the Obsidian vault path — auto-detect if not explicitly configured.

    If configured_path is non-empty, return it.
    Otherwise check if ~/obsidian-vault/ exists and return it.
    Returns empty string if no vault found.
    """
    if configured_path:
        return configured_path
    default = os.path.expanduser("~/obsidian-vault")
    if os.path.isdir(default):
        return default
    return ""
