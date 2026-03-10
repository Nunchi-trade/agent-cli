"""Tests for agent memory engine."""
import time
from modules.memory_engine import (
    MemoryEngine, MemoryEvent, Playbook, PlaybookEntry,
)


class TestMemoryEvent:
    def test_roundtrip(self):
        e = MemoryEvent(
            event_type="param_change",
            timestamp_ms=1000,
            payload={"foo": "bar"},
            summary="test",
        )
        d = e.to_dict()
        e2 = MemoryEvent.from_dict(d)
        assert e2.event_type == "param_change"
        assert e2.payload == {"foo": "bar"}
        assert e2.summary == "test"


class TestPlaybook:
    def test_empty(self):
        p = Playbook()
        assert p.to_dict() == {}

    def test_roundtrip(self):
        p = Playbook()
        p.entries["ETH-PERP:scanner"] = PlaybookEntry(
            instrument="ETH-PERP", signal_source="scanner",
            trade_count=10, win_count=6, total_pnl=50.0, total_roe=25.0,
        )
        d = p.to_dict()
        p2 = Playbook.from_dict(d)
        e = p2.entries["ETH-PERP:scanner"]
        assert e.trade_count == 10
        assert e.win_rate == 60.0
        assert e.avg_roe == 2.5


class TestMemoryEngine:
    def test_create_param_change_event(self):
        from modules.howl_adapter import Adjustment
        engine = MemoryEngine()
        adj = [Adjustment(param="scanner_score_threshold", old_value=170, new_value=180, reason="test")]
        event = engine.create_param_change_event(adj)
        assert event.event_type == "param_change"
        assert "170->180" in event.summary
        assert event.payload["adjustments"][0]["param"] == "scanner_score_threshold"

    def test_create_session_event(self):
        engine = MemoryEngine()
        event = engine.create_session_event("session_start", tick_count=100, total_pnl=50.5)
        assert event.event_type == "session_start"
        assert "100 ticks" in event.summary
        assert event.payload["tick_count"] == 100

    def test_create_howl_event(self):
        engine = MemoryEngine()
        event = engine.create_howl_event(win_rate=65.0, net_pnl=120.5, fdr=8.0, round_trips=20)
        assert event.event_type == "howl_review"
        assert event.payload["round_trips"] == 20

    def test_create_notable_trade_event(self):
        engine = MemoryEngine()
        event = engine.create_notable_trade_event(
            instrument="ETH-PERP", direction="long", pnl=45.0,
            roe_pct=12.5, entry_source="scanner", close_reason="guard_close",
        )
        assert event.event_type == "notable_trade"
        assert "ETH-PERP" in event.summary

    def test_create_judge_event(self):
        engine = MemoryEngine()
        event = engine.create_judge_event(
            findings_count=3,
            false_positive_rates={"scanner": 25.0, "movers_immediate": 60.0},
            recommendations=["Disable movers auto-entry"],
        )
        assert event.event_type == "judge_finding"
        assert event.payload["findings_count"] == 3

    def test_update_playbook(self):
        engine = MemoryEngine()
        playbook = Playbook()

        slots = [
            {"instrument": "ETH-PERP", "entry_source": "scanner",
             "close_pnl": 10.0, "current_roe": 5.0, "entry_ts": 1000, "close_ts": 5000},
            {"instrument": "ETH-PERP", "entry_source": "scanner",
             "close_pnl": -5.0, "current_roe": -2.5, "entry_ts": 6000, "close_ts": 9000},
        ]

        playbook = engine.update_playbook(playbook, slots)
        entry = playbook.entries["ETH-PERP:scanner"]
        assert entry.trade_count == 2
        assert entry.win_count == 1
        assert entry.total_pnl == 5.0
        assert entry.win_rate == 50.0

    def test_query(self):
        events = [
            MemoryEvent(event_type="param_change", timestamp_ms=100),
            MemoryEvent(event_type="howl_review", timestamp_ms=200),
            MemoryEvent(event_type="param_change", timestamp_ms=300),
        ]
        result = MemoryEngine.query(events, event_type="param_change")
        assert len(result) == 2
        assert result[0].timestamp_ms == 300  # Most recent first

    def test_query_limit(self):
        events = [MemoryEvent(event_type="test", timestamp_ms=i) for i in range(50)]
        result = MemoryEngine.query(events, limit=5)
        assert len(result) == 5
