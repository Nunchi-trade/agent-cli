"""Phase 3 integration tests — end-to-end lifecycle validation.

Tests cover:
  3a  Signal Taxonomy (PulseEngine 5-tier)
  3b  Phase 1 Auto-Cut (TrailingStopEngine timeout / weak-peak)
  3c  ALO Fee Opt (DirectMockProxy order types)
  3d  Rotation Cooldown (ApexEngine min_hold + slot cooldown)
  3e  Risk Guardian (RiskManager gate machine)
"""
from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# Phase 3a — Signal Taxonomy
# ---------------------------------------------------------------------------

from modules.pulse_config import PulseConfig
from modules.pulse_engine import PulseEngine
from modules.pulse_state import PulseResult


def _build_all_markets(assets: list[dict]) -> list:
    """Build realistic HL-shaped all_markets payload.

    Each asset dict should have: name, openInterest, dayNtlVlm, funding, markPx.
    """
    universe = [{"name": a["name"]} for a in assets]
    ctxs = [
        {
            "openInterest": str(a.get("openInterest", 0)),
            "dayNtlVlm": str(a.get("dayNtlVlm", 0)),
            "funding": str(a.get("funding", 0)),
            "markPx": str(a.get("markPx", 0)),
        }
        for a in assets
    ]
    return [{"universe": universe}, ctxs]


def _build_scan_history(assets: list[dict], n_scans: int = 6) -> list[dict]:
    """Build scan_history with stable OI so baseline is deterministic."""
    history = []
    for i in range(n_scans):
        snapshots = []
        for a in assets:
            # Use a baseline OI that's lower than current so OI delta triggers
            baseline_oi = a.get("baseline_oi", a.get("openInterest", 0))
            snapshots.append({
                "asset": a["name"],
                "open_interest": baseline_oi,
                "volume_24h": a.get("dayNtlVlm", 0),
                "funding_rate": a.get("baseline_funding", a.get("funding", 0)),
                "mark_price": a.get("markPx", 0),
            })
        history.append({"scan_time_ms": 1000 * (i + 1), "snapshots": snapshots})
    return history


