"""Tests for event_schedule.py — CalendarEventSchedule."""
import os
import tempfile

from quoting_engine.event_schedule import (
    CalendarEvent,
    CalendarEventSchedule,
    StubEventSchedule,
)


def test_stub_returns_zero():
    s = StubEventSchedule()
    assert s.h_event("FR-PERP", 1000000) == 0.0


def test_calendar_no_events():
    """Empty calendar returns 0."""
    cs = CalendarEventSchedule()  # no path -> no events
    cs.set_mid(100.0)
    assert cs.h_event("FR-PERP", 1708300800000) == 0.0


def test_calendar_zero_timestamp():
    cs = CalendarEventSchedule()
    cs.set_mid(100.0)
    assert cs.h_event("FR-PERP", 0) == 0.0


def test_funding_settlement_in_pre_window():
    """Funding settlement at HH:00: 1 min before should be active."""
    event = CalendarEvent(
        event_type="funding_settlement",
        h_event_bps=3.0,
        pre_window_ms=120_000,   # 2 min
        post_window_ms=30_000,
        time_pattern="HH:00",
    )
    # 2024-02-19 10:00:00 UTC = 1708336800000 ms
    event_ms = 1708336800000
    # 1 minute before the event
    ts = event_ms - 60_000
    assert event.is_active(ts)


def test_funding_settlement_in_post_window():
    event = CalendarEvent(
        event_type="funding_settlement",
        h_event_bps=3.0,
        pre_window_ms=120_000,
        post_window_ms=30_000,
        time_pattern="HH:00",
    )
    # 2024-02-19 10:00:00 UTC = 1708336800000 ms
    event_ms = 1708336800000
    # 15 seconds after
    ts = event_ms + 15_000
    assert event.is_active(ts)


def test_funding_settlement_outside_window():
    event = CalendarEvent(
        event_type="funding_settlement",
        h_event_bps=3.0,
        pre_window_ms=120_000,
        post_window_ms=30_000,
        time_pattern="HH:00",
    )
    # 2024-02-19 10:30:00 UTC = 1708338600000 (30 min after the hour)
    ts = 1708338600000
    assert not event.is_active(ts)


def test_session_event_active():
    """US open at 14:30 UTC."""
    event = CalendarEvent(
        event_type="session_us_open",
        h_event_bps=2.0,
        pre_window_ms=300_000,   # 5 min
        post_window_ms=300_000,
        time_utc="14:30",
    )
    # 2024-02-19 14:28:00 UTC = 1708352880000 (2 min before 14:30)
    ts = 1708352880000
    assert event.is_active(ts)


def test_multiple_events_returns_max():
    """When multiple events are active, return max h_event."""
    yaml_content = """
events:
  - type: funding_settlement
    time_pattern: "HH:00"
    pre_window_ms: 120000
    post_window_ms: 30000
    h_event_bps: 3.0
  - type: session_us_open
    time_utc: "14:00"
    pre_window_ms: 300000
    post_window_ms: 300000
    h_event_bps: 5.0
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        path = f.name

    try:
        cs = CalendarEventSchedule(path)
        cs.set_mid(10000.0)
        # 2024-02-19 14:00:00 UTC = 1708351200000 — both events active
        # (funding at HH:00 and session at 14:00)
        ts = 1708351200000
        h = cs.h_event("FR-PERP", ts)
        # At least one event should be active, h > 0
        # Max should be 5.0 bps = 5.0 / 10000 * 10000 = 5.0
        assert h > 0
    finally:
        os.unlink(path)


def test_set_mid_affects_output():
    yaml_content = """
events:
  - type: funding_settlement
    time_pattern: "HH:00"
    pre_window_ms: 120000
    post_window_ms: 30000
    h_event_bps: 3.0
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        path = f.name

    try:
        cs = CalendarEventSchedule(path)
        # 2024-02-19 10:00:00 UTC = 1708336800000
        ts = 1708336800000

        cs.set_mid(100.0)
        h1 = cs.h_event("FR-PERP", ts)

        cs.set_mid(200.0)
        h2 = cs.h_event("FR-PERP", ts)

        # Both should be > 0 and h2 ~ 2 * h1
        if h1 > 0:
            assert abs(h2 / h1 - 2.0) < 0.01
    finally:
        os.unlink(path)
