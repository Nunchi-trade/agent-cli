"""Tests for smart money tracker and APEX engine integration."""
import pytest

from modules.smart_money.config import SmartMoneyConfig
from modules.smart_money.tracker import SmartMoneyTracker, WalletSnapshot
from modules.apex_config import ApexConfig
from modules.apex_engine import ApexEngine
from modules.apex_state import ApexState


# ---------------------------------------------------------------------------
# Mock HL proxy for testing
# ---------------------------------------------------------------------------

class MockInfo:
    """Mock HL Info API that returns configurable user_state."""

    def __init__(self, states: dict = None):
        # address -> user_state response dict
        self._states = states or {}

    def user_state(self, address: str) -> dict:
        return self._states.get(address, {"assetPositions": []})


class MockHL:
    """Mock HL proxy exposing _info."""

    def __init__(self, states: dict = None):
        self._info = MockInfo(states)


def _make_user_state(*positions):
    """Build a user_state dict from (coin, size, entry_px) tuples.

    Positive size = long, negative size = short.
    """
    asset_positions = []
    for coin, size, entry_px in positions:
        asset_positions.append({
            "position": {
                "coin": coin,
                "szi": str(size),
                "entryPx": str(entry_px),
            }
        })
    return {"assetPositions": asset_positions}


# ---------------------------------------------------------------------------
# Tests: SmartMoneyTracker
# ---------------------------------------------------------------------------

class TestDetectNewPosition:
    """First scan detects existing positions as 'opened'."""

    def test_detect_new_position(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice"],
            min_position_usd=10_000.0,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),  # $25,000 long
        })

        signals = tracker.scan(hl)
        assert len(signals) == 1
        assert signals[0]["asset"] == "ETH"
        assert signals[0]["direction"] == "LONG"
        assert signals[0]["signal_type"] == "SMART_MONEY"

    def test_ignores_small_positions(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice"],
            min_position_usd=10_000.0,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("DOGE", 100.0, 0.05)),  # $5 notional
        })

        signals = tracker.scan(hl)
        assert len(signals) == 0


class TestDetectFlip:
    """Wallet flips long to short."""

    def test_detect_flip(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xbob"],
            min_position_usd=10_000.0,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)
        hl_long = MockHL(states={
            "0xbob": _make_user_state(("ETH", 10.0, 2500.0)),
        })
        hl_short = MockHL(states={
            "0xbob": _make_user_state(("ETH", -10.0, 2500.0)),
        })

        # First scan — detect as opened
        tracker.scan(hl_long)

        # Second scan — detect flip
        signals = tracker.scan(hl_short)
        assert len(signals) == 1
        assert signals[0]["direction"] == "SHORT"


class TestDetectIncrease:
    """Wallet increases position by >20%."""

    def test_detect_increase(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xcarl"],
            min_position_usd=10_000.0,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)
        hl_initial = MockHL(states={
            "0xcarl": _make_user_state(("SOL", 500.0, 100.0)),  # $50,000
        })
        hl_increased = MockHL(states={
            "0xcarl": _make_user_state(("SOL", 700.0, 100.0)),  # $70,000 (+40%)
        })

        # First scan
        tracker.scan(hl_initial)

        # Second scan — increase detected
        signals = tracker.scan(hl_increased)
        assert len(signals) == 1
        assert signals[0]["asset"] == "SOL"
        assert signals[0]["direction"] == "LONG"

    def test_no_signal_on_small_increase(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xcarl"],
            min_position_usd=10_000.0,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)
        hl_initial = MockHL(states={
            "0xcarl": _make_user_state(("SOL", 500.0, 100.0)),  # $50,000
        })
        hl_small_increase = MockHL(states={
            "0xcarl": _make_user_state(("SOL", 550.0, 100.0)),  # $55,000 (+10%)
        })

        tracker.scan(hl_initial)
        signals = tracker.scan(hl_small_increase)
        assert len(signals) == 0


class TestConvergenceSignal:
    """2+ wallets on same direction = HIGH_CONVICTION."""

    def test_convergence_high_conviction(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice", "0xbob"],
            min_position_usd=10_000.0,
            conviction_threshold=2,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),  # $25k long
            "0xbob": _make_user_state(("ETH", 8.0, 2500.0)),     # $20k long
        })

        signals = tracker.scan(hl)
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "HIGH_CONVICTION"
        assert len(signals[0]["source_addresses"]) == 2

    def test_single_wallet_is_smart_money(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice"],
            min_position_usd=10_000.0,
            conviction_threshold=2,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),
        })

        signals = tracker.scan(hl)
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "SMART_MONEY"


class TestMinPositionFilter:
    """Positions below min_position_usd are ignored."""

    def test_below_min(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice"],
            min_position_usd=50_000.0,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),  # $25k < $50k min
        })

        signals = tracker.scan(hl)
        assert len(signals) == 0

    def test_above_min(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice"],
            min_position_usd=20_000.0,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),  # $25k > $20k min
        })

        signals = tracker.scan(hl)
        assert len(signals) == 1


class TestPollInterval:
    """Tracker only polls every N ticks."""

    def test_skips_non_interval_ticks(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice"],
            min_position_usd=10_000.0,
            poll_interval_ticks=3,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),
        })

        # Tick 1: skip
        assert tracker.scan(hl) == []
        # Tick 2: skip
        assert tracker.scan(hl) == []
        # Tick 3: poll
        signals = tracker.scan(hl)
        assert len(signals) == 1

    def test_polls_on_every_interval(self):
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice"],
            min_position_usd=10_000.0,
            poll_interval_ticks=2,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),
        })

        # Tick 1: skip
        assert tracker.scan(hl) == []
        # Tick 2: poll (first scan, detects as new)
        signals = tracker.scan(hl)
        assert len(signals) == 1
        # Tick 3: skip
        assert tracker.scan(hl) == []
        # Tick 4: poll (no changes, no new signals)
        signals = tracker.scan(hl)
        assert len(signals) == 0


