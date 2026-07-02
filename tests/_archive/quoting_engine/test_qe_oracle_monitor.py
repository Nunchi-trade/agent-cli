"""Tests for feeds/oracle_monitor.py — oracle freshness zones."""
from quoting_engine.feeds.oracle_monitor import (
    OracleFreshnessMonitor,
    OracleMonitorConfig,
)


def _make_monitor(**kwargs) -> OracleFreshnessMonitor:
    return OracleFreshnessMonitor(OracleMonitorConfig(**kwargs))


def test_fresh_zone():
    m = _make_monitor()
    s = m.check(oracle_timestamp_ms=1000, now_ms=2000)  # 1s old
    assert s.zone == "fresh"
    assert s.spread_mult == 1.0
    assert not s.reduce_only
    assert not s.halt


def test_warning_zone():
    m = _make_monitor(warning_ms=5000)
    s = m.check(oracle_timestamp_ms=1000, now_ms=7000)  # 6s old
    assert s.zone == "warning"
    assert s.spread_mult == 1.5
    assert not s.reduce_only
    assert not s.halt


def test_stale_zone():
    m = _make_monitor(stale_ms=15000)
    s = m.check(oracle_timestamp_ms=1000, now_ms=20000)  # 19s old
    assert s.zone == "stale"
    assert s.spread_mult == 3.0
    assert s.reduce_only
    assert not s.halt


def test_kill_zone():
    m = _make_monitor(kill_ms=60000)
    s = m.check(oracle_timestamp_ms=1000, now_ms=70000)  # 69s old
    assert s.zone == "kill"
    assert s.halt
    assert s.reduce_only


def test_exact_warning_boundary():
    m = _make_monitor(warning_ms=5000)
    s = m.check(oracle_timestamp_ms=1000, now_ms=6000)  # exactly 5s
    assert s.zone == "warning"


def test_exact_stale_boundary():
    m = _make_monitor(warning_ms=5000, stale_ms=15000)
    s = m.check(oracle_timestamp_ms=1000, now_ms=16000)  # exactly 15s
    assert s.zone == "stale"


def test_exact_kill_boundary():
    m = _make_monitor(kill_ms=60000)
    s = m.check(oracle_timestamp_ms=1000, now_ms=61000)  # exactly 60s
    assert s.zone == "kill"


def test_disabled_monitor():
    m = _make_monitor(enabled=False)
    s = m.check(oracle_timestamp_ms=0, now_ms=999999)
    assert s.zone == "fresh"
    assert s.spread_mult == 1.0
    assert not s.halt


def test_zero_timestamps():
    m = _make_monitor()
    s = m.check(oracle_timestamp_ms=0, now_ms=0)
    assert s.zone == "fresh"
    assert not s.halt


def test_negative_age_clamped():
    m = _make_monitor()
    # now_ms < oracle_timestamp — shouldn't happen but should be safe
    s = m.check(oracle_timestamp_ms=5000, now_ms=1000)
    assert s.age_ms == 0
    assert s.zone == "fresh"