class TestSignalTaxonomy:
    """Phase 3a — PulseEngine 5-tier signal classification."""

    def _make_config(self) -> PulseConfig:
        return PulseConfig(
            sector_map={
                "ETH": "L1",
                "SOL": "L1",
                "ARB": "L2",
                "OP": "L2",
                "DOGE": "meme",
            },
            volume_min_24h=100_000,
            min_scans_for_signal=2,
            oi_delta_breakout_pct=8.0,
            volume_surge_ratio=3.0,
            contrib_explosion_oi_pct=15.0,
            contrib_explosion_vol_mult=5.0,
        )

    def test_scan_end_to_end_returns_tiered_signals(self):
        """Full scan() with sector_map produces signals with signal_tier set."""
        cfg = self._make_config()
        engine = PulseEngine(config=cfg)

        # ETH: big OI jump (20%) + volume surge -> should qualify for tier 1 or 2
        assets = [
            {
                "name": "ETH",
                "openInterest": 12_000_000,
                "dayNtlVlm": 6_000_000,
                "funding": 0.0001,
                "markPx": 3500,
                "baseline_oi": 10_000_000,  # 20% delta
            },
            {
                "name": "SOL",
                "openInterest": 5_500_000,
                "dayNtlVlm": 3_000_000,
                "funding": 0.0002,
                "markPx": 150,
                "baseline_oi": 5_000_000,  # 10% delta
            },
            {
                "name": "DOGE",
                "openInterest": 800_000,
                "dayNtlVlm": 200_000,
                "funding": 0.0,
                "markPx": 0.15,
                "baseline_oi": 700_000,  # ~14% delta
            },
        ]

        all_markets = _build_all_markets(assets)
        scan_history = _build_scan_history(assets, n_scans=6)

        # Provide 4h candles for volume surge detection
        asset_candles: dict = {}
        for a in assets:
            avg_4h = a["dayNtlVlm"] / 6
            # Give a recent 4h candle with 4x average -> surge ratio = 4
            asset_candles[a["name"]] = {
                "4h": [{"v": str(avg_4h * 4)}],
                "1h": [],
            }

        result = engine.scan(all_markets, asset_candles, scan_history)

        assert isinstance(result, PulseResult)
        assert result.stats["has_baseline"] is True

        # At least one signal should have signal_tier > 0
        tiered = [s for s in result.signals if s.signal_tier > 0]
        assert len(tiered) > 0, f"Expected tiered signals but got: {[s.signal_tier for s in result.signals]}"

        # Every signal_tier should be in the valid range [0..5]
        for sig in result.signals:
            assert 0 <= sig.signal_tier <= 5, f"{sig.asset} has invalid tier {sig.signal_tier}"

    def test_first_jump_only_first_asset_in_sector(self):
        """FIRST_JUMP (tier 1) is assigned to the first qualifying asset per sector only."""
        cfg = self._make_config()
        engine = PulseEngine(config=cfg)

        # Two L1 assets both qualifying for FIRST_JUMP criteria
        assets = [
            {
                "name": "ETH",
                "openInterest": 11_000_000,
                "dayNtlVlm": 6_000_000,
                "funding": 0.0001,
                "markPx": 3500,
                "baseline_oi": 10_000_000,  # 10% delta >= 8%
            },
            {
                "name": "SOL",
                "openInterest": 5_500_000,
                "dayNtlVlm": 3_000_000,
                "funding": 0.0002,
                "markPx": 150,
                "baseline_oi": 5_000_000,  # 10% delta >= 8%
            },
        ]

        all_markets = _build_all_markets(assets)
        scan_history = _build_scan_history(assets, n_scans=6)

        # Both need volume surge >= 3.0 to qualify for FIRST_JUMP
        asset_candles: dict = {}
        for a in assets:
            avg_4h = a["dayNtlVlm"] / 6
            asset_candles[a["name"]] = {
                "4h": [{"v": str(avg_4h * 4)}],  # surge ratio = 4.0
                "1h": [],
            }

        result = engine.scan(all_markets, asset_candles, scan_history)

        first_jumps = [s for s in result.signals if s.signal_tier == 1]
        # At most one FIRST_JUMP per sector (L1)
        l1_first_jumps = [s for s in first_jumps if s.asset in ("ETH", "SOL")]
        assert len(l1_first_jumps) <= 1, (
            f"Expected at most 1 FIRST_JUMP in L1 sector, got {len(l1_first_jumps)}: "
            f"{[s.asset for s in l1_first_jumps]}"
        )


# ---------------------------------------------------------------------------
# Phase 3b — Phase 1 Auto-Cut
# ---------------------------------------------------------------------------

from modules.guard_config import GuardConfig, Tier
from modules.guard_state import GuardState
from modules.trailing_stop import GuardAction, TrailingStopEngine