class TestConfidenceCalculation:
    """Verify confidence formula: min(60 + wallets*10 + (notional/100k)*10, 100)."""

    def test_single_wallet_25k(self):
        # 60 + 1*10 + (25000/100000)*10 = 60 + 10 + 2.5 = 72.5
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice"],
            min_position_usd=10_000.0,
            conviction_threshold=2,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),  # $25k
        })

        signals = tracker.scan(hl)
        assert signals[0]["confidence"] == 72.5

    def test_two_wallets_45k(self):
        # 60 + 2*10 + (45000/100000)*10 = 60 + 20 + 4.5 = 84.5
        cfg = SmartMoneyConfig(
            watch_addresses=["0xalice", "0xbob"],
            min_position_usd=10_000.0,
            conviction_threshold=2,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xalice": _make_user_state(("ETH", 10.0, 2500.0)),  # $25k
            "0xbob": _make_user_state(("ETH", 8.0, 2500.0)),     # $20k
        })

        signals = tracker.scan(hl)
        assert signals[0]["confidence"] == 84.5

    def test_confidence_capped_at_100(self):
        # Many wallets, huge notional -> capped
        cfg = SmartMoneyConfig(
            watch_addresses=["0xa", "0xb", "0xc", "0xd", "0xe"],
            min_position_usd=10_000.0,
            conviction_threshold=2,
            poll_interval_ticks=1,
        )
        tracker = SmartMoneyTracker(cfg)

        hl = MockHL(states={
            "0xa": _make_user_state(("BTC", 10.0, 60000.0)),   # $600k
            "0xb": _make_user_state(("BTC", 10.0, 60000.0)),
            "0xc": _make_user_state(("BTC", 10.0, 60000.0)),
            "0xd": _make_user_state(("BTC", 10.0, 60000.0)),
            "0xe": _make_user_state(("BTC", 10.0, 60000.0)),
        })

        signals = tracker.scan(hl)
        assert signals[0]["confidence"] == 100.0


# ---------------------------------------------------------------------------
# Tests: APEX engine integration with smart money signals
# ---------------------------------------------------------------------------

class TestApexEngineSmartMoney:
    """Smart money signals flow through APEX engine entry evaluation."""

    def _make_state(self, max_slots=3):
        return ApexState.new(max_slots)

    def test_smart_money_entry(self):
        engine = ApexEngine(ApexConfig())
        state = self._make_state()

        sm_signals = [{
            "asset": "ETH",
            "signal_type": "SMART_MONEY",
            "direction": "LONG",
            "confidence": 75.0,
            "source_addresses": ["0xalice"],
            "notional_usd": 50000.0,
        }]

        actions = engine.evaluate(state, [], [], {}, {}, smart_money_signals=sm_signals)
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].instrument == "ETH-PERP"
        assert "smart_money" in entries[0].source

    def test_high_conviction_beats_scanner(self):
        """HIGH_CONVICTION (priority 1.5) should beat scanner (priority 2)."""
        engine = ApexEngine(ApexConfig(max_slots=1))
        state = self._make_state(max_slots=1)

        scanner = [{"asset": "SOL", "direction": "LONG", "final_score": 200}]
        sm_signals = [{
            "asset": "ETH",
            "signal_type": "HIGH_CONVICTION",
            "direction": "LONG",
            "confidence": 85.0,
            "source_addresses": ["0xalice", "0xbob"],
            "notional_usd": 80000.0,
        }]

        actions = engine.evaluate(state, [], scanner, {}, {}, smart_money_signals=sm_signals)
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].instrument == "ETH-PERP"

    def test_smart_money_below_confidence_skipped(self):
        engine = ApexEngine(ApexConfig())
        state = self._make_state()

        sm_signals = [{
            "asset": "ETH",
            "signal_type": "SMART_MONEY",
            "direction": "LONG",
            "confidence": 50.0,  # Below 60 threshold
        }]

        actions = engine.evaluate(state, [], [], {}, {}, smart_money_signals=sm_signals)
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 0

    def test_backward_compatible_no_smart_money(self):
        """Engine works fine without smart_money_signals parameter."""
        engine = ApexEngine(ApexConfig())
        state = self._make_state()

        # No smart_money_signals param at all
        actions = engine.evaluate(state, [], [], {}, {})
        assert actions == []

    def test_pulse_immediate_still_beats_high_conviction(self):
        """Pulse IMMEDIATE (priority 1) still beats HIGH_CONVICTION (priority 1.5)."""
        engine = ApexEngine(ApexConfig(max_slots=1))
        state = self._make_state(max_slots=1)

        movers = [{
            "asset": "SOL", "signal_type": "IMMEDIATE_MOVER",
            "direction": "LONG", "confidence": 100,
        }]
        sm_signals = [{
            "asset": "ETH", "signal_type": "HIGH_CONVICTION",
            "direction": "LONG", "confidence": 95.0,
            "source_addresses": ["0xa", "0xb"], "notional_usd": 100000.0,
        }]

        actions = engine.evaluate(state, movers, [], {}, {}, smart_money_signals=sm_signals)
        entries = [a for a in actions if a.action == "enter"]
        assert len(entries) == 1
        assert entries[0].instrument == "SOL-PERP"
        assert entries[0].source == "pulse_immediate"
