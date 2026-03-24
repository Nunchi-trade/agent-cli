"""Event schedule — calendar-driven spread widening.

Phase 2: CalendarEventSchedule loads events from YAML and returns
h_event in price units when the current timestamp falls within an
event's pre/post window.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import yaml


class BaseEventSchedule(ABC):
    """Interface for event-risk spread component."""

    @abstractmethod
    def h_event(self, instrument: str, timestamp_ms: int) -> float:
        """Return event-risk component h_event in price units.

        Returns 0 when no event is imminent; positive value to widen
        spreads around scheduled events.
        """
        ...


class StubEventSchedule(BaseEventSchedule):
    """Phase 1: always returns 0."""

    def h_event(self, instrument: str, timestamp_ms: int) -> float:
        return 0.0


@dataclass
class CalendarEvent:
    """A single scheduled event that widens spreads."""
    event_type: str
    h_event_bps: float
    pre_window_ms: int
    post_window_ms: int
    time_pattern: Optional[str] = None   # "HH:MM" hourly pattern
    time_utc: Optional[str] = None       # "HH:MM" fixed daily UTC time

    def is_active(self, timestamp_ms: int) -> bool:
        """Check if timestamp falls within this event's window."""
        if timestamp_ms <= 0:
            return False

        dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)

        if self.time_pattern is not None:
            # Hourly pattern like "HH:00" — fires every hour at :00
            try:
                event_minute = int(self.time_pattern.split(":")[1])
            except (IndexError, ValueError):
                event_minute = 0

            # Check current hour, previous hour, and next hour
            # (pre-window may reach into the next hour, post-window into previous)
            base_dt = dt.replace(minute=event_minute, second=0, microsecond=0)
            for delta_hours in [-1, 0, 1]:
                candidate = base_dt + timedelta(hours=delta_hours)
                candidate_ms = int(candidate.timestamp() * 1000)
                if (candidate_ms - self.pre_window_ms) <= timestamp_ms <= (candidate_ms + self.post_window_ms):
                    return True
            return False

        elif self.time_utc is not None:
            # Fixed daily time like "14:30"
            parts = self.time_utc.split(":")
            try:
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                return False

            event_dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
            event_ms = int(event_dt.timestamp() * 1000)
        else:
            return False

        return (event_ms - self.pre_window_ms) <= timestamp_ms <= (event_ms + self.post_window_ms)


class CalendarEventSchedule(BaseEventSchedule):
    """Calendar-driven event schedule loaded from YAML.

    Each event has a time pattern and window. When the current time
    is within [event_time - pre_window, event_time + post_window],
    the event is active and contributes h_event_bps to spread widening.
    """

    def __init__(self, calendar_path: str = ""):
        self._events: List[CalendarEvent] = []
        self._mid: float = 0.0

        if calendar_path and os.path.exists(calendar_path):
            self._load(calendar_path)

    def _load(self, path: str) -> None:
        with open(path) as f:
            data = yaml.safe_load(f)

        for entry in data.get("events", []):
            self._events.append(CalendarEvent(
                event_type=entry.get("type", "unknown"),
                h_event_bps=entry.get("h_event_bps", 0.0),
                pre_window_ms=entry.get("pre_window_ms", 0),
                post_window_ms=entry.get("post_window_ms", 0),
                time_pattern=entry.get("time_pattern"),
                time_utc=entry.get("time_utc"),
            ))

    def set_mid(self, mid: float) -> None:
        """Update mid price for bps -> price conversion."""
        self._mid = mid

    def h_event(self, instrument: str, timestamp_ms: int) -> float:
        """Return max h_event across all active events, in price units."""
        if not self._events or self._mid <= 0 or timestamp_ms <= 0:
            return 0.0

        max_bps = 0.0
        for event in self._events:
            if event.is_active(timestamp_ms):
                max_bps = max(max_bps, event.h_event_bps)

        # Convert bps to price units: bps / 10000 * mid
        return max_bps / 10_000.0 * self._mid
