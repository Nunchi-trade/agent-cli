"""Tests for modules/guard_bridge.py — Guard Bridge lifecycle and I/O."""
import tempfile
import pytest
from unittest.mock import MagicMock

from modules.guard_bridge import GuardBridge
from modules.guard_config import GuardConfig
from modules.guard_state import GuardState, GuardStateStore
from modules.trailing_stop import GuardAction


def _make_bridge(entry=2500.0, direction="long", tmp_dir=None):
    config = GuardConfig(direction=direction, leverage=10.0)
    state = GuardState.new(
        instrument="ETH-PERP", entry_price=entry,
        position_size=1.0, direction=direction,
    )
    store = GuardStateStore(data_dir=tmp_dir or tempfile.mkdtemp())
    return GuardBridge(config=config, state=state, store=store)


class TestCheck:
    def test_check_returns_guard_result(self):
        bridge = _make_bridge()
        result = bridge.check(2510.0)
        assert result.action in (GuardAction.HOLD, GuardAction.CLOSE, GuardAction.TIER_CHANGED)

    def test_check_updates_last_check_ts(self):
        bridge = _make_bridge()
        assert bridge.state.last_check_ts == 0
        bridge.check(2510.0)
        assert bridge.state.last_check_ts > 0

    def test_hold_on_small_move(self):
        bridge = _make_bridge(entry=2500.0)
        result = bridge.check(2501.0)  # small move up
        assert result.action == GuardAction.HOLD


class TestLifecycle:
    def test_is_active_initially(self):
        bridge = _make_bridge()
        assert bridge.is_active is True

    def test_mark_closed(self):
        bridge = _make_bridge()
        bridge.mark_closed(2480.0, "test_close")
        assert bridge.is_active is False
        assert bridge.state.closed is True
        assert bridge.state.close_reason == "test_close"
        assert bridge.state.close_price == 2480.0
        assert bridge.state.close_ts > 0


class TestExchangeSL:
    def test_sync_places_trigger_order(self):
        bridge = _make_bridge()
        hl = MagicMock()
        hl.place_trigger_order.return_value = "trigger-123"
        hl.cancel_trigger_order.return_value = True

        bridge.sync_exchange_sl(hl, "ETH-PERP")
        hl.place_trigger_order.assert_called_once()
        assert bridge.state.exchange_sl_oid == "trigger-123"

    def test_sync_cancels_old_sl_first(self):
        bridge = _make_bridge()
        bridge.state.exchange_sl_oid = "old-sl"
        hl = MagicMock()
        hl.place_trigger_order.return_value = "new-sl"

        bridge.sync_exchange_sl(hl, "ETH-PERP")
        hl.cancel_trigger_order.assert_called_with("ETH-PERP", "old-sl")

    def test_cancel_exchange_sl(self):
        bridge = _make_bridge()
        bridge.state.exchange_sl_oid = "sl-to-cancel"
        hl = MagicMock()

        bridge.cancel_exchange_sl(hl, "ETH-PERP")
        hl.cancel_trigger_order.assert_called_with("ETH-PERP", "sl-to-cancel")
        assert bridge.state.exchange_sl_oid == ""

    def test_cancel_noop_when_no_sl(self):
        bridge = _make_bridge()
        hl = MagicMock()
        bridge.cancel_exchange_sl(hl, "ETH-PERP")
        hl.cancel_trigger_order.assert_not_called()

    def test_sync_inactive_guard_noop(self):
        bridge = _make_bridge()
        bridge.mark_closed(2480.0, "test")
        hl = MagicMock()
        bridge.sync_exchange_sl(hl, "ETH-PERP")
        hl.place_trigger_order.assert_not_called()


class TestPersistence:
    def test_from_store_roundtrip(self):
        tmp = tempfile.mkdtemp()
        bridge = _make_bridge(tmp_dir=tmp)
        pos_id = bridge.state.position_id
        bridge.check(2510.0)  # triggers a save

        # Restore from store
        restored = GuardBridge.from_store(pos_id, store=GuardStateStore(data_dir=tmp))
        assert restored is not None
        assert restored.state.position_id == pos_id
        assert restored.state.entry_price == 2500.0

    def test_from_store_missing_returns_none(self):
        tmp = tempfile.mkdtemp()
        store = GuardStateStore(data_dir=tmp)
        assert GuardBridge.from_store("nonexistent", store=store) is None


class TestComputeFloor:
    def test_phase1_absolute_floor(self):
        bridge = _make_bridge(entry=2500.0)
        bridge.config.phase1_absolute_floor = 2400.0
        floor = bridge._compute_current_floor()
        assert floor == 2400.0

    def test_phase1_trailing_floor(self):
        bridge = _make_bridge(entry=2500.0)
        # Phase 1 without absolute floor — uses trailing retrace
        bridge.config.phase1_absolute_floor = 0.0
        bridge.state.high_water = 2600.0
        floor = bridge._compute_current_floor()
        assert floor > 0
        assert floor < 2600.0  # below high water