class TestPhase1AutoCut:
    """Phase 3b — TrailingStopEngine Phase 1 timeout and weak-peak cut."""

    @staticmethod
    def _make_engine_and_state(
        entry: float = 100.0,
        leverage: float = 10.0,
        phase1_max_duration_ms: int = 5_400_000,
        phase1_weak_peak_ms: int = 2_700_000,
        phase1_weak_peak_min_roe: float = 3.0,
    ) -> tuple[TrailingStopEngine, GuardState]:
        cfg = GuardConfig(
            direction="long",
            leverage=leverage,
            phase1_retrace=0.03,
            phase1_max_breaches=3,
            phase1_max_duration_ms=phase1_max_duration_ms,
            phase1_weak_peak_ms=phase1_weak_peak_ms,
            phase1_weak_peak_min_roe=phase1_weak_peak_min_roe,
            tiers=[
                Tier(trigger_pct=10.0, lock_pct=5.0),
                Tier(trigger_pct=20.0, lock_pct=14.0),
            ],
        )
        engine = TrailingStopEngine(cfg)
        state = GuardState(
            instrument="ETH-PERP",
            entry_price=entry,
            high_water=entry,
            direction="long",
            phase1_start_ts=1_000_000,
            created_ts=1_000_000,
            current_tier_index=-1,
        )
        return engine, state

    def test_phase1_timeout_after_91_minutes(self):
        """Position stuck in Phase 1 for 91 min without graduating -> PHASE1_TIMEOUT."""
        engine, state = self._make_engine_and_state(entry=100.0)

        # Simulate ticks every 10 min for 91 min. Price wiggles but never reaches +10% ROE.
        # With leverage=10, tier 0 trigger = 10% ROE -> price = 101.0
        # Keep price at 100.5 (ROE = 5%) — not enough to graduate.
        t = 1_000_000
        last_result = None
        for minute in range(0, 92, 5):
            t = 1_000_000 + minute * 60_000
            price = 100.5 + (minute % 3) * 0.05  # small wiggle
            result = engine.evaluate(price, state, now_ms=t)
            state = result.state
            last_result = result
            if result.action in (GuardAction.PHASE1_TIMEOUT, GuardAction.CLOSE):
                break

        assert last_result is not None
        assert last_result.action == GuardAction.PHASE1_TIMEOUT, (
            f"Expected PHASE1_TIMEOUT but got {last_result.action}: {last_result.reason}"
        )

    def test_weak_peak_cut_after_46_minutes_with_low_peak(self):
        """Position with peak ROE 2% after 46 min -> WEAK_PEAK_CUT."""
        engine, state = self._make_engine_and_state(
            entry=100.0,
            phase1_weak_peak_min_roe=3.0,
        )

        # Price goes up slightly to 100.02 -> peak ROE = (0.02/100)*10*100 = 2%
        # Then stays around there for 46 min.
        t = 1_000_000

        # First push price to create a modest peak
        result = engine.evaluate(100.02, state, now_ms=t + 60_000)
        state = result.state

        # Now advance to 46 min mark with price at or near entry
        t_46min = 1_000_000 + 46 * 60_000
        result = engine.evaluate(100.01, state, now_ms=t_46min)

        assert result.action == GuardAction.WEAK_PEAK_CUT, (
            f"Expected WEAK_PEAK_CUT but got {result.action}: {result.reason}"
        )

    def test_graduation_to_phase2_prevents_timeout(self):
        """Position that graduates to Phase 2 within 30 min is NOT cut."""
        engine, state = self._make_engine_and_state(entry=100.0)

        t = 1_000_000

        # Quickly push price to trigger tier 0 (ROE >= 10% -> price >= 101.0 with 10x lev)
        # Price = 101.1 -> ROE = (1.1/100)*10*100 = 11%
        result = engine.evaluate(101.1, state, now_ms=t + 5 * 60_000)
        state = result.state

        assert result.action == GuardAction.TIER_CHANGED, (
            f"Expected tier graduation but got {result.action}: {result.reason}"
        )
        assert state.current_tier_index >= 0, "Should be in Phase 2 (tier >= 0)"

        # Now advance to 91+ min — should NOT get PHASE1_TIMEOUT since we're in Phase 2
        t_late = 1_000_000 + 95 * 60_000
        result = engine.evaluate(101.1, state, now_ms=t_late)

        assert result.action != GuardAction.PHASE1_TIMEOUT, (
            f"Phase 2 position should not get PHASE1_TIMEOUT, got {result.action}"
        )
        assert result.action != GuardAction.WEAK_PEAK_CUT, (
            f"Phase 2 position should not get WEAK_PEAK_CUT, got {result.action}"
        )


# ---------------------------------------------------------------------------
# Phase 3c — ALO Fee Opt
# ---------------------------------------------------------------------------

from cli.hl_adapter import DirectMockProxy


class TestAloFeeOpt:
    """Phase 3c — ALO order type and Gtc fallback via DirectMockProxy."""

    def test_alo_tif_recorded(self):
        """place_order with tif='Alo' records the tif correctly on the mock."""
        proxy = DirectMockProxy()

        fill = proxy.place_order(
            instrument="ETH-PERP",
            side="buy",
            size=0.1,
            price=3500.0,
            tif="Alo",
        )

        assert fill is not None
        assert proxy._last_tif == "Alo"
        assert fill.instrument == "ETH-PERP"
        assert fill.side == "buy"

    def test_alo_then_gtc_fallback_path(self):
        """Verify that the mock can also be called with tif='Gtc' — simulating fallback."""
        proxy = DirectMockProxy()

        # First call with ALO
        fill_alo = proxy.place_order(
            instrument="ETH-PERP", side="buy", size=0.1, price=3500.0, tif="Alo",
        )
        assert proxy._last_tif == "Alo"

        # Second call with Gtc (simulating the fallback the real adapter would do)
        fill_gtc = proxy.place_order(
            instrument="ETH-PERP", side="buy", size=0.1, price=3500.0, tif="Gtc",
        )
        assert proxy._last_tif == "Gtc"
        assert fill_gtc is not None

    def test_ioc_default_tif(self):
        """Default tif should be Ioc."""
        proxy = DirectMockProxy()
        proxy.place_order(
            instrument="SOL-PERP", side="sell", size=1.0, price=150.0,
        )
        assert proxy._last_tif == "Ioc"


