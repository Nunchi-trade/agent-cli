"""Tests for exchange-level SL sync (Phase 2.5b).

Covers: trigger order mock, GuardBridge SL sync, state serialization.
"""
from __future__ import annotations

import pytest

from cli.hl_adapter import DirectMockProxy
from modules.guard_bridge import GuardBridge
from modules.guard_config import GuardConfig, Tier
from modules.guard_state import GuardState, GuardStateStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config(direction: str = "long", leverage: float = 10.0) -> GuardConfig:
    return GuardConfig(
        direction=direction,
        leverage=leverage,
        phase1_retrace=0.03,
        phase1_max_breaches=3,
        phase1_absolute_floor=0.0,
        phase2_retrace=0.015,
        phase2_max_breaches=2,
        tiers=[
            Tier(trigger_pct=10.0, lock_pct=5.0),
            Tier(trigger_pct=20.0, lock_pct=14.0),
        ],
    )


def _make_guard(
    direction: str = "long",
    entry_price: float = 100.0,
    position_size: float = 1.0,
    tier_index: int = -1,
    high_water: float = 0.0,
) -> GuardBridge:
    cfg = _make_config(direction=direction)
    state = GuardState.new(
        instrument="ETH-PERP",
        entry_price=entry_price,
        position_size=position_size,
        direction=direction,
        position_id="test-pos-1",
    )
    state.current_tier_index = tier_index
    if high_water > 0:
        state.high_water = high_water
    store = GuardStateStore(data_dir="/tmp/test_exchange_sl_guard")
    return GuardBridge(config=cfg, state=state, store=store)


# ---------------------------------------------------------------------------
# Mock trigger order tests
# ---------------------------------------------------------------------------

class TestMockTriggerOrders:

    def test_place_trigger_order_returns_oid(self):
        mock = DirectMockProxy()
        oid = mock.place_trigger_order("ETH-PERP", "sell", 1.0, 95.0)
        assert oid is not None
        assert oid == "9000"

    def test_place_trigger_order_increments_oid(self):
        mock = DirectMockProxy()
        oid1 = mock.place_trigger_order("ETH-PERP", "sell", 1.0, 95.0)
        oid2 = mock.place_trigger_order("ETH-PERP", "sell", 1.0, 96.0)
        assert oid1 != oid2
        assert int(oid2) == int(oid1) + 1

    def test_cancel_trigger_order_returns_true(self):
        mock = DirectMockProxy()
        oid = mock.place_trigger_order("ETH-PERP", "sell", 1.0, 95.0)
        assert mock.cancel_trigger_order("ETH-PERP", oid) is True

    def test_cancel_trigger_order_removes_order(self):
        mock = DirectMockProxy()
        oid = mock.place_trigger_order("ETH-PERP", "sell", 1.0, 95.0)
        mock.cancel_trigger_order("ETH-PERP", oid)
        # Second cancel should fail — already removed
        assert mock.cancel_trigger_order("ETH-PERP", oid) is False

    def test_cancel_nonexistent_returns_false(self):
        mock = DirectMockProxy()
        assert mock.cancel_trigger_order("ETH-PERP", "99999") is False

    def test_trigger_order_stores_details(self):
        mock = DirectMockProxy()
        oid = mock.place_trigger_order("BTC-PERP", "buy", 0.5, 30000.0)
        order = mock._trigger_orders[oid]
        assert order["instrument"] == "BTC-PERP"
        assert order["side"] == "buy"
        assert order["size"] == 0.5
        assert order["trigger_price"] == 30000.0


# ---------------------------------------------------------------------------
# GuardBridge SL sync tests
# ---------------------------------------------------------------------------

class TestSyncExchangeSL:

    def test_sync_places_order_with_correct_floor(self):
        """Phase 1 long: floor = high_water * (1 - retrace)."""
        mock = DirectMockProxy()
        guard = _make_guard(entry_price=100.0, high_water=105.0)
        guard.sync_exchange_sl(mock, "ETH-PERP")

        assert guard.state.exchange_sl_oid != ""
        oid = guard.state.exchange_sl_oid
        order = mock._trigger_orders[oid]
        expected_floor = 105.0 * (1 - 0.03)  # 101.85
        assert order["trigger_price"] == pytest.approx(expected_floor)
        assert order["side"] == "sell"  # long -> sell to close
        assert order["size"] == 1.0

    def test_sync_short_direction(self):
        """Phase 1 short: floor = high_water * (1 + retrace), side = buy."""
        mock = DirectMockProxy()
        guard = _make_guard(direction="short", entry_price=100.0, high_water=95.0)
        guard.sync_exchange_sl(mock, "ETH-PERP")

        oid = guard.state.exchange_sl_oid
        order = mock._trigger_orders[oid]
        expected_floor = 95.0 * (1 + 0.03)  # 97.85
        assert order["trigger_price"] == pytest.approx(expected_floor)
        assert order["side"] == "buy"

    def test_sync_updates_on_tier_change(self):
        """When tier changes, old SL is cancelled and new one placed."""
        mock = DirectMockProxy()
        guard = _make_guard(entry_price=100.0, high_water=105.0)

        # First sync (Phase 1)
        guard.sync_exchange_sl(mock, "ETH-PERP")
        old_oid = guard.state.exchange_sl_oid
        assert old_oid in mock._trigger_orders

        # Simulate tier change
        guard.state.current_tier_index = 0
        guard.sync_exchange_sl(mock, "ETH-PERP")
        new_oid = guard.state.exchange_sl_oid

        # Old order should be cancelled (removed from mock)
        assert old_oid not in mock._trigger_orders
        # New order should exist
        assert new_oid in mock._trigger_orders
        assert new_oid != old_oid

    def test_sync_phase2_uses_tier_floor(self):
        """Phase 2: floor comes from tier lock_pct calculation."""
        mock = DirectMockProxy()
        guard = _make_guard(entry_price=100.0, tier_index=0, high_water=112.0)
        guard.sync_exchange_sl(mock, "ETH-PERP")

        oid = guard.state.exchange_sl_oid
        order = mock._trigger_orders[oid]
        # Tier 0: lock_pct=5.0, leverage=10
        # LONG: entry * (1 + 5.0/100/10) = 100 * 1.005 = 100.5
        expected_tier_floor = 100.0 * (1.0 + 5.0 / 100.0 / 10.0)
        assert order["trigger_price"] == pytest.approx(expected_tier_floor)

    def test_sync_inactive_guard_does_nothing(self):
        """Sync on a closed guard should be a no-op."""
        mock = DirectMockProxy()
        guard = _make_guard(entry_price=100.0)
        guard.state.closed = True
        guard.sync_exchange_sl(mock, "ETH-PERP")
        assert guard.state.exchange_sl_oid == ""
        assert len(mock._trigger_orders) == 0


