"""Tests for 5-tier signal taxonomy (Phase 3a)."""
import pytest
from modules.pulse_config import PulseConfig
from modules.pulse_engine import PulseEngine
from modules.pulse_state import AssetSnapshot, PulseSignal


def _make_snap(asset="BTC", oi=1_000_000, volume=500_000, funding=0.0001, price=50000.0):
    return AssetSnapshot(
        asset=asset,
        timestamp_ms=1000,
        open_interest=oi,
        volume_24h=volume,
        funding_rate=funding,
        mark_price=price,
    )


def _make_history(asset="BTC", oi_values=None, n_scans=6):
    """Build scan_history with given OI progression."""
    if oi_values is None:
        oi_values = [100_000] * n_scans
    history = []
    for oi in oi_values:
        history.append({
            "snapshots": [{"asset": asset, "open_interest": oi, "volume_24h": 500_000}],
        })
    return history


class TestClassifyTier:
    """Test _classify_tier directly."""

    def test_contrib_explosion_both_extreme(self):
        """Tier 2: OI >= 15% AND volume >= 5x."""
        cfg = PulseConfig()
        engine = PulseEngine(config=cfg)
        snap = _make_snap()
        tier = engine._classify_tier(snap, oi_delta_pct=16.0, vol_ratio=5.5, scan_history=[])
        assert tier == 2

    def test_contrib_explosion_only_oi(self):
        """Not tier 2 if only OI is extreme."""
        engine = PulseEngine()
        snap = _make_snap()
        tier = engine._classify_tier(snap, oi_delta_pct=16.0, vol_ratio=2.0, scan_history=[])
        # Should fall to tier 3 (IMMEDIATE_MOVER) since OI >= 15
        assert tier == 3

    def test_immediate_mover_extreme_oi(self):
        """Tier 3: OI >= 15% (either extreme)."""
        engine = PulseEngine()
        snap = _make_snap()
        tier = engine._classify_tier(snap, oi_delta_pct=15.0, vol_ratio=0.5, scan_history=[])
        assert tier == 3

    def test_immediate_mover_extreme_volume(self):
        """Tier 3: volume >= 5x (either extreme)."""
        engine = PulseEngine()
        snap = _make_snap()
        tier = engine._classify_tier(snap, oi_delta_pct=2.0, vol_ratio=5.0, scan_history=[])
        assert tier == 3

    def test_new_entry_deep_oi_high_vol_low(self):
        """Tier 4: OI grows but volume stays low → smart money accumulation."""
        engine = PulseEngine()
        snap = _make_snap()
        tier = engine._classify_tier(snap, oi_delta_pct=9.0, vol_ratio=1.2, scan_history=[])
        assert tier == 4

    def test_new_entry_deep_vol_too_high(self):
        """Not tier 4 if volume is too high."""
        engine = PulseEngine()
        snap = _make_snap()
        tier = engine._classify_tier(snap, oi_delta_pct=9.0, vol_ratio=2.0, scan_history=[])
        # vol_ratio > 1.5 disqualifies tier 4, OI 9% < 15% disqualifies tier 3
        assert tier == 0  # unclassified

    def test_deep_climber_sustained_oi(self):
        """Tier 5: sustained OI climb over 3+ windows."""
        cfg = PulseConfig(deep_climber_min_windows=3, deep_climber_min_oi_pct=5.0)
        engine = PulseEngine(config=cfg)
        snap = _make_snap(asset="ETH")
        # 3 windows: each 6%+ growth (100k → 106k → 112.4k → 119.1k)
        history = _make_history("ETH", [100_000, 106_000, 112_400, 119_200])
        tier = engine._classify_tier(snap, oi_delta_pct=3.0, vol_ratio=0.5, scan_history=history)
        assert tier == 5

    def test_deep_climber_not_enough_windows(self):
        """Not tier 5 with insufficient consecutive climbs (one window stalls)."""
        cfg = PulseConfig(deep_climber_min_windows=3, deep_climber_min_oi_pct=5.0)
        engine = PulseEngine(config=cfg)
        snap = _make_snap(asset="ETH")
        # 4 windows but middle one stalls (100k → 106k → 106k → 112k)
        # Only 1 consecutive climb after the stall, not 2
        history = _make_history("ETH", [100_000, 106_000, 106_000, 112_000])
        tier = engine._classify_tier(snap, oi_delta_pct=3.0, vol_ratio=0.5, scan_history=history)
        assert tier == 0

    def test_first_jump_first_in_sector(self):
        """Tier 1: first asset in sector to show OI+volume breakout."""
        cfg = PulseConfig(sector_map={"BTC": "L1", "ETH": "L1", "SOL": "L1"})
        engine = PulseEngine(config=cfg)
        snap = _make_snap(asset="BTC")
        tier = engine._classify_tier(snap, oi_delta_pct=10.0, vol_ratio=3.5, scan_history=[])
        assert tier == 1

    def test_first_jump_second_in_sector_not_tier1(self):
        """Second asset in same sector doesn't get FIRST_JUMP."""
        cfg = PulseConfig(sector_map={"BTC": "L1", "ETH": "L1"})
        engine = PulseEngine(config=cfg)
        # BTC gets FIRST_JUMP
        snap_btc = _make_snap(asset="BTC")
        tier1 = engine._classify_tier(snap_btc, oi_delta_pct=10.0, vol_ratio=3.5, scan_history=[])
        assert tier1 == 1
        # ETH in same sector → not FIRST_JUMP
        snap_eth = _make_snap(asset="ETH")
        tier2 = engine._classify_tier(snap_eth, oi_delta_pct=10.0, vol_ratio=3.5, scan_history=[])
        assert tier2 != 1  # should be tier 3 (IMMEDIATE_MOVER)

    def test_no_sector_no_first_jump(self):
        """Without sector mapping, FIRST_JUMP is never assigned."""
        engine = PulseEngine()  # empty sector_map
        snap = _make_snap(asset="BTC")
        tier = engine._classify_tier(snap, oi_delta_pct=10.0, vol_ratio=3.5, scan_history=[])
        assert tier != 1

    def test_highest_tier_wins(self):
        """CONTRIB_EXPLOSION (2) takes priority over IMMEDIATE_MOVER (3)."""
        engine = PulseEngine()
        snap = _make_snap()
        # Both extreme OI AND volume → tier 2, not tier 3
        tier = engine._classify_tier(snap, oi_delta_pct=20.0, vol_ratio=6.0, scan_history=[])
        assert tier == 2

    def test_unclassified_returns_zero(self):
        """Low OI and volume → tier 0."""
        engine = PulseEngine()
        snap = _make_snap()
        tier = engine._classify_tier(snap, oi_delta_pct=2.0, vol_ratio=0.5, scan_history=[])
        assert tier == 0


class TestSignalTierInOutput:
    """Verify signal_tier field propagates through PulseSignal."""

    def test_signal_tier_default(self):
        sig = PulseSignal(asset="BTC", signal_type="OI_BREAKOUT", direction="LONG", confidence=60.0)
        assert sig.signal_tier == 0

    def test_signal_tier_set(self):
        sig = PulseSignal(asset="BTC", signal_type="OI_BREAKOUT", direction="LONG",
                         confidence=60.0, signal_tier=2)
        assert sig.signal_tier == 2


class TestConfigRoundtrip:
    """Config fields survive serialization."""

    def test_pulse_config_taxonomy_fields(self):
        cfg = PulseConfig(
            contrib_explosion_oi_pct=18.0,
            new_entry_deep_oi_pct=10.0,
            deep_climber_min_windows=4,
            sector_map={"BTC": "L1", "ETH": "L1"},
        )
        d = cfg.to_dict()
        cfg2 = PulseConfig.from_dict(d)
        assert cfg2.contrib_explosion_oi_pct == 18.0
        assert cfg2.new_entry_deep_oi_pct == 10.0
        assert cfg2.deep_climber_min_windows == 4
        assert cfg2.sector_map == {"BTC": "L1", "ETH": "L1"}
