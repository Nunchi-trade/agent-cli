"""Tests for modules.rotation — min-hold and slot cooldown enforcement."""
from __future__ import annotations

import pytest

from modules.rotation import RotationPolicy


class TestMinHold:
    def test_cannot_exit_before_min_hold(self):
        rp = RotationPolicy(min_hold_ms=2_700_000)
        rp.record_entry(slot_id=0, now_ms=1_000_000)
        assert not rp.can_exit(slot_id=0, now_ms=1_500_000)  # 500s < 2700s

    def test_can_exit_after_min_hold(self):
        rp = RotationPolicy(min_hold_ms=2_700_000)
        rp.record_entry(slot_id=0, now_ms=1_000_000)
        assert rp.can_exit(slot_id=0, now_ms=3_800_000)  # 2800s > 2700s

    def test_can_exit_exactly_at_boundary(self):
        rp = RotationPolicy(min_hold_ms=2_700_000)
        rp.record_entry(slot_id=0, now_ms=1_000_000)
        assert rp.can_exit(slot_id=0, now_ms=3_700_000)  # exactly 2700s

    def test_can_exit_no_entry_recorded(self):
        rp = RotationPolicy(min_hold_ms=2_700_000)
        assert rp.can_exit(slot_id=99, now_ms=1_000)

    def test_min_hold_disabled(self):
        rp = RotationPolicy(min_hold_ms=0)
        rp.record_entry(slot_id=0, now_ms=1_000_000)
        assert rp.can_exit(slot_id=0, now_ms=1_000_001)

    def test_time_until_exit(self):
        rp = RotationPolicy(min_hold_ms=2_700_000)
        rp.record_entry(slot_id=0, now_ms=1_000_000)
        assert rp.time_until_exit_allowed(slot_id=0, now_ms=2_000_000) == 1_700_000
        assert rp.time_until_exit_allowed(slot_id=0, now_ms=4_000_000) == 0


class TestSlotCooldown:
    def test_cannot_enter_during_cooldown(self):
        rp = RotationPolicy(slot_cooldown_ms=300_000)
        rp.record_close(slot_id=0, now_ms=1_000_000)
        assert not rp.can_enter_slot(slot_id=0, now_ms=1_100_000)  # 100s < 300s

    def test_can_enter_after_cooldown(self):
        rp = RotationPolicy(slot_cooldown_ms=300_000)
        rp.record_close(slot_id=0, now_ms=1_000_000)
        assert rp.can_enter_slot(slot_id=0, now_ms=1_400_000)  # 400s > 300s

    def test_can_enter_at_boundary(self):
        rp = RotationPolicy(slot_cooldown_ms=300_000)
        rp.record_close(slot_id=0, now_ms=1_000_000)
        assert rp.can_enter_slot(slot_id=0, now_ms=1_300_000)

    def test_can_enter_never_closed(self):
        rp = RotationPolicy(slot_cooldown_ms=300_000)
        assert rp.can_enter_slot(slot_id=5, now_ms=1_000)

    def test_cooldown_disabled(self):
        rp = RotationPolicy(slot_cooldown_ms=0)
        rp.record_close(slot_id=0, now_ms=1_000_000)
        assert rp.can_enter_slot(slot_id=0, now_ms=1_000_001)

    def test_time_until_slot_available(self):
        rp = RotationPolicy(slot_cooldown_ms=300_000)
        rp.record_close(slot_id=0, now_ms=1_000_000)
        assert rp.time_until_slot_available(slot_id=0, now_ms=1_100_000) == 200_000
        assert rp.time_until_slot_available(slot_id=0, now_ms=1_400_000) == 0


class TestMultiSlot:
    def test_independent_slots(self):
        rp = RotationPolicy(min_hold_ms=2_700_000, slot_cooldown_ms=300_000)
        rp.record_entry(slot_id=0, now_ms=1_000_000)
        rp.record_entry(slot_id=1, now_ms=2_000_000)

        # Slot 0 can exit, slot 1 cannot
        assert rp.can_exit(slot_id=0, now_ms=4_000_000)
        assert not rp.can_exit(slot_id=1, now_ms=4_000_000)

    def test_close_clears_entry(self):
        rp = RotationPolicy(min_hold_ms=2_700_000)
        rp.record_entry(slot_id=0, now_ms=1_000_000)
        rp.record_close(slot_id=0, now_ms=2_000_000)
        # After close, can_exit returns True (no entry recorded)
        assert rp.can_exit(slot_id=0, now_ms=2_000_001)


class TestReset:
    def test_reset_clears_all(self):
        rp = RotationPolicy(min_hold_ms=2_700_000, slot_cooldown_ms=300_000)
        rp.record_entry(slot_id=0, now_ms=1_000_000)
        rp.record_close(slot_id=1, now_ms=1_000_000)
        rp.reset()
        assert rp.can_exit(slot_id=0, now_ms=1_000_001)
        assert rp.can_enter_slot(slot_id=1, now_ms=1_000_001)
