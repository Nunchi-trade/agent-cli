"""Tests for modules/apex_engine.py — decision engine with synthetic data."""
import pytest

from modules.apex_config import ApexConfig, APEX_PRESETS
from modules.apex_engine import ApexAction, ApexEngine
from modules.apex_state import ApexSlot, ApexState


def _make_state(max_slots=3, slots=None):
    """Build a ApexState with optional pre-configured slots."""
    state = ApexState.new(max_slots)
    if slots:
        for i, s in enumerate(slots):
            if i < len(state.slots):
                for k, v in s.items():
                    setattr(state.slots[i], k, v)
    return state


def _active_slot(slot_id=0, instrument="ETH-PERP", direction="long",
                 entry_price=2500.0, current_roe=0.0, **kwargs):
    """Build an active slot dict for _make_state."""
    d = {
        "slot_id": slot_id,
        "status": "active",
        "instrument": instrument,
        "direction": direction,
        "entry_price": entry_price,
        "entry_size": 1.0,
        "current_roe": current_roe,
        "high_water_roe": max(current_roe, 0),
        "entry_ts": 1000,
        "last_progress_ts": 1000,
        "last_signal_seen_ts": 1000,
        "signal_disappeared_ts": 0,
    }
    d.update(kwargs)
    return d


class TestRiskGate:
    def setup_method(self):
        self.engine = ApexEngine(ApexConfig(daily_loss_limit=500.0))

    def test_daily_loss_closes_all(self):
        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP"),
            _active_slot(1, "SOL-PERP"),
        ])
        state.daily_pnl = -500.0

        actions = self.engine.evaluate(state, [], [], {}, {})
        assert len(actions) == 2
        assert all(a.action == "exit" for a in actions)
        assert all(a.reason == "daily_loss_limit" for a in actions)

    def test_daily_loss_flag_closes_all(self):
        state = _make_state(slots=[_active_slot(0, "ETH-PERP")])
        state.daily_loss_triggered = True

        actions = self.engine.evaluate(state, [], [], {}, {})
        assert len(actions) == 1
        assert actions[0].reason == "daily_loss_limit"

    def test_no_actions_when_pnl_ok(self):
        state = _make_state(slots=[_active_slot(0, "ETH-PERP")])
        state.daily_pnl = -100.0

        actions = self.engine.evaluate(state, [], [], {}, {})
        # No exits triggered (no DSL, price ok, signals present-ish)
        assert all(a.action != "exit" or a.reason != "daily_loss_limit" for a in actions)


class TestExitLogic:
    def setup_method(self):
        self.engine = ApexEngine(ApexConfig(max_negative_roe=-5.0))

    def test_guard_close(self):
        state = _make_state(slots=[_active_slot(0, "ETH-PERP")])
        guard_results = {0: {"action": "close", "reason": "tier_breach"}}

        actions = self.engine.evaluate(state, [], [], {}, guard_results)
        exits = [a for a in actions if a.action == "exit"]
        assert len(exits) == 1
        assert "guard_close" in exits[0].reason

    def test_hard_stop(self):
        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", entry_price=2500.0, current_roe=-6.0),
        ])
        # Price that yields < -5% ROE
        prices = {0: 2350.0}

        actions = self.engine.evaluate(state, [], [], prices, {})
        exits = [a for a in actions if a.action == "exit"]
        assert len(exits) == 1
        assert "hard_stop" in exits[0].reason

    def test_no_hard_stop_above_threshold(self):
        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", entry_price=2500.0, current_roe=-3.0),
        ])
        prices = {0: 2490.0}  # small loss, above -5% with leverage

        actions = self.engine.evaluate(state, [], [], prices, {})
        exits = [a for a in actions if a.action == "exit" and "hard_stop" in a.reason]
        assert len(exits) == 0

    def test_conviction_collapse(self):
        """Signal disappeared + negative ROE + timeout → exit."""
        cfg = ApexConfig(conviction_collapse_minutes=30)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        disappeared_ts = now_ms - 31 * 60_000  # 31 min ago

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", current_roe=-2.0,
                         signal_disappeared_ts=disappeared_ts),
        ])

        # No matching signals
        actions = engine.evaluate(state, [], [], {}, {}, now_ms=now_ms)
        exits = [a for a in actions if a.action == "exit" and "conviction_collapse" in a.reason]
        assert len(exits) == 1

    def test_no_conviction_collapse_when_signal_present(self):
        cfg = ApexConfig(conviction_collapse_minutes=30)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", current_roe=-2.0,
                         signal_disappeared_ts=now_ms - 60 * 60_000),
        ])

        # ETH signal is still present
        movers = [{"asset": "ETH", "signal_type": "OI_BREAKOUT", "direction": "LONG", "confidence": 80}]
        actions = engine.evaluate(state, movers, [], {}, {}, now_ms=now_ms)
        exits = [a for a in actions if a.action == "exit" and "conviction_collapse" in a.reason]
        assert len(exits) == 0

    def test_stagnation_tp(self):
        cfg = ApexConfig(stagnation_minutes=60, stagnation_min_roe=3.0)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        stale_ts = now_ms - 61 * 60_000  # 61 min ago

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", current_roe=5.0,
                         high_water_roe=5.0, last_progress_ts=stale_ts),
        ])

        actions = engine.evaluate(state, [], [], {}, {}, now_ms=now_ms)
        exits = [a for a in actions if a.action == "exit" and "stagnation" in a.reason]
        assert len(exits) == 1