# ---------------------------------------------------------------------------
# Cancel exchange SL tests
# ---------------------------------------------------------------------------

class TestCancelExchangeSL:

    def test_cancel_clears_oid(self):
        mock = DirectMockProxy()
        guard = _make_guard(entry_price=100.0, high_water=105.0)
        guard.sync_exchange_sl(mock, "ETH-PERP")
        assert guard.state.exchange_sl_oid != ""

        guard.cancel_exchange_sl(mock, "ETH-PERP")
        assert guard.state.exchange_sl_oid == ""

    def test_cancel_removes_from_exchange(self):
        mock = DirectMockProxy()
        guard = _make_guard(entry_price=100.0, high_water=105.0)
        guard.sync_exchange_sl(mock, "ETH-PERP")
        oid = guard.state.exchange_sl_oid

        guard.cancel_exchange_sl(mock, "ETH-PERP")
        assert oid not in mock._trigger_orders

    def test_cancel_noop_if_no_oid(self):
        """Cancel when no SL exists should be safe (no-op)."""
        mock = DirectMockProxy()
        guard = _make_guard(entry_price=100.0)
        # No SL placed
        guard.cancel_exchange_sl(mock, "ETH-PERP")
        assert guard.state.exchange_sl_oid == ""


# ---------------------------------------------------------------------------
# State serialization tests
# ---------------------------------------------------------------------------

class TestExchangeSLPersistence:

    def test_exchange_sl_oid_in_to_dict(self):
        state = GuardState.new("ETH-PERP", 100.0, 1.0)
        state.exchange_sl_oid = "12345"
        d = state.to_dict()
        assert d["exchange_sl_oid"] == "12345"

    def test_exchange_sl_oid_from_dict(self):
        d = {
            "instrument": "ETH-PERP",
            "position_id": "test",
            "entry_price": 100.0,
            "position_size": 1.0,
            "direction": "long",
            "high_water": 100.0,
            "high_water_ts": 0,
            "current_tier_index": -1,
            "breach_count": 0,
            "current_roe": 0.0,
            "exchange_sl_oid": "67890",
            "created_ts": 0,
            "last_check_ts": 0,
            "closed": False,
            "close_reason": "",
            "close_price": 0.0,
            "close_ts": 0,
        }
        state = GuardState.from_dict(d)
        assert state.exchange_sl_oid == "67890"

    def test_exchange_sl_oid_roundtrip(self):
        state = GuardState.new("BTC-PERP", 50000.0, 0.1)
        state.exchange_sl_oid = "SL-42"
        d = state.to_dict()
        restored = GuardState.from_dict(d)
        assert restored.exchange_sl_oid == "SL-42"

    def test_exchange_sl_oid_default_empty(self):
        state = GuardState.new("ETH-PERP", 100.0, 1.0)
        assert state.exchange_sl_oid == ""

    def test_from_dict_missing_exchange_sl_oid(self):
        """Backwards compat: old state files without exchange_sl_oid."""
        d = {
            "instrument": "ETH-PERP",
            "position_id": "test",
            "entry_price": 100.0,
            "position_size": 1.0,
            "direction": "long",
            "high_water": 100.0,
        }
        state = GuardState.from_dict(d)
        assert state.exchange_sl_oid == ""


# ---------------------------------------------------------------------------
# Failure resilience tests
# ---------------------------------------------------------------------------

class TestFailureResilience:

    def test_place_failure_is_warning_not_exception(self):
        """If place_trigger_order returns None, sync should not raise."""
        mock = DirectMockProxy()
        guard = _make_guard(entry_price=100.0, high_water=105.0)

        # Monkey-patch to return None (simulating failure)
        mock.place_trigger_order = lambda *a, **kw: None

        # Should not raise
        guard.sync_exchange_sl(mock, "ETH-PERP")
        assert guard.state.exchange_sl_oid == ""
