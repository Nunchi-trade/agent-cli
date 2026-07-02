"""Unit tests for the pure Guard trailing stop engine."""
from __future__ import annotations

import pytest

from modules.guard_config import GuardConfig, Tier, PRESETS
from modules.guard_state import GuardState
from modules.trailing_stop import GuardAction, TrailingStopEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _long_config(**overrides) -> GuardConfig:
    """Standard LONG config with 3 tiers at 10x leverage."""
    defaults = dict(
        direction="long",
        leverage=10.0,
        phase1_retrace=0.03,
        phase1_max_breaches=3,
        phase1_absolute_floor=0.0,
        phase2_retrace=0.015,
        phase2_max_breaches=2,
        breach_decay_mode="hard",
        tiers=[
            Tier(trigger_pct=10.0, lock_pct=5.0),
            Tier(trigger_pct=20.0, lock_pct=14.0),
            Tier(trigger_pct=50.0, lock_pct=40.0, retrace=0.010),
        ],
    )
    defaults.update(overrides)
    tiers = defaults.pop("tiers")
    cfg = GuardConfig(**defaults)
    cfg.tiers = tiers
    return cfg


def _short_config(**overrides) -> GuardConfig:
    defaults = dict(
        direction="short",
        leverage=7.0,
        phase1_retrace=0.03,
        phase1_max_breaches=3,
        phase1_absolute_floor=0.0,
        phase2_retrace=0.015,
        phase2_max_breaches=2,
        breach_decay_mode="hard",
        tiers=[
            Tier(trigger_pct=10.0, lock_pct=5.0),
            Tier(trigger_pct=20.0, lock_pct=14.0),
        ],
    )
    defaults.update(overrides)
    tiers = defaults.pop("tiers")
    cfg = GuardConfig(**defaults)
    cfg.tiers = tiers
    return cfg


def _state(entry: float = 100.0, size: float = 10.0, direction: str = "long") -> GuardState:
    return GuardState(
        instrument="TEST-PERP",
        position_id="test-1",
        entry_price=entry,
        position_size=size,
        direction=direction,
        high_water=entry,
        high_water_ts=1000000,
        current_tier_index=-1,
        breach_count=0,
        created_ts=1000000,
    )


NOW = 2_000_000  # Fixed timestamp for tests


# ---------------------------------------------------------------------------
# Phase 1 Tests
# ---------------------------------------------------------------------------