class TestEntryLogic:
    def setup_method(self):
        self.engine = ApexEngine(ApexConfig(
            radar_score_threshold=170,
            pulse_confidence_threshold=70.0,
        ))

    def test_pulse_immediate_entry(self):
        state = _make_state()
        movers = [{
            "asset": "ETH",
            "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG",
            "confidence": 100,
        }]

        actions = self.engine.evaluate(state, movers, [], {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].instrument == "ETH-PERP"
        assert entries[0].source == "pulse_immediate"

    def test_radar_entry(self):
        state = _make_state()
        scanner = [{
            "asset": "SOL",
            "direction": "LONG",
            "final_score": 185,
        }]

        actions = self.engine.evaluate(state, [], scanner, {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].instrument == "SOL-PERP"
        assert entries[0].source == "radar"

    def test_radar_below_threshold_skipped(self):
        state = _make_state()
        scanner = [{"asset": "SOL", "direction": "LONG", "final_score": 150}]

        actions = self.engine.evaluate(state, [], scanner, {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 0

    def test_pulse_signal_entry(self):
        state = _make_state()
        movers = [{
            "asset": "DOGE",
            "signal_type": "OI_BREAKOUT",
            "direction": "LONG",
            "confidence": 80,
        }]

        actions = self.engine.evaluate(state, movers, [], {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].source == "pulse_signal"

    def test_pulse_signal_below_threshold_skipped(self):
        state = _make_state()
        movers = [{
            "asset": "DOGE",
            "signal_type": "OI_BREAKOUT",
            "direction": "LONG",
            "confidence": 50,
        }]

        actions = self.engine.evaluate(state, movers, [], {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 0

    def test_no_duplicate_instrument_entry(self):
        state = _make_state(slots=[_active_slot(0, "ETH-PERP")])
        movers = [{
            "asset": "ETH",
            "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG",
            "confidence": 100,
        }]

        actions = self.engine.evaluate(state, movers, [], {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 0

    def test_direction_limit(self):
        cfg = ApexConfig(max_same_direction=2)
        engine = ApexEngine(cfg)

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", direction="long"),
            _active_slot(1, "SOL-PERP", direction="long"),
        ])
        movers = [{
            "asset": "DOGE",
            "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG",
            "confidence": 100,
        }]

        actions = engine.evaluate(state, movers, [], {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 0  # Already 2 longs

    def test_fills_multiple_slots(self):
        state = _make_state()
        movers = [
            {"asset": "ETH", "signal_type": "IMMEDIATE_MOVER", "direction": "LONG", "confidence": 100},
            {"asset": "SOL", "signal_type": "IMMEDIATE_MOVER", "direction": "SHORT", "confidence": 100},
        ]

        actions = self.engine.evaluate(state, movers, [], {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 2
        instruments = {e.instrument for e in entries}
        assert instruments == {"ETH-PERP", "SOL-PERP"}

    def test_no_slots_available(self):
        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP"),
            _active_slot(1, "SOL-PERP"),
            _active_slot(2, "DOGE-PERP"),
        ])
        movers = [{
            "asset": "BTC",
            "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG",
            "confidence": 100,
        }]

        actions = self.engine.evaluate(state, movers, [], {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 0

    def test_excluded_instruments(self):
        cfg = ApexConfig(excluded_instruments=["MEME-PERP"])
        engine = ApexEngine(cfg)

        state = _make_state()
        movers = [{
            "asset": "MEME",
            "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG",
            "confidence": 100,
        }]

        actions = engine.evaluate(state, movers, [], {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 0


class TestPriorityOrder:
    def test_pulse_immediate_before_radar(self):
        engine = ApexEngine(ApexConfig(max_slots=1))
        state = _make_state(max_slots=1)

        movers = [{
            "asset": "ETH", "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG", "confidence": 100,
        }]
        scanner = [{
            "asset": "SOL", "direction": "LONG", "final_score": 200,
        }]

        actions = engine.evaluate(state, movers, scanner, {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].instrument == "ETH-PERP"
        assert entries[0].source == "pulse_immediate"

    def test_radar_before_pulse_signal(self):
        engine = ApexEngine(ApexConfig(max_slots=1))
        state = _make_state(max_slots=1)

        movers = [{
            "asset": "DOGE", "signal_type": "OI_BREAKOUT",
            "direction": "LONG", "confidence": 80,
        }]
        scanner = [{
            "asset": "SOL", "direction": "LONG", "final_score": 190,
        }]

        actions = engine.evaluate(state, movers, scanner, {}, {})
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].instrument == "SOL-PERP"
        assert entries[0].source == "radar"


class TestSlotManagement:
    def test_empty_state(self):
        engine = ApexEngine(ApexConfig())
        state = _make_state()
        actions = engine.evaluate(state, [], [], {}, {})
        assert actions == []

    def test_entry_marks_slot_entering(self):
        """After evaluation, entered slot should be marked as entering."""
        engine = ApexEngine(ApexConfig())
        state = _make_state()

        movers = [{
            "asset": "ETH", "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG", "confidence": 100,
        }]

        engine.evaluate(state, movers, [], {}, {})
        # The engine marks slot as "entering" to prevent double-allocation
        used_slot = state.slots[0]
        assert used_slot.status == "entering"
        assert used_slot.instrument == "ETH-PERP"


class TestROEUpdate:
    def test_long_roe_calculation(self):
        cfg = ApexConfig(leverage=10.0)
        engine = ApexEngine(cfg)

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", direction="long", entry_price=2500.0),
        ])

        # Price goes to 2525 = +1% raw, +10% ROE with 10x
        prices = {0: 2525.0}
        engine.evaluate(state, [], [], prices, {})

        assert state.slots[0].current_roe == pytest.approx(10.0)

    def test_short_roe_calculation(self):
        cfg = ApexConfig(leverage=10.0)
        engine = ApexEngine(cfg)

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", direction="short", entry_price=2500.0),
        ])

        # Price goes to 2475 = -1% raw = +10% ROE for short
        prices = {0: 2475.0}
        engine.evaluate(state, [], [], prices, {})

        assert state.slots[0].current_roe == pytest.approx(10.0)

    def test_high_water_mark_updates(self):
        cfg = ApexConfig(leverage=10.0)
        engine = ApexEngine(cfg)

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", direction="long", entry_price=2500.0,
                         high_water_roe=5.0),
        ])

        prices = {0: 2525.0}  # 10% ROE
        engine.evaluate(state, [], [], prices, {})

        assert state.slots[0].high_water_roe == pytest.approx(10.0)


class TestConfigPresets:
    def test_presets_exist(self):
        assert "default" in APEX_PRESETS
        assert "conservative" in APEX_PRESETS
        assert "aggressive" in APEX_PRESETS

    def test_conservative_tighter(self):
        default = APEX_PRESETS["default"]
        conservative = APEX_PRESETS["conservative"]
        assert conservative.max_slots <= default.max_slots
        assert conservative.leverage <= default.leverage
        assert conservative.daily_loss_limit <= default.daily_loss_limit

    def test_aggressive_looser(self):
        default = APEX_PRESETS["default"]
        aggressive = APEX_PRESETS["aggressive"]
        assert aggressive.leverage >= default.leverage
        assert aggressive.radar_score_threshold <= default.radar_score_threshold
        assert aggressive.daily_loss_limit >= default.daily_loss_limit

    def test_margin_auto_computed(self):
        cfg = ApexConfig(total_budget=10_000, max_slots=5)
        assert cfg.margin_per_slot == pytest.approx(2000.0)


class TestMinHoldTime:
    """Phase 3d — min hold blocks conviction/stagnation but NOT safety exits."""

    def test_min_hold_blocks_conviction_collapse(self):
        """Conviction collapse exit should be blocked when under min hold."""
        cfg = ApexConfig(conviction_collapse_minutes=30, min_hold_ms=2_700_000)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        # Entry was 20 min ago — still under 45 min hold
        entry_ts = now_ms - 20 * 60_000
        disappeared_ts = now_ms - 31 * 60_000  # signal gone 31 min

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", current_roe=-2.0,
                         entry_ts=entry_ts,
                         signal_disappeared_ts=disappeared_ts),
        ])

        actions = engine.evaluate(state, [], [], {}, {}, now_ms=now_ms)
        exits = [a for a in actions if a.action == "exit" and "conviction_collapse" in a.reason]
        assert len(exits) == 0

    def test_min_hold_does_not_block_guard_close(self):
        """GUARD close must always fire, even under min hold."""
        cfg = ApexConfig(min_hold_ms=2_700_000)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        entry_ts = now_ms - 10 * 60_000  # 10 min ago — under hold

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", entry_ts=entry_ts),
        ])
        guard_results = {0: {"action": "close", "reason": "tier_breach"}}

        actions = engine.evaluate(state, [], [], {}, guard_results, now_ms=now_ms)
        exits = [a for a in actions if a.action == "exit" and "guard_close" in a.reason]
        assert len(exits) == 1

    def test_min_hold_does_not_block_daily_loss(self):
        """Daily loss limit must always fire, even under min hold."""
        cfg = ApexConfig(min_hold_ms=2_700_000, daily_loss_limit=500.0)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        entry_ts = now_ms - 10 * 60_000  # 10 min ago

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", entry_ts=entry_ts),
        ])
        state.daily_pnl = -500.0

        actions = engine.evaluate(state, [], [], {}, {}, now_ms=now_ms)
        exits = [a for a in actions if a.action == "exit"]
        assert len(exits) == 1
        assert exits[0].reason == "daily_loss_limit"

    def test_min_hold_allows_exit_after_expiry(self):
        """After min hold expires, conviction collapse should fire normally."""
        cfg = ApexConfig(conviction_collapse_minutes=30, min_hold_ms=2_700_000)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        # Entry was 50 min ago — past the 45 min hold
        entry_ts = now_ms - 50 * 60_000
        disappeared_ts = now_ms - 31 * 60_000

        state = _make_state(slots=[
            _active_slot(0, "ETH-PERP", current_roe=-2.0,
                         entry_ts=entry_ts,
                         signal_disappeared_ts=disappeared_ts),
        ])

        actions = engine.evaluate(state, [], [], {}, {}, now_ms=now_ms)
        exits = [a for a in actions if a.action == "exit" and "conviction_collapse" in a.reason]
        assert len(exits) == 1


