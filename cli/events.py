"""Shared local telemetry — append machine-readable events to events.jsonl.

A small, dependency-free primitive used across the operator loop (APEX/REFLECT
proof + schedule) and intended for reuse by freshness/demo-seed surfaces.

`events.jsonl` lives under a run's data dir (e.g. data/apex/events.jsonl) and is
an append-only JSONL log: one JSON object per line. Each line is a self-describing
event with a `type`, a `ts`/`ts_ms` timestamp, and an arbitrary payload.

This is deliberately thin: it wraps parent.store.JSONLStore so the on-disk format
matches the rest of the codebase (trades.jsonl, clearing_log.jsonl) and corrupt
lines are skipped on read rather than blowing up consumers.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from parent.store import JSONLStore

EVENTS_FILENAME = "events.jsonl"


def events_path(data_dir: str) -> Path:
    """Return the canonical events.jsonl path for a data dir."""
    return Path(data_dir) / EVENTS_FILENAME


def append_event(data_dir: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Append one event to ``<data_dir>/events.jsonl`` and return the stored record.

    The input dict is copied and stamped with ``ts_ms`` (epoch millis) and ``ts``
    (UTC ISO-8601) if those keys are absent, so every line is time-ordered and
    human-readable without mutating the caller's dict. ``type`` is left to the
    caller; convention is a short snake_case string (e.g. "reflect_run").
    """
    record = dict(event)
    if "ts_ms" not in record:
        record["ts_ms"] = int(time.time() * 1000)
    if "ts" not in record:
        record["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store = JSONLStore(path=str(events_path(data_dir)))
    store.append(record)
    return record


def read_events(data_dir: str) -> List[Dict[str, Any]]:
    """Read all events from ``<data_dir>/events.jsonl`` (corrupt lines skipped)."""
    return JSONLStore(path=str(events_path(data_dir))).read_all()