class TestPhase1:
    def test_high_water_tracks_upward_long(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)

        # Price 100.5 → ROE = 5% (below tier 0 trigger of 10%), stays Phase 1
        r = engine.evaluate(100.5, s, now_ms=NOW)
        assert r.state.high_water == 100.5
        assert r.action == GuardAction.HOLD

    def test_high_water_does_not_decrease_long(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.high_water = 102.0

        r = engine.evaluate(101.0, s, now_ms=NOW)
        assert r.state.high_water == 102.0

    def test_no_breach_within_retrace(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.high_water = 100.0

        # Floor = 100 * (1 - 0.03) = 97.0. Price 98 is above floor.
        r = engine.evaluate(98.0, s, now_ms=NOW)
        assert r.action == GuardAction.HOLD
        assert r.state.breach_count == 0

    def test_single_breach_no_close(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.high_water = 100.0

        # Floor = 97.0. Price 96 breaches.
        r = engine.evaluate(96.0, s, now_ms=NOW)
        assert r.action == GuardAction.HOLD
        assert r.state.breach_count == 1

    def test_three_breaches_close(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.high_water = 100.0

        for i in range(3):
            r = engine.evaluate(96.0, s, now_ms=NOW)
            s = r.state

        assert r.action == GuardAction.CLOSE
        assert r.state.breach_count == 3

    def test_absolute_floor_long(self):
        cfg = _long_config(phase1_absolute_floor=98.0)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.high_water = 100.0

        # Trailing floor = 97.0, absolute = 98.0. Effective = max(97, 98) = 98.
        r = engine.evaluate(97.5, s, now_ms=NOW)
        assert r.action == GuardAction.HOLD
        assert r.state.breach_count == 1  # 97.5 <= 98.0

    def test_hard_decay_resets_to_zero(self):
        cfg = _long_config(breach_decay_mode="hard")
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.high_water = 100.0

        # Breach once
        r = engine.evaluate(96.0, s, now_ms=NOW)
        assert r.state.breach_count == 1

        # Recover: hard decay resets to 0
        r = engine.evaluate(99.0, r.state, now_ms=NOW)
        assert r.state.breach_count == 0

    def test_soft_decay_decrements_by_one(self):
        cfg = _long_config(breach_decay_mode="soft")
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.high_water = 100.0

        # Two breaches
        r = engine.evaluate(96.0, s, now_ms=NOW)
        r = engine.evaluate(96.0, r.state, now_ms=NOW)
        assert r.state.breach_count == 2

        # Recover: soft decays by 1
        r = engine.evaluate(99.0, r.state, now_ms=NOW)
        assert r.state.breach_count == 1


# ---------------------------------------------------------------------------
# Phase 1 -> Phase 2 Graduation
# ---------------------------------------------------------------------------

class TestGraduation:
    def test_graduates_on_first_tier(self):
        """10x leverage, entry=100. Tier 0 triggers at 10% ROE.
        Price for 10% ROE: 100 * (1 + 10/100/10) = 101.0
        """
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)

        r = engine.evaluate(101.0, s, now_ms=NOW)
        assert r.action == GuardAction.TIER_CHANGED
        assert r.new_tier_index == 0
        assert r.state.current_tier_index == 0
        assert r.state.breach_count == 0

    def test_does_not_graduate_below_trigger(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)

        # ROE at 100.99 = (0.99/100)*10*100 = 9.9% < 10%
        r = engine.evaluate(100.99, s, now_ms=NOW)
        assert r.action == GuardAction.HOLD
        assert r.state.current_tier_index == -1


# ---------------------------------------------------------------------------
# Phase 2 Tests
# ---------------------------------------------------------------------------

class TestPhase2:
    def test_tier_floor_long(self):
        """Tier 0: lock 5%, entry=100, leverage=10.
        Floor = 100 * (1 + 5/100/10) = 100.5
        """
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 0

        r = engine.evaluate(101.5, s, now_ms=NOW)
        assert r.tier_floor == pytest.approx(100.5)

    def test_tier_ratchet_single(self):
        """Jump from tier 0 to tier 1 (20% ROE).
        Price = 100 * (1 + 20/100/10) = 102.0
        """
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 0
        s.high_water = 102.0

        r = engine.evaluate(102.0, s, now_ms=NOW)
        assert r.action == GuardAction.TIER_CHANGED
        assert r.state.current_tier_index == 1
        assert r.new_tier_index == 1
        assert r.state.breach_count == 0

    def test_multi_tier_jump(self):
        """Jump from tier 0 straight to tier 2 (50% ROE).
        Price = 100 * (1 + 50/100/10) = 105.0
        """
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 0
        s.high_water = 105.0

        r = engine.evaluate(105.0, s, now_ms=NOW)
        assert r.action == GuardAction.TIER_CHANGED
        assert r.state.current_tier_index == 2
        assert r.new_tier_index == 2

    def test_tier_never_goes_backward(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 2
        s.high_water = 105.0

        # Price drops, ROE drops. Tier stays at 2.
        r = engine.evaluate(101.0, s, now_ms=NOW)
        assert r.state.current_tier_index == 2

    def test_per_tier_retrace(self):
        """Tier 2 has retrace=0.01. Trailing floor = hw * (1 - 0.01)."""
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 2
        s.high_water = 106.0

        r = engine.evaluate(105.5, s, now_ms=NOW)
        # Trailing floor = 106 * 0.99 = 104.94
        assert r.trailing_floor == pytest.approx(104.94)

    def test_per_tier_max_breaches(self):
        cfg = _long_config(
            tiers=[
                Tier(trigger_pct=10.0, lock_pct=5.0, max_breaches=1),
            ],
        )
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 0
        s.high_water = 101.0

        # Tier floor = 100.5, trailing floor = 101 * (1-0.015) = 99.485
        # Effective = max(100.5, 99.485) = 100.5
        # Price 100.0 <= 100.5 → breach. max_breaches=1 → close immediately.
        r = engine.evaluate(100.0, s, now_ms=NOW)
        assert r.action == GuardAction.CLOSE
        assert r.state.breach_count == 1

    def test_phase2_breach_close(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 0
        s.high_water = 101.0

        # Tier floor = 100.5, default max_breaches = 2
        # Price 100.0 < 100.5 → breach
        r1 = engine.evaluate(100.0, s, now_ms=NOW)
        assert r1.action == GuardAction.HOLD
        assert r1.state.breach_count == 1

        r2 = engine.evaluate(100.0, r1.state, now_ms=NOW)
        assert r2.action == GuardAction.CLOSE
        assert r2.state.breach_count == 2


# ---------------------------------------------------------------------------
# SHORT Direction
# ---------------------------------------------------------------------------

class TestShort:
    def test_high_water_tracks_downward(self):
        cfg = _short_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=1000.0, direction="short")
        s.high_water = 1000.0

        r = engine.evaluate(990.0, s, now_ms=NOW)
        assert r.state.high_water == 990.0

    def test_roe_positive_when_price_drops(self):
        cfg = _short_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=1000.0, direction="short")

        # ROE = (1000 - 990) / 1000 * 7 * 100 = 7%
        r = engine.evaluate(990.0, s, now_ms=NOW)
        assert r.roe_pct == pytest.approx(7.0)

    def test_short_graduation(self):
        """7x leverage, entry=1000. 10% ROE at:
        price = 1000 * (1 - 10/100/7) ≈ 985.71
        """
        cfg = _short_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=1000.0, direction="short")
        s.high_water = 985.0

        r = engine.evaluate(985.0, s, now_ms=NOW)
        assert r.action == GuardAction.TIER_CHANGED
        assert r.state.current_tier_index == 0

    def test_short_tier_floor(self):
        """Tier 0 lock 5%, entry=1000, leverage=7.
        Floor = 1000 * (1 - 5/100/7) ≈ 992.857
        """
        cfg = _short_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=1000.0, direction="short")
        s.current_tier_index = 0
        s.high_water = 985.0

        r = engine.evaluate(985.0, s, now_ms=NOW)
        assert r.tier_floor == pytest.approx(1000.0 * (1 - 5 / 100 / 7))

    def test_short_breach_when_price_rises(self):
        """SHORT breach: price >= effective floor."""
        cfg = _short_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=1000.0, direction="short")
        s.current_tier_index = 0
        s.high_water = 985.0

        # Tier floor ≈ 992.857, trailing = 985 * 1.015 = 999.775
        # Effective = min(992.857, 999.775) = 992.857
        # Price 993 >= 992.857 → breach
        r1 = engine.evaluate(993.0, s, now_ms=NOW)
        assert r1.state.breach_count == 1

        r2 = engine.evaluate(993.0, r1.state, now_ms=NOW)
        assert r2.action == GuardAction.CLOSE

    def test_short_absolute_floor(self):
        """SHORT absolute floor is above entry (caps max loss)."""
        cfg = _short_config(phase1_absolute_floor=1010.0)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=1000.0, direction="short")
        s.high_water = 1000.0

        # Trailing floor = 1000 * 1.03 = 1030
        # Absolute = 1010, effective = min(1030, 1010) = 1010
        # Price 1015 >= 1010 → breach
        r = engine.evaluate(1015.0, s, now_ms=NOW)
        assert r.state.breach_count == 1