# ---------------------------------------------------------------------------
# Phase 3d — Rotation Cooldown
# ---------------------------------------------------------------------------

from modules.apex_config import ApexConfig
from modules.apex_engine import ApexEngine
from modules.apex_state import ApexSlot, ApexState


class TestRotationCooldown:
    """Phase 3d — min_hold_ms blocks premature exits; slot cooldown blocks reuse."""

    def test_min_hold_blocks_then_allows_conviction_collapse_exit(self):
        """Position with conviction collapse signal at 30 min is blocked; at 46 min is allowed."""
        cfg = ApexConfig(
            max_slots=3,
            leverage=10.0,
            min_hold_ms=2_700_000,       # 45 min
            conviction_collapse_minutes=5,
            max_negative_roe=-50.0,      # set high so hard_stop doesn't fire
            daily_loss_limit=99999,
        )
        engine = ApexEngine(config=cfg)

        # Create state with one active position entered at t=0
        state = ApexState.new(max_slots=3)
        slot = state.slots[0]
        slot.status = "active"
        slot.instrument = "ETH-PERP"
        slot.direction = "long"
        slot.entry_price = 3500.0
        slot.entry_ts = 1_000_000
        slot.current_roe = -2.0  # slightly negative
        slot.current_price = 3493.0
        slot.signal_disappeared_ts = 1_000_000 + 10 * 60_000  # disappeared at 10 min
        slot.last_signal_seen_ts = 1_000_000 + 5 * 60_000

        # At 30 min: under min_hold (30 < 45), conviction collapse should be blocked
        now_30 = 1_000_000 + 30 * 60_000
        actions_30 = engine.evaluate(
            state=state,
            pulse_signals=[],  # no signals -> conviction collapse
            radar_opps=[],
            slot_prices={0: 3493.0},
            slot_guard_results={},
            now_ms=now_30,
        )
        exit_actions_30 = [a for a in actions_30 if a.action == "exit" and "conviction" in a.reason]
        assert len(exit_actions_30) == 0, (
            f"Should NOT exit at 30 min (under min_hold), but got: {[a.reason for a in exit_actions_30]}"
        )

        # At 46 min: past min_hold (46 > 45), conviction collapse should fire
        now_46 = 1_000_000 + 46 * 60_000
        # Reset signal_disappeared_ts so elapsed since disappearance > conviction_collapse_minutes
        slot.signal_disappeared_ts = 1_000_000 + 10 * 60_000  # 36 min ago >> 5 min threshold
        slot.current_roe = -2.0

        actions_46 = engine.evaluate(
            state=state,
            pulse_signals=[],
            radar_opps=[],
            slot_prices={0: 3493.0},
            slot_guard_results={},
            now_ms=now_46,
        )
        exit_actions_46 = [a for a in actions_46 if a.action == "exit" and "conviction" in a.reason]
        assert len(exit_actions_46) == 1, (
            f"Should exit at 46 min (past min_hold), actions: {[a.reason for a in actions_46]}"
        )

    def test_slot_cooldown_blocks_then_allows_reuse(self):
        """Closed slot cannot be reused within cooldown_ms; available after cooldown."""
        cooldown_ms = 300_000  # 5 min
        cfg = ApexConfig(
            max_slots=2,
            slot_cooldown_ms=cooldown_ms,
            leverage=10.0,
        )

        state = ApexState.new(max_slots=2)

        # Slot 0: recently closed at t=10_000_000
        slot0 = state.slots[0]
        slot0.status = "empty"
        slot0.close_ts = 10_000_000

        # Slot 1: active (occupied)
        slot1 = state.slots[1]
        slot1.status = "active"
        slot1.instrument = "ETH-PERP"

        # Immediately after close (t = 10_000_000 + 1_000) — slot 0 should be unavailable
        now_early = 10_000_000 + 1_000
        empty_early = state.get_empty_slot(now_ms=now_early, cooldown_ms=cooldown_ms)
        assert empty_early is None, (
            "Slot should NOT be available within cooldown period"
        )

        # After 5+ min (t = 10_000_000 + 310_000) — slot 0 should be available
        now_late = 10_000_000 + 310_000
        empty_late = state.get_empty_slot(now_ms=now_late, cooldown_ms=cooldown_ms)
        assert empty_late is not None, (
            "Slot should be available after cooldown period expires"
        )
        assert empty_late.slot_id == 0


