"""Tests for trade journal engine."""
from modules.journal_engine import JournalEngine, JournalEntry


class TestJournalEntry:
    def test_roundtrip(self):
        e = JournalEntry(
            entry_id="ETH-PERP-1000",
            instrument="ETH-PERP",
            direction="long",
            pnl=10.5,
            roe_pct=5.25,
        )
        d = e.to_dict()
        e2 = JournalEntry.from_dict(d)
        assert e2.entry_id == "ETH-PERP-1000"
        assert e2.pnl == 10.5


class TestJournalEngine:
    def test_create_entry_radar(self):
        engine = JournalEngine()
        entry = engine.create_entry(
            instrument="ETH-PERP",
            direction="long",
            entry_price=3000.0,
            exit_price=3150.0,
            pnl=50.0,
            roe_pct=5.0,
            entry_source="radar",
            entry_signal_score=210.0,
            close_reason="guard_close",
            entry_ts=1000000,
            close_ts=4600000,
        )
        assert entry.entry_id == "ETH-PERP-1000000"
        assert entry.signal_quality == "good"  # High score + profitable
        assert "radar opportunity" in entry.entry_reasoning
        assert "Guard trailing stop" in entry.exit_reasoning
        assert entry.holding_ms == 3600000

    def test_create_entry_movers_loss(self):
        engine = JournalEngine()
        entry = engine.create_entry(
            instrument="SOL-PERP",
            direction="short",
            entry_price=100.0,
            exit_price=105.0,
            pnl=-25.0,
            roe_pct=-5.0,
            entry_source="movers_immediate",
            entry_signal_score=100.0,
            close_reason="conviction_collapse",
            entry_ts=1000000,
            close_ts=2800000,
        )
        assert entry.signal_quality == "poor"
        assert "false positive" in entry.retrospective.lower()

    def test_signal_quality_assessment(self):
        engine = JournalEngine()
        # Good: profitable + high score
        assert engine._assess_signal_quality("radar", 210, 50, 5) == "good"
        # Fair: profitable + low score
        assert engine._assess_signal_quality("radar", 160, 10, 1) == "fair"
        # Poor: high score but lost money
        assert engine._assess_signal_quality("radar", 250, -20, -3) == "poor"

    def test_nightly_review_empty(self):
        engine = JournalEngine()
        result = engine.compute_nightly_review([], [], date="2026-03-05")
        assert result.round_trips_today == 0
        assert result.date == "2026-03-05"
        assert "Nightly Review" in result.briefing_md

    def test_retrospective_dsl_profit(self):
        engine = JournalEngine()
        retro = engine._generate_retrospective("good", "radar", "guard_close", 8.0, 40.0)
        assert "working" in retro.lower()

    def test_retrospective_conviction_loss(self):
        engine = JournalEngine()
        retro = engine._generate_retrospective("poor", "movers_signal", "conviction_collapse", -3.0, -15.0)
        assert "tighter" in retro.lower() or "faster" in retro.lower()