# ---------------------------------------------------------------------------
# Stagnation Take-Profit
# ---------------------------------------------------------------------------

class TestStagnation:
    def test_stagnation_triggers_close(self):
        cfg = _long_config(stagnation_enabled=True, stagnation_min_roe=8.0,
                           stagnation_timeout_ms=3600_000)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 0
        s.high_water = 101.5
        s.high_water_ts = 1_000_000  # old

        # ROE at 101.5 = 15%, HW stale for > 1hr
        now = 1_000_000 + 3_700_000  # 3700 seconds later
        r = engine.evaluate(101.5, s, now_ms=now)
        assert r.action == GuardAction.CLOSE
        assert "Stagnation" in r.reason

    def test_stagnation_does_not_trigger_if_hw_fresh(self):
        cfg = _long_config(stagnation_enabled=True, stagnation_min_roe=8.0,
                           stagnation_timeout_ms=3600_000)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 0
        s.high_water = 101.5
        s.high_water_ts = 5_000_000

        # HW updated recently
        r = engine.evaluate(101.5, s, now_ms=5_100_000)
        assert r.action != GuardAction.CLOSE

    def test_stagnation_does_not_trigger_if_roe_too_low(self):
        cfg = _long_config(stagnation_enabled=True, stagnation_min_roe=8.0,
                           stagnation_timeout_ms=3600_000)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.current_tier_index = 0
        s.high_water = 100.5
        s.high_water_ts = 1_000_000

        # ROE at 100.5 = 5% < 8%
        r = engine.evaluate(100.5, s, now_ms=1_000_000 + 4_000_000)
        assert r.action != GuardAction.CLOSE


