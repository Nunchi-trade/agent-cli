"""Integration safety tests for Phases 2.5a, 2.5b, 2.5c.

Validates reconciliation, exchange SL sync, and archiving work end-to-end
in realistic multi-component scenarios. Read-only validation — no production
code modified.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pytest

from modules.reconciliation import ReconciliationEngine, Discrepancy
from modules.guard_bridge import GuardBridge
from modules.guard_config import GuardConfig, Tier
from modules.guard_state import GuardState, GuardStateStore
from modules.archiver import StateArchiver
from modules.apex_state import ApexSlot, ApexState
from cli.hl_adapter import DirectMockProxy


# ===========================================================================
# Helpers
# ===========================================================================

def _slot_dict(slot_id: int, instrument: str = "", size: float = 0.0,
               direction: str = "long", status: str = "empty") -> Dict[str, Any]:
    """Build a slot dict matching ApexSlot.to_dict() shape."""
    return ApexSlot(
        slot_id=slot_id,
        status=status,
        instrument=instrument,
        direction=direction,
        entry_size=size,
    ).to_dict()


def _exchange_pos(coin: str, szi: float) -> Dict[str, Any]:
    """HL-style assetPosition with nested 'position' dict."""
    return {"position": {"coin": coin, "szi": str(szi)}}


def _make_guard_bridge(
    direction: str = "long",
    entry_price: float = 100.0,
    position_size: float = 1.0,
    tier_index: int = -1,
    high_water: float = 0.0,
    leverage: float = 10.0,
    store: GuardStateStore | None = None,
) -> GuardBridge:
    cfg = GuardConfig(
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
            Tier(trigger_pct=30.0, lock_pct=22.0, retrace=0.012),
        ],
    )
    state = GuardState.new(
        instrument="ETH-PERP",
        entry_price=entry_price,
        position_size=position_size,
        direction=direction,
        position_id="integ-test-pos",
    )
    state.current_tier_index = tier_index
    if high_water > 0:
        state.high_water = high_water
    if store is None:
        store = GuardStateStore(data_dir="/tmp/test_integ_safety_guard")
    return GuardBridge(config=cfg, state=state, store=store)


# ===========================================================================
# Phase 2.5a — Reconciliation Integration
# ===========================================================================

class TestReconciliationIntegration:
    """Realistic multi-slot reconciliation with orphans and mismatches."""

    def test_realistic_3_slot_scenario(self):
        """3 APEX slots (2 active, 1 empty) + exchange positions with
        an orphan and a size mismatch. Verifies correct discrepancy detection."""
        engine = ReconciliationEngine()

        # APEX state: 3 slots
        slots = [
            _slot_dict(0, instrument="ETH-PERP", size=1.0, direction="long", status="active"),
            _slot_dict(1, instrument="BTC-PERP", size=0.5, direction="short", status="active"),
            _slot_dict(2),  # empty slot
        ]

        # Exchange positions:
        #  - ETH: 1.25 (size mismatch vs slot 0's 1.0 — 25% diff, critical)
        #  - BTC: -0.5 (matches slot 1 perfectly)
        #  - SOL: 100.0 (orphan — no slot tracks it)
        exchange = [
            _exchange_pos("ETH", 1.25),
            _exchange_pos("BTC", -0.5),
            _exchange_pos("SOL", 100.0),
        ]

        discrepancies = engine.reconcile(slots, exchange)

        # Should find exactly 2 issues: orphan_exchange (SOL) + size_mismatch (ETH)
        assert len(discrepancies) == 2

        types_found = {d.type for d in discrepancies}
        assert "orphan_exchange" in types_found
        assert "size_mismatch" in types_found

        # Orphan: SOL on exchange, no slot
        orphan = next(d for d in discrepancies if d.type == "orphan_exchange")
        assert orphan.instrument == "SOL-PERP"
        assert orphan.severity == "critical"
        assert orphan.slot_id is None
        assert orphan.exchange_size == 100.0
        assert orphan.internal_size == 0.0

        # Size mismatch: ETH slot says 1.0, exchange says 1.25 (25% diff -> critical)
        mismatch = next(d for d in discrepancies if d.type == "size_mismatch")
        assert mismatch.instrument == "ETH-PERP"
        assert mismatch.severity == "critical"  # 25% > 10%
        assert mismatch.slot_id == 0
        assert mismatch.exchange_size == 1.25
        assert mismatch.internal_size == 1.0

    def test_sorting_critical_before_warning(self):
        """Critical discrepancies (orphan_exchange, large mismatch) sort before
        warning-level ones (orphan_slot, small mismatch)."""
        engine = ReconciliationEngine()

        slots = [
            _slot_dict(0, instrument="DOGE-PERP", size=1.0, direction="long", status="active"),
            _slot_dict(1, instrument="XRP-PERP", size=50.0, direction="long", status="active"),
        ]
        exchange = [
            # DOGE missing from exchange -> orphan_slot (warning)
            # XRP: 48.0 vs 50.0 = 4% mismatch -> warning
            _exchange_pos("XRP", 48.0),
            # LINK orphan on exchange -> orphan_exchange (critical)
            _exchange_pos("LINK", 200.0),
        ]

        discrepancies = engine.reconcile(slots, exchange)
        assert len(discrepancies) == 3

        # First item should be critical
        assert discrepancies[0].severity == "critical"
        # Warnings come after
        assert all(d.severity in ("critical", "warning") for d in discrepancies)
        critical_indices = [i for i, d in enumerate(discrepancies) if d.severity == "critical"]
        warning_indices = [i for i, d in enumerate(discrepancies) if d.severity == "warning"]
        assert max(critical_indices) < min(warning_indices)

    def test_all_matched_no_discrepancies(self):
        """When all slots match exchange positions exactly, no discrepancies."""
        engine = ReconciliationEngine()

        slots = [
            _slot_dict(0, instrument="ETH-PERP", size=2.0, direction="long", status="active"),
            _slot_dict(1, instrument="BTC-PERP", size=0.1, direction="short", status="active"),
            _slot_dict(2),
        ]
        exchange = [
            _exchange_pos("ETH", 2.0),
            _exchange_pos("BTC", -0.1),
        ]

        discrepancies = engine.reconcile(slots, exchange)
        assert len(discrepancies) == 0

    def test_discrepancy_to_dict_serialization(self):
        """Verify discrepancies are JSON-serializable via to_dict()."""
        engine = ReconciliationEngine()

        slots = [_slot_dict(0, instrument="ETH-PERP", size=1.0, direction="long", status="active")]
        exchange = [_exchange_pos("ETH", 0.5), _exchange_pos("AVAX", 10.0)]

        discrepancies = engine.reconcile(slots, exchange)
        for d in discrepancies:
            d_dict = d.to_dict()
            # Must be JSON-serializable
            serialized = json.dumps(d_dict)
            parsed = json.loads(serialized)
            assert parsed["type"] == d.type
            assert parsed["severity"] == d.severity


class TestStandaloneRunnerReconIntegration:
    """Verify standalone_runner._reconcile_on_startup() structure is correct."""

    def test_reconcile_on_startup_method_exists(self):
        """The _reconcile_on_startup method must exist on StandaloneGuardRunner."""
        import importlib
        import inspect

        # We cannot instantiate the runner (requires HL connection), but we can
        # verify the method exists on the class by inspecting the source.
        spec = importlib.util.find_spec("skills.apex.scripts.standalone_runner")
        assert spec is not None, "standalone_runner module must be importable"

        source_path = spec.origin
        assert source_path is not None
        source = Path(source_path).read_text()

        # Verify key structural elements
        assert "def _reconcile_on_startup(self)" in source
        assert "self.recon_engine.reconcile(slot_dicts, positions)" in source
        assert 'self.recon_engine = ReconciliationEngine()' in source

    def test_reconcile_on_startup_handles_all_discrepancy_types(self):
        """Verify the method handles orphan_exchange, orphan_slot, and size_mismatch."""
        source = Path("skills/apex/scripts/standalone_runner.py").read_text()

        # Extract the _reconcile_on_startup method body
        start = source.index("def _reconcile_on_startup(self)")
        # Find next method (def at same indentation)
        body = source[start:]

        assert 'orphan_exchange' in body
        assert 'orphan_slot' in body
        assert 'size_mismatch' in body
        assert '_adopt_orphan' in body


# ===========================================================================
# Phase 2.5b — Exchange SL Sync Integration
# ===========================================================================

class TestExchangeSLSyncIntegration:
    """End-to-end SL sync: compute floor -> place trigger -> verify price."""

    def test_phase1_long_floor_computation_and_order(self):
        """Phase 1 long: floor = high_water * (1 - retrace). Verify the
        trigger order is placed at the correct price on the mock exchange."""
        mock = DirectMockProxy()
        guard = _make_guard_bridge(
            direction="long", entry_price=100.0, high_water=110.0,
            tier_index=-1, position_size=2.5,
        )

        guard.sync_exchange_sl(mock, "ETH-PERP")

        # Verify order was placed
        oid = guard.state.exchange_sl_oid
        assert oid != ""
        order = mock._trigger_orders[oid]

        expected_floor = 110.0 * (1 - 0.03)  # 106.7
        assert order["trigger_price"] == pytest.approx(expected_floor, rel=1e-6)
        assert order["side"] == "sell"  # long -> sell to close
        assert order["size"] == 2.5
        assert order["instrument"] == "ETH-PERP"

    def test_phase2_tier_floor_computation_and_order(self):
        """Phase 2 tier 1: floor = entry * (1 + lock_pct/100/leverage).
        lock_pct=14.0, leverage=10 -> floor = 100 * 1.014 = 101.4."""
        mock = DirectMockProxy()
        guard = _make_guard_bridge(
            direction="long", entry_price=100.0, high_water=125.0,
            tier_index=1, position_size=1.0,
        )

        guard.sync_exchange_sl(mock, "ETH-PERP")

        oid = guard.state.exchange_sl_oid
        order = mock._trigger_orders[oid]
        expected_floor = 100.0 * (1.0 + 14.0 / 100.0 / 10.0)  # 101.4
        assert order["trigger_price"] == pytest.approx(expected_floor, rel=1e-6)

    def test_tier_ratchet_cancels_old_and_places_new(self):
        """When tier upgrades, the old SL must be cancelled and a new one placed
        at the higher tier floor."""
        mock = DirectMockProxy()
        guard = _make_guard_bridge(
            direction="long", entry_price=100.0, high_water=105.0,
            tier_index=-1, position_size=1.0,
        )

        # Phase 1: place initial SL
        guard.sync_exchange_sl(mock, "ETH-PERP")
        phase1_oid = guard.state.exchange_sl_oid
        phase1_floor = mock._trigger_orders[phase1_oid]["trigger_price"]
        assert phase1_oid in mock._trigger_orders

        # Simulate tier upgrade to tier 0
        guard.state.current_tier_index = 0
        guard.sync_exchange_sl(mock, "ETH-PERP")
        tier0_oid = guard.state.exchange_sl_oid

        # Old order cancelled
        assert phase1_oid not in mock._trigger_orders
        # New order exists at tier 0 floor
        assert tier0_oid in mock._trigger_orders
        tier0_floor = mock._trigger_orders[tier0_oid]["trigger_price"]
        expected_tier0_floor = 100.0 * (1.0 + 5.0 / 100.0 / 10.0)  # 100.5
        assert tier0_floor == pytest.approx(expected_tier0_floor, rel=1e-6)

        # Simulate further upgrade to tier 1
        guard.state.current_tier_index = 1
        guard.sync_exchange_sl(mock, "ETH-PERP")
        tier1_oid = guard.state.exchange_sl_oid

        # Tier 0 order cancelled
        assert tier0_oid not in mock._trigger_orders
        # Tier 1 order at higher floor
        tier1_floor = mock._trigger_orders[tier1_oid]["trigger_price"]
        expected_tier1_floor = 100.0 * (1.0 + 14.0 / 100.0 / 10.0)  # 101.4
        assert tier1_floor == pytest.approx(expected_tier1_floor, rel=1e-6)
        assert tier1_floor > tier0_floor  # floors must ratchet up

    def test_short_direction_sl_uses_buy_side(self):
        """Short position SL should be a buy trigger order above entry."""
        mock = DirectMockProxy()
        guard = _make_guard_bridge(
            direction="short", entry_price=100.0, high_water=95.0,
            tier_index=-1, position_size=3.0,
        )

        guard.sync_exchange_sl(mock, "ETH-PERP")

        oid = guard.state.exchange_sl_oid
        order = mock._trigger_orders[oid]
        expected_floor = 95.0 * (1 + 0.03)  # 97.85
        assert order["trigger_price"] == pytest.approx(expected_floor, rel=1e-6)
        assert order["side"] == "buy"
        assert order["size"] == 3.0

    def test_closed_guard_sync_is_noop(self):
        """sync_exchange_sl on a closed guard should not place any order."""
        mock = DirectMockProxy()
        guard = _make_guard_bridge(entry_price=100.0, high_water=105.0)
        guard.state.closed = True

        guard.sync_exchange_sl(mock, "ETH-PERP")

        assert guard.state.exchange_sl_oid == ""
        assert len(mock._trigger_orders) == 0


# ===========================================================================
# Phase 2.5c — Archiving Integration
# ===========================================================================

class TestArchivingIntegration:
    """End-to-end archiving: create state -> mark closed -> archive -> verify."""

    def test_guard_state_archive_end_to_end(self, tmp_path):
        """Create a guard state JSON file, mark it closed, archive it,
        verify it moved to the correct date directory."""
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()
        archive_dir = tmp_path / "archive"

        # Write a realistic closed guard state file
        position_id = "apex-slot-0-ETH-PERP"
        state_data = {
            "state": {
                "instrument": "ETH-PERP",
                "position_id": position_id,
                "entry_price": 3200.0,
                "position_size": 1.5,
                "direction": "long",
                "high_water": 3400.0,
                "current_tier_index": 1,
                "breach_count": 0,
                "current_roe": 15.0,
                "exchange_sl_oid": "",
                "closed": True,
                "close_reason": "phase2_breach",
                "close_price": 3350.0,
                "close_ts": int(time.time() * 1000),
            },
            "config": {
                "direction": "long",
                "leverage": 10.0,
                "tiers": [
                    {"trigger_pct": 10.0, "lock_pct": 5.0},
                    {"trigger_pct": 20.0, "lock_pct": 14.0},
                ],
            },
        }
        src_file = guard_dir / f"{position_id}.json"
        src_file.write_text(json.dumps(state_data, indent=2))

        # Archive
        archiver = StateArchiver(archive_dir=str(archive_dir))
        result = archiver.archive_guard_state(str(guard_dir), position_id)

        assert result is True
        assert not src_file.exists(), "Source file should be moved (not copied)"

        # Verify destination
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        archived_file = archive_dir / date_str / "guard" / f"{position_id}.json"
        assert archived_file.exists()

        # Verify content integrity
        archived_data = json.loads(archived_file.read_text())
        assert archived_data["state"]["instrument"] == "ETH-PERP"
        assert archived_data["state"]["closed"] is True
        assert archived_data["state"]["close_reason"] == "phase2_breach"
        assert archived_data["config"]["leverage"] == 10.0

    def test_slot_snapshot_archive_end_to_end(self, tmp_path):
        """Archive a slot snapshot and verify it writes with correct structure."""
        archive_dir = tmp_path / "archive"
        archiver = StateArchiver(archive_dir=str(archive_dir))

        slot_data = {
            "slot_id": 1,
            "status": "closed",
            "instrument": "BTC-PERP",
            "direction": "short",
            "entry_price": 65000.0,
            "entry_size": 0.2,
            "close_reason": "guard_breach",
            "close_pnl": 130.0,
        }

        archiver.archive_slot_snapshot(slot_data, slot_id=1)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        apex_dir = archive_dir / date_str / "apex"
        assert apex_dir.exists()

        files = list(apex_dir.glob("slot-1-*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["slot_id"] == 1
        assert data["instrument"] == "BTC-PERP"
        assert data["close_pnl"] == 130.0

    def test_archive_old_only_moves_closed_guards(self, tmp_path):
        """archive_old() should move closed guards and skip active ones."""
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()
        archive_dir = tmp_path / "archive"

        # Closed guard
        (guard_dir / "closed-eth.json").write_text(json.dumps({
            "state": {"closed": True, "instrument": "ETH-PERP", "close_reason": "breach"},
        }))

        # Active guard (should NOT be archived)
        (guard_dir / "active-btc.json").write_text(json.dumps({
            "state": {"closed": False, "instrument": "BTC-PERP"},
        }))

        # Another closed guard
        (guard_dir / "closed-sol.json").write_text(json.dumps({
            "state": {"closed": True, "instrument": "SOL-PERP", "close_reason": "timeout"},
        }))

        archiver = StateArchiver(archive_dir=str(archive_dir))
        counts = archiver.archive_old(str(guard_dir))

        assert counts["guard"] == 2
        assert counts["skipped"] == 1

        # Closed files moved
        assert not (guard_dir / "closed-eth.json").exists()
        assert not (guard_dir / "closed-sol.json").exists()
        # Active file remains
        assert (guard_dir / "active-btc.json").exists()

        # Check archive
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        archive_guard_dir = archive_dir / date_str / "guard"
        archived = sorted(f.name for f in archive_guard_dir.iterdir())
        assert "closed-eth.json" in archived
        assert "closed-sol.json" in archived
        assert "active-btc.json" not in archived

    def test_multiple_snapshots_dont_overwrite(self, tmp_path):
        """Two slot snapshots for the same slot_id get unique filenames (timestamped)."""
        archive_dir = tmp_path / "archive"
        archiver = StateArchiver(archive_dir=str(archive_dir))

        slot_data_1 = {"slot_id": 0, "instrument": "ETH-PERP", "close_pnl": 50.0}
        slot_data_2 = {"slot_id": 0, "instrument": "ETH-PERP", "close_pnl": -20.0}

        archiver.archive_slot_snapshot(slot_data_1, slot_id=0)
        # Small delay to ensure different timestamps
        time.sleep(0.01)
        archiver.archive_slot_snapshot(slot_data_2, slot_id=0)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        apex_dir = archive_dir / date_str / "apex"
        files = list(apex_dir.glob("slot-0-*.json"))
        assert len(files) == 2

        # Verify both files have distinct data
        pnls = set()
        for f in files:
            data = json.loads(f.read_text())
            pnls.add(data["close_pnl"])
        assert pnls == {50.0, -20.0}