class TestSlotCooldown:
    """Phase 3d — slot cooldown prevents immediate reuse after close."""

    def test_cooldown_prevents_reuse_too_early(self):
        """A slot closed 2 min ago should not be available (cooldown=5min)."""
        cfg = ApexConfig(slot_cooldown_ms=300_000)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        state = _make_state(max_slots=1)
        # Slot 0 is empty but was closed 2 min ago
        state.slots[0].status = "empty"
        state.slots[0].close_ts = now_ms - 2 * 60_000

        movers = [{
            "asset": "ETH", "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG", "confidence": 100,
        }]

        actions = engine.evaluate(state, movers, [], {}, {}, now_ms=now_ms)
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 0

    def test_cooldown_allows_reuse_after_expiry(self):
        """A slot closed 6 min ago should be available (cooldown=5min)."""
        cfg = ApexConfig(slot_cooldown_ms=300_000)
        engine = ApexEngine(cfg)

        now_ms = 100_000_000
        state = _make_state(max_slots=1)
        # Slot 0 is empty and was closed 6 min ago
        state.slots[0].status = "empty"
        state.slots[0].close_ts = now_ms - 6 * 60_000

        movers = [{
            "asset": "ETH", "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG", "confidence": 100,
        }]

        actions = engine.evaluate(state, movers, [], {}, {}, now_ms=now_ms)
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].instrument == "ETH-PERP"