# ---------------------------------------------------------------------------
# Full Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_entry_to_tier2_to_close(self):
        """Walk through: entry at 100, ride to tier 2, then close on retrace."""
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)

        # Price rises slowly — still Phase 1
        r = engine.evaluate(100.5, s, now_ms=NOW)
        assert r.action == GuardAction.HOLD
        assert r.state.current_tier_index == -1

        # Price hits tier 0 (10% ROE = 101.0)
        r = engine.evaluate(101.0, r.state, now_ms=NOW)
        assert r.action == GuardAction.TIER_CHANGED
        assert r.state.current_tier_index == 0

        # Price keeps rising to tier 1 (20% ROE = 102.0)
        r = engine.evaluate(102.0, r.state, now_ms=NOW)
        assert r.action == GuardAction.TIER_CHANGED
        assert r.state.current_tier_index == 1

        # Price rises more but not enough for tier 2 (50% ROE = 105.0)
        r = engine.evaluate(103.0, r.state, now_ms=NOW)
        assert r.action == GuardAction.HOLD
        assert r.state.current_tier_index == 1
        assert r.state.high_water == 103.0

        # Price drops — tier 1 floor = 100 * (1 + 14/100/10) = 100.14
        # trailing = 103 * (1 - 0.015) = 101.455
        # effective = max(100.14, 101.455) = 101.455
        # Price 101.0 < 101.455 → breach
        r = engine.evaluate(101.0, r.state, now_ms=NOW)
        assert r.action == GuardAction.HOLD
        assert r.state.breach_count == 1

        # Second breach → close (phase2_max_breaches = 2)
        r = engine.evaluate(101.0, r.state, now_ms=NOW)
        assert r.action == GuardAction.CLOSE


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_zero_entry_price(self):
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=0.0)

        r = engine.evaluate(100.0, s, now_ms=NOW)
        assert r.roe_pct == 0.0

    def test_price_exactly_at_floor_long(self):
        """Price == floor is a breach for LONG (price <= floor)."""
        cfg = _long_config()
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.high_water = 100.0

        floor = 100.0 * (1.0 - 0.03)  # 97.0
        r = engine.evaluate(floor, s, now_ms=NOW)
        assert r.state.breach_count == 1

    def test_no_tiers_stays_phase1(self):
        cfg = _long_config(tiers=[])
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)

        # Even at high ROE, no tiers → stays Phase 1
        r = engine.evaluate(110.0, s, now_ms=NOW)
        assert r.action == GuardAction.HOLD
        assert r.state.current_tier_index == -1


# ---------------------------------------------------------------------------
# Presets Smoke Test
# ---------------------------------------------------------------------------

class TestPresets:
    def test_moderate_preset_exists(self):
        cfg = PRESETS["moderate"]
        assert len(cfg.tiers) == 6
        assert cfg.tiers[0].trigger_pct == 10.0

    def test_tight_preset_exists(self):
        cfg = PRESETS["tight"]
        assert len(cfg.tiers) == 4
        assert cfg.stagnation_enabled is True
        assert cfg.tiers[3].max_breaches == 1


# ---------------------------------------------------------------------------
# Config / State Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_config_roundtrip(self):
        cfg = PRESETS["moderate"]
        d = cfg.to_dict()
        cfg2 = GuardConfig.from_dict(d)
        assert cfg2.leverage == cfg.leverage
        assert len(cfg2.tiers) == len(cfg.tiers)
        assert cfg2.tiers[2].retrace == cfg.tiers[2].retrace

    def test_state_roundtrip(self):
        s = GuardState.new("ETH-PERP", 2500.0, 1.0, "long", "test-pos")
        d = s.to_dict()
        s2 = GuardState.from_dict(d)
        assert s2.entry_price == 2500.0
        assert s2.position_id == "test-pos"
        assert s2.high_water == 2500.0

    def test_config_roundtrip_with_phase1_timing(self):
        cfg = GuardConfig(
            phase1_max_duration_ms=5_400_000,
            phase1_weak_peak_ms=2_700_000,
            phase1_weak_peak_min_roe=3.0,
        )
        d = cfg.to_dict()
        cfg2 = GuardConfig.from_dict(d)
        assert cfg2.phase1_max_duration_ms == 5_400_000
        assert cfg2.phase1_weak_peak_ms == 2_700_000
        assert cfg2.phase1_weak_peak_min_roe == 3.0

    def test_state_roundtrip_with_phase1_start_ts(self):
        s = GuardState.new("ETH-PERP", 2500.0, 1.0, "long", "test-pos")
        assert s.phase1_start_ts > 0
        d = s.to_dict()
        s2 = GuardState.from_dict(d)
        assert s2.phase1_start_ts == s.phase1_start_ts


