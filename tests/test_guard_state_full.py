"""Tests for modules/guard_state.py — GuardState and GuardStateStore."""
import json
import tempfile
import pytest

from modules.guard_state import GuardState, GuardStateStore


class TestGuardStateNew:
    def test_creates_with_defaults(self):
        state = GuardState.new(
            instrument="ETH-PERP", entry_price=2500.0,
            position_size=1.0, direction="long",
        )
        assert state.instrument == "ETH-PERP"
        assert state.entry_price == 2500.0
        assert state.position_size == 1.0
        assert state.direction == "long"
        assert state.high_water == 2500.0
        assert state.current_tier_index == -1
        assert state.breach_count == 0
        assert state.closed is False
        assert state.created_ts > 0
        assert state.phase1_start_ts > 0

    def test_short_direction(self):
        state = GuardState.new(
            instrument="BTC-PERP", entry_price=60000.0,
            position_size=0.1, direction="short",
        )
        assert state.direction == "short"

    def test_auto_position_id(self):
        state = GuardState.new(
            instrument="ETH-PERP", entry_price=2500.0,
            position_size=1.0,
        )
        assert state.position_id.startswith("ETH-PERP-")

    def test_custom_position_id(self):
        state = GuardState.new(
            instrument="ETH-PERP", entry_price=2500.0,
            position_size=1.0, position_id="custom-123",
        )
        assert state.position_id == "custom-123"


class TestSerialization:
    def test_to_dict_roundtrip(self):
        state = GuardState.new(
            instrument="ETH-PERP", entry_price=2500.0,
            position_size=1.0, direction="long",
        )
        d = state.to_dict()
        restored = GuardState.from_dict(d)
        assert restored.instrument == state.instrument
        assert restored.entry_price == state.entry_price
        assert restored.direction == state.direction
        assert restored.high_water == state.high_water
        assert restored.phase1_start_ts == state.phase1_start_ts

    def test_from_dict_ignores_unknown_fields(self):
        d = {"instrument": "ETH-PERP", "unknown_field": 42, "entry_price": 100.0}
        state = GuardState.from_dict(d)
        assert state.instrument == "ETH-PERP"
        assert not hasattr(state, "unknown_field")

    def test_copy(self):
        state = GuardState.new("ETH-PERP", 2500.0, 1.0)
        copied = state.copy()
        copied.breach_count = 99
        assert state.breach_count == 0  # original unchanged

    def test_exchange_sl_oid_serializes(self):
        state = GuardState.new("ETH-PERP", 2500.0, 1.0)
        state.exchange_sl_oid = "trigger-456"
        d = state.to_dict()
        assert d["exchange_sl_oid"] == "trigger-456"
        restored = GuardState.from_dict(d)
        assert restored.exchange_sl_oid == "trigger-456"


class TestGuardStateStore:
    def test_save_and_load(self):
        tmp = tempfile.mkdtemp()
        store = GuardStateStore(data_dir=tmp)
        state = GuardState.new("ETH-PERP", 2500.0, 1.0)
        store.save(state, {"key": "value"})

        loaded = store.load(state.position_id)
        assert loaded is not None
        assert loaded["state"]["entry_price"] == 2500.0
        assert loaded["config"]["key"] == "value"

    def test_load_state(self):
        tmp = tempfile.mkdtemp()
        store = GuardStateStore(data_dir=tmp)
        state = GuardState.new("ETH-PERP", 2500.0, 1.0)
        store.save(state)

        loaded = store.load_state(state.position_id)
        assert loaded is not None
        assert loaded.entry_price == 2500.0

    def test_load_missing_returns_none(self):
        tmp = tempfile.mkdtemp()
        store = GuardStateStore(data_dir=tmp)
        assert store.load("nonexistent") is None
        assert store.load_state("nonexistent") is None

    def test_list_active(self):
        tmp = tempfile.mkdtemp()
        store = GuardStateStore(data_dir=tmp)

        active = GuardState.new("ETH-PERP", 2500.0, 1.0, position_id="active-1")
        closed = GuardState.new("BTC-PERP", 60000.0, 0.1, position_id="closed-1")
        closed.closed = True

        store.save(active)
        store.save(closed)

        active_list = store.list_active()
        assert "active-1" in active_list
        assert "closed-1" not in active_list

    def test_list_all(self):
        tmp = tempfile.mkdtemp()
        store = GuardStateStore(data_dir=tmp)
        state = GuardState.new("ETH-PERP", 2500.0, 1.0, position_id="test-pos")
        store.save(state)
        assert "test-pos" in store.list_all()
