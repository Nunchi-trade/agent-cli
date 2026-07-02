"""Tests for Full Regime System (G3)."""
import datetime

from quoting_engine.config import (
    MarketConfig, SessionRegimeConfig, RegimeOverride, LadderParams,
)
from quoting_engine.engine import QuotingEngine


def _ts_for(year: int, month: int, day: int, hour: int, minute: int) -> int:
    dt = datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def _make_engine(session_cfg: SessionRegimeConfig, num_levels: int = 3) -> QuotingEngine:
    cfg = MarketConfig(
        session_regime=session_cfg,
        ladder=LadderParams(num_levels=num_levels),
    )
    engine = QuotingEngine(cfg)
    for _ in range(35):
        engine.tick(mid=100.0, bid=99.9, ask=100.1)
    return engine


def test_regime_open():
    """During OPEN session -> spread_mult=1.0, OPEN regime."""
    engine = _make_engine(SessionRegimeConfig(
        enabled=True,
        in_session_start_utc="14:30",
        in_session_end_utc="21:00",
        regimes={
            "OPEN": RegimeOverride(spread_mult=1.0),
            "CLOSE": RegimeOverride(spread_mult=3.0),
        },
    ))
    # Thursday 16:00 UTC -> in-session -> OPEN
    ts = _ts_for(2026, 1, 15, 16, 0)  # Thursday
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert r.meta["regime_name"] == "OPEN"
    assert r.meta["session_mult"] == 1.0


def test_regime_close():
    """During CLOSE (off-session) -> wider spread."""
    engine = _make_engine(SessionRegimeConfig(
        enabled=True,
        in_session_start_utc="14:30",
        in_session_end_utc="21:00",
        regimes={
            "CLOSE": RegimeOverride(spread_mult=3.0, size_mult=0.7),
        },
    ))
    # Thursday 08:00 UTC -> off-session -> CLOSE
    ts = _ts_for(2026, 1, 15, 8, 0)  # Thursday
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert r.meta["regime_name"] == "CLOSE"
    assert r.meta["session_mult"] == 3.0


def test_regime_weekend():
    """Saturday -> WEEKEND regime with wider spread and reduce_only."""
    engine = _make_engine(SessionRegimeConfig(
        enabled=True,
        weekend_days=[5, 6],
        regimes={
            "WEEKEND": RegimeOverride(spread_mult=5.0, size_mult=0.3, reduce_only=True),
        },
    ))
    # Saturday 12:00 UTC -> WEEKEND
    ts = _ts_for(2026, 1, 17, 12, 0)  # Saturday
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert r.meta["regime_name"] == "WEEKEND"
    assert r.meta["session_mult"] == 5.0
    assert r.reduce_only is True


def test_regime_reopen_window():
    """Monday 00:15 UTC -> REOPEN_WINDOW with moderate widening."""
    engine = _make_engine(SessionRegimeConfig(
        enabled=True,
        weekend_days=[5, 6],
        reopen_window_minutes=30,
        regimes={
            "REOPEN_WINDOW": RegimeOverride(spread_mult=2.0, size_mult=0.5),
        },
    ))
    # Monday 00:15 UTC -> within 30min reopen window
    ts = _ts_for(2026, 1, 19, 0, 15)  # Monday
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert r.meta["regime_name"] == "REOPEN_WINDOW"
    assert r.meta["session_mult"] == 2.0


def test_regime_backwards_compat():
    """When no regimes dict, fall back to simple off_session_spread_mult."""
    engine = _make_engine(SessionRegimeConfig(
        enabled=True,
        in_session_start_utc="14:30",
        in_session_end_utc="21:00",
        off_session_spread_mult=3.0,
        # No regimes dict -> backwards compat
    ))
    # Off-session
    ts = _ts_for(2026, 1, 15, 8, 0)  # Thursday 08:00
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert r.meta["regime_name"] == "CLOSE"
    assert r.meta["session_mult"] == 3.0

    # In-session
    ts2 = _ts_for(2026, 1, 15, 16, 0)  # Thursday 16:00
    r2 = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts2, timestamp_ms=ts2)
    assert r2.meta["regime_name"] == "OPEN"
    assert r2.meta["session_mult"] == 1.0


def test_regime_num_levels_override():
    """Regime can override number of ladder levels."""
    engine = _make_engine(SessionRegimeConfig(
        enabled=True,
        in_session_start_utc="14:30",
        in_session_end_utc="21:00",
        regimes={
            "CLOSE": RegimeOverride(spread_mult=2.0, num_levels=1),
        },
    ), num_levels=3)
    # Off-session -> CLOSE regime with num_levels=1
    ts = _ts_for(2026, 1, 15, 8, 0)
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert len(r.levels) == 1