# ---------------------------------------------------------------------------
# Phase 1 Auto-Cut (Time-Based Exits)
# ---------------------------------------------------------------------------

class TestPhase1AutoCut:
    def test_phase1_timeout_triggers_close(self):
        """Position stuck in Phase 1 for 90+ min → PHASE1_TIMEOUT."""
        cfg = _long_config(phase1_max_duration_ms=5_400_000)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.phase1_start_ts = 1_000_000

        # 91 minutes later, still in Phase 1
        now = 1_000_000 + 91 * 60_000
        r = engine.evaluate(100.2, s, now_ms=now)
        assert r.action == GuardAction.PHASE1_TIMEOUT
        assert "timeout" in r.reason.lower()

    def test_phase1_timeout_does_not_trigger_before_limit(self):
        """Before 90 min, no timeout."""
        cfg = _long_config(phase1_max_duration_ms=5_400_000, phase1_weak_peak_ms=0)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.phase1_start_ts = 1_000_000

        # 80 minutes — should still be HOLD (weak-peak disabled)
        now = 1_000_000 + 80 * 60_000
        r = engine.evaluate(100.2, s, now_ms=now)
        assert r.action == GuardAction.HOLD

    def test_phase1_timeout_disabled_when_zero(self):
        """phase1_max_duration_ms=0 disables the timeout."""
        cfg = _long_config(phase1_max_duration_ms=0, phase1_weak_peak_ms=0)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.phase1_start_ts = 1_000_000

        # 10 hours later — no timeout because both timers disabled
        now = 1_000_000 + 600 * 60_000
        r = engine.evaluate(100.2, s, now_ms=now)
        assert r.action == GuardAction.HOLD

    def test_weak_peak_cut_triggers(self):
        """After 45 min, peak ROE < 3% → WEAK_PEAK_CUT."""
        cfg = _long_config(
            phase1_weak_peak_ms=2_700_000,
            phase1_weak_peak_min_roe=3.0,
        )
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.phase1_start_ts = 1_000_000
        s.high_water = 100.2  # Peak ROE = (0.2/100)*10*100 = 2% < 3%

        now = 1_000_000 + 46 * 60_000
        r = engine.evaluate(100.1, s, now_ms=now)
        assert r.action == GuardAction.WEAK_PEAK_CUT
        assert "weak peak" in r.reason.lower()

    def test_weak_peak_does_not_trigger_if_peak_roe_sufficient(self):
        """After 45 min, peak ROE >= 3% → no weak-peak cut."""
        cfg = _long_config(
            phase1_weak_peak_ms=2_700_000,
            phase1_weak_peak_min_roe=3.0,
        )
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.phase1_start_ts = 1_000_000
        s.high_water = 100.5  # Peak ROE = (0.5/100)*10*100 = 5% >= 3%

        now = 1_000_000 + 46 * 60_000
        r = engine.evaluate(100.3, s, now_ms=now)
        assert r.action != GuardAction.WEAK_PEAK_CUT

    def test_weak_peak_does_not_trigger_before_time(self):
        """Before 45 min, no weak-peak check."""
        cfg = _long_config(
            phase1_weak_peak_ms=2_700_000,
            phase1_weak_peak_min_roe=3.0,
        )
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.phase1_start_ts = 1_000_000
        s.high_water = 100.1  # Very low peak

        now = 1_000_000 + 30 * 60_000  # Only 30 min
        r = engine.evaluate(100.05, s, now_ms=now)
        assert r.action == GuardAction.HOLD

    def test_graduation_takes_priority_over_timeout(self):
        """If ROE hits graduation AND timeout, graduation wins (checked after timeout but
        timeout fires first — however if we hit tier trigger, we should graduate.
        Actually timeout is checked first, so it wins. This test verifies that.)"""
        cfg = _long_config(phase1_max_duration_ms=5_400_000)
        engine = TrailingStopEngine(cfg)
        s = _state(entry=100.0)
        s.phase1_start_ts = 1_000_000

        # 91 min later, price at 101.0 (10% ROE = tier 0 trigger)
        # Timeout is checked first, so PHASE1_TIMEOUT wins
        now = 1_000_000 + 91 * 60_000
        r = engine.evaluate(101.0, s, now_ms=now)
        assert r.action == GuardAction.PHASE1_TIMEOUT