# ---------------------------------------------------------------------------
# Phase 3e — Risk Guardian
# ---------------------------------------------------------------------------

from parent.risk_manager import RiskGate, RiskManager


class TestRiskGuardian:
    """Phase 3e — RiskManager gate machine state transitions."""

    @staticmethod
    def _make_rm() -> RiskManager:
        rm = RiskManager()
        rm.configure_gate(
            cooldown_duration_ms=1_800_000,  # 30 min
            cooldown_trigger_losses=2,
            cooldown_drawdown_pct=50.0,
        )
        return rm

    def test_open_to_cooldown_via_losses_then_auto_expiry_to_open(self):
        """OPEN -> 2 losses -> COOLDOWN -> auto-expiry (30 min) -> OPEN."""
        rm = self._make_rm()
        t = 1_000_000

        # Starts OPEN
        assert rm.state.risk_gate == RiskGate.OPEN
        assert rm.can_open_position() is True
        assert rm.can_trade() is True

        # First loss — still OPEN
        rm.record_loss(now_ms=t)
        assert rm.state.risk_gate == RiskGate.OPEN
        assert rm.state.consecutive_losses == 1

        # Second loss — transitions to COOLDOWN
        rm.record_loss(now_ms=t + 60_000)
        assert rm.state.risk_gate == RiskGate.COOLDOWN
        assert rm.can_open_position() is False  # entries blocked
        assert rm.can_trade() is True            # exits still allowed

        # Check auto-expiry before duration — still COOLDOWN
        rm.check_auto_expiry(now_ms=t + 60_000 + 1_000_000)  # 16 min
        assert rm.state.risk_gate == RiskGate.COOLDOWN

        # Check auto-expiry after 30 min — back to OPEN
        rm.check_auto_expiry(now_ms=t + 60_000 + 1_800_000)
        assert rm.state.risk_gate == RiskGate.OPEN
        assert rm.can_open_position() is True
        assert rm.state.consecutive_losses == 0

    def test_open_to_cooldown_to_closed_via_loss_then_daily_reset(self):
        """OPEN -> 2 losses -> COOLDOWN -> another loss -> CLOSED -> daily_reset -> OPEN."""
        rm = self._make_rm()
        t = 1_000_000

        # OPEN -> COOLDOWN (2 losses)
        rm.record_loss(now_ms=t)
        rm.record_loss(now_ms=t + 1000)
        assert rm.state.risk_gate == RiskGate.COOLDOWN

        # Loss during COOLDOWN -> CLOSED
        rm.record_loss(now_ms=t + 2000)
        assert rm.state.risk_gate == RiskGate.CLOSED
        assert rm.can_open_position() is False
        assert rm.can_trade() is False

        # Daily reset -> back to OPEN
        rm.daily_reset()
        assert rm.state.risk_gate == RiskGate.OPEN
        assert rm.can_open_position() is True
        assert rm.can_trade() is True
        assert rm.state.consecutive_losses == 0

    def test_can_open_and_can_trade_per_state(self):
        """Verify can_open_position() and can_trade() return correct values in each state."""
        rm = self._make_rm()

        # OPEN
        assert rm.state.risk_gate == RiskGate.OPEN
        assert rm.can_open_position() is True
        assert rm.can_trade() is True

        # Force COOLDOWN
        rm.state.risk_gate = RiskGate.COOLDOWN
        assert rm.can_open_position() is False
        assert rm.can_trade() is True

        # Force CLOSED
        rm.state.risk_gate = RiskGate.CLOSED
        assert rm.can_open_position() is False
        assert rm.can_trade() is False
