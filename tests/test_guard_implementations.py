"""Tests for guard implementations — journal, judge, memory, pulse, radar, strategy."""
import tempfile
import time
import pytest
from unittest.mock import MagicMock

from modules.journal_engine import JournalEntry
from modules.journal_guard import JournalGuard
from modules.judge_guard import JudgeGuard
from modules.memory_engine import MemoryEvent
from modules.memory_guard import MemoryGuard
from modules.pulse_guard import PulseGuard
from modules.radar_guard import RadarGuard
from modules.strategy_guard import StrategyGuard


class TestJournalGuard:
    def test_log_and_read_entry(self):
        tmp = tempfile.mkdtemp()
        guard = JournalGuard(data_dir=tmp)
        entry = JournalEntry(
            instrument="ETH-PERP", direction="long",
            entry_price=2500.0, exit_price=2550.0,
            entry_ts=1000, close_ts=2000,
            pnl=50.0, roe_pct=2.0,
            close_reason="guard_close",
        )
        guard.log_entry(entry)
        entries = guard.read_entries()
        assert len(entries) == 1
        assert entries[0].instrument == "ETH-PERP"
        assert entries[0].pnl == 50.0

    def test_read_empty(self):
        tmp = tempfile.mkdtemp()
        guard = JournalGuard(data_dir=tmp)
        assert guard.read_entries() == []

    def test_read_with_date_filter(self):
        tmp = tempfile.mkdtemp()
        guard = JournalGuard(data_dir=tmp)
        entry = JournalEntry(
            instrument="ETH-PERP", direction="long",
            entry_price=2500.0, exit_price=2550.0,
            entry_ts=1000, close_ts=int(time.time() * 1000),
            pnl=50.0, roe_pct=2.0,
        )
        guard.log_entry(entry)
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries = guard.read_entries(date=today)
        assert len(entries) == 1

    def test_read_with_limit(self):
        tmp = tempfile.mkdtemp()
        guard = JournalGuard(data_dir=tmp)
        for i in range(10):
            entry = JournalEntry(
                instrument="ETH-PERP", direction="long",
                entry_price=2500.0 + i, exit_price=2550.0,
                entry_ts=1000, close_ts=2000 + i,
                pnl=50.0, roe_pct=2.0,
            )
            guard.log_entry(entry)
        entries = guard.read_entries(limit=5)
        assert len(entries) == 5


class TestJudgeGuard:
    def test_initialization(self):
        tmp = tempfile.mkdtemp()
        guard = JudgeGuard(data_dir=tmp)
        assert guard._engine is not None

    def test_save_report(self):
        tmp = tempfile.mkdtemp()
        guard = JudgeGuard(data_dir=tmp)
        from modules.judge_engine import JudgeReport
        report = JudgeReport(
            timestamp_ms=int(time.time() * 1000),
            round_trips_evaluated=10,
        )
        path = guard.save_report(report)
        assert path.exists()


class TestMemoryGuard:
    def test_log_and_read_event(self):
        tmp = tempfile.mkdtemp()
        guard = MemoryGuard(data_dir=tmp)
        event = MemoryEvent(
            event_type="session_start",
            timestamp_ms=int(time.time() * 1000),
            payload={"tick_count": 0},
        )
        guard.log_event(event)
        events = guard.read_events()
        assert len(events) == 1
        assert events[0].event_type == "session_start"

    def test_read_with_type_filter(self):
        tmp = tempfile.mkdtemp()
        guard = MemoryGuard(data_dir=tmp)
        now = int(time.time() * 1000)
        guard.log_event(MemoryEvent(event_type="start", timestamp_ms=now, payload={}))
        guard.log_event(MemoryEvent(event_type="trade", timestamp_ms=now + 1, payload={}))
        guard.log_event(MemoryEvent(event_type="start", timestamp_ms=now + 2, payload={}))

        starts = guard.read_events(event_type="start")
        assert len(starts) == 2
        trades = guard.read_events(event_type="trade")
        assert len(trades) == 1

    def test_read_empty(self):
        tmp = tempfile.mkdtemp()
        guard = MemoryGuard(data_dir=tmp)
        assert guard.read_events() == []


class TestPulseGuard:
    def test_initialization(self):
        guard = PulseGuard()
        assert guard.engine is not None
        assert guard.config is not None
        assert guard.last_result is None

    def test_config_defaults(self):
        guard = PulseGuard()
        assert guard.config.volume_min_24h > 0


class TestRadarGuard:
    def test_initialization(self):
        guard = RadarGuard()
        assert guard.engine is not None
        assert guard.config is not None
        assert guard.last_result is None

    def test_config_defaults(self):
        guard = RadarGuard()
        assert guard.config.scan_history_size > 0


class TestStrategyGuard:
    def test_init_empty(self):
        guard = StrategyGuard(strategy_names=[], enabled=True)
        assert guard.strategies == []
        assert guard.enabled is True

    def test_init_disabled(self):
        guard = StrategyGuard(enabled=False)
        assert guard.enabled is False

    def test_loads_valid_strategy(self):
        guard = StrategyGuard(strategy_names=["simple_mm"], enabled=True)
        assert len(guard.strategies) == 1

    def test_skips_invalid_strategy(self):
        guard = StrategyGuard(strategy_names=["nonexistent_xyz"], enabled=True)
        assert len(guard.strategies) == 0

    def test_multiple_strategies(self):
        guard = StrategyGuard(
            strategy_names=["simple_mm", "mean_reversion"],
            enabled=True,
        )
        assert len(guard.strategies) == 2
