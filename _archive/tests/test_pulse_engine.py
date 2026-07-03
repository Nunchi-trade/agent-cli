"""Tests for modules/pulse_engine.py — signal detection with synthetic data."""
import pytest

from modules.pulse_config import PulseConfig
from modules.pulse_engine import PulseEngine
from modules.pulse_state import AssetSnapshot, PulseResult


def _make_markets(assets_data):
    """Build mock all_markets from list of (name, vol, funding, oi, mark) tuples."""
    universe = [{"name": a[0], "szDecimals": 1} for a in assets_data]
    ctxs = [
        {
            "dayNtlVlm": str(a[1]),
            "funding": str(a[2]),
            "openInterest": str(a[3]),
            "markPx": str(a[4]),
        }
        for a in assets_data
    ]
    return [{"universe": universe}, ctxs]


def _make_history_scan(assets_data, scan_time=1000):
    """Build a fake scan history entry."""
    snapshots = [
        {"asset": a[0], "timestamp_ms": scan_time,
         "open_interest": a[3], "volume_24h": a[1],
         "funding_rate": a[2], "mark_price": a[4]}
        for a in assets_data
    ]
    return {"scan_time_ms": scan_time, "signals": [], "snapshots": snapshots, "stats": {}}


def _make_candles_4h(volumes):
    """Build 4h candles with specified volumes."""
    return [
        {"t": str(i * 14400000), "o": "100", "h": "101", "l": "99",
         "c": "100", "v": str(v)}
        for i, v in enumerate(volumes)
    ]


def _make_candles_1h(prices):
    """Build 1h candles from close prices."""
    return [
        {"t": str(i * 3600000), "o": str(p - 0.5), "h": str(p + 1),
         "l": str(p - 1), "c": str(p), "v": "100"}
        for i, p in enumerate(prices)
    ]


class TestOiDelta:
    def setup_method(self):
        self.engine = PulseEngine()

    def test_detects_oi_breakout(self):
        snap = AssetSnapshot(asset="ETH", open_interest=1.1e8)
        # Baseline at 1e8, current at 1.1e8 = 10% increase > 8% threshold
        result = self.engine._detect_oi_delta(snap, 1e8)
        assert result is not None
        assert result["delta_pct"] == pytest.approx(10.0)

    def test_no_signal_below_threshold(self):
        snap = AssetSnapshot(asset="ETH", open_interest=1.05e8)
        # 5% increase < 8% threshold
        result = self.engine._detect_oi_delta(snap, 1e8)
        assert result is None

    def test_no_baseline(self):
        snap = AssetSnapshot(asset="ETH", open_interest=1e8)
        assert self.engine._detect_oi_delta(snap, None) is None

    def test_zero_baseline(self):
        snap = AssetSnapshot(asset="ETH", open_interest=1e8)
        assert self.engine._detect_oi_delta(snap, 0) is None


class TestVolumeSurge:
    def setup_method(self):
        self.engine = PulseEngine()

    def test_detects_surge(self):
        snap = AssetSnapshot(asset="ETH", volume_24h=6e6)  # avg 4h = 1e6
        candles = {"4h": _make_candles_4h([1e6, 1e6, 1e6, 1e6, 1e6, 4e6])}
        result = self.engine._detect_volume_surge(snap, candles)
        assert result is not None
        assert result["surge_ratio"] >= 3.0

    def test_no_surge(self):
        snap = AssetSnapshot(asset="ETH", volume_24h=6e6)
        candles = {"4h": _make_candles_4h([1e6, 1e6, 1e6, 1e6, 1e6, 1e6])}
        result = self.engine._detect_volume_surge(snap, candles)
        assert result is None

    def test_no_candles(self):
        snap = AssetSnapshot(asset="ETH", volume_24h=6e6)
        assert self.engine._detect_volume_surge(snap, {}) is None


class TestFundingFlip:
    def setup_method(self):
        self.engine = PulseEngine()

    def test_detects_flip(self):
        snap = AssetSnapshot(asset="ETH", funding_rate=-0.001)
        # Previous was positive, now negative
        result = self.engine._detect_funding_flip(snap, [0.001])
        assert result is not None
        assert result["type"] == "flip"

    def test_detects_acceleration(self):
        snap = AssetSnapshot(asset="ETH", funding_rate=0.003)
        result = self.engine._detect_funding_flip(snap, [0.001])
        assert result is not None
        assert result["type"] == "acceleration"

    def test_no_flip_same_direction(self):
        snap = AssetSnapshot(asset="ETH", funding_rate=0.0011)
        result = self.engine._detect_funding_flip(snap, [0.001])
        assert result is None  # only 10% increase, below 50% threshold

    def test_no_history(self):
        snap = AssetSnapshot(asset="ETH", funding_rate=0.001)
        assert self.engine._detect_funding_flip(snap, []) is None


class TestPriceBreakout:
    def setup_method(self):
        self.engine = PulseEngine()

    def test_breakout_up(self):
        prices = [100.0] * 24 + [105.0]  # 25 1h candles, last one breaks out
        candles = _make_candles_1h(prices)
        snap = AssetSnapshot(asset="ETH", mark_price=105.0)
        result = self.engine._detect_price_breakout(snap, candles)
        assert result is not None
        assert result["direction"] == "up"

    def test_breakout_down(self):
        prices = [100.0] * 24 + [95.0]
        candles = _make_candles_1h(prices)
        snap = AssetSnapshot(asset="ETH", mark_price=95.0)
        result = self.engine._detect_price_breakout(snap, candles)
        assert result is not None
        assert result["direction"] == "down"

    def test_no_breakout(self):
        prices = [100.0] * 25
        candles = _make_candles_1h(prices)
        snap = AssetSnapshot(asset="ETH", mark_price=100.5)
        result = self.engine._detect_price_breakout(snap, candles)
        assert result is None


class TestDirectionClassification:
    def setup_method(self):
        self.engine = PulseEngine()

    def test_long_from_positive_funding(self):
        snap = AssetSnapshot(asset="ETH", funding_rate=0.001)
        direction = self.engine._classify_direction(None, None, None, None, snap)
        assert direction == "LONG"

    def test_short_from_negative_funding(self):
        snap = AssetSnapshot(asset="ETH", funding_rate=-0.001)
        direction = self.engine._classify_direction(None, None, None, None, snap)
        assert direction == "SHORT"

    def test_long_from_breakout_up(self):
        snap = AssetSnapshot(asset="ETH", funding_rate=0)
        breakout = {"direction": "up"}
        direction = self.engine._classify_direction(None, None, None, breakout, snap)
        assert direction == "LONG"


class TestSignalClassification:
    def setup_method(self):
        self.engine = PulseEngine()

    def test_immediate_mover(self):
        oi = {"delta_pct": 20.0}
        vol = {"surge_ratio": 6.0}
        result = self.engine._classify_signal_type(oi, vol, None, None)
        assert result == "IMMEDIATE_MOVER"

    def test_oi_breakout(self):
        oi = {"delta_pct": 10.0}
        result = self.engine._classify_signal_type(oi, None, None, None)
        assert result == "OI_BREAKOUT"

    def test_volume_surge(self):
        vol = {"surge_ratio": 4.0}
        result = self.engine._classify_signal_type(None, vol, None, None)
        assert result == "VOLUME_SURGE"

    def test_funding_flip(self):
        fund = {"shift": -0.002}
        result = self.engine._classify_signal_type(None, None, fund, None)
        assert result == "FUNDING_FLIP"


class TestFullPipeline:
    def test_first_scan_no_signals(self):
        """First scan (no history) should produce no signals."""
        engine = PulseEngine()
        markets = _make_markets([
            ("ETH", 5e8, 0.001, 5e7, 2500),
            ("SOL", 2e8, -0.001, 2e7, 100),
        ])
        result = engine.scan(all_markets=markets, asset_candles={}, scan_history=[])
        assert result.signals == []
        assert result.stats["has_baseline"] is False

    def test_second_scan_with_oi_spike(self):
        """After building baseline, OI spike should produce signal."""
        engine = PulseEngine(PulseConfig(min_scans_for_signal=2))

        baseline = [("ETH", 5e8, 0.001, 5e7, 2500), ("SOL", 2e8, -0.001, 2e7, 100)]
        history = [
            _make_history_scan(baseline, 1000),
            _make_history_scan(baseline, 2000),
        ]

        # Current scan: ETH OI jumped 20%
        markets = _make_markets([
            ("ETH", 5e8, 0.001, 6e7, 2500),  # OI: 5e7 -> 6e7 = +20%
            ("SOL", 2e8, -0.001, 2e7, 100),
        ])

        result = engine.scan(all_markets=markets, asset_candles={}, scan_history=history)
        assert result.stats["has_baseline"] is True
        eth_signals = [s for s in result.signals if s.asset == "ETH"]
        assert len(eth_signals) > 0
        assert eth_signals[0].signal_type == "OI_BREAKOUT"
        assert eth_signals[0].oi_delta_pct > 15

    def test_volume_minimum_filter(self):
        """Assets below volume minimum should be excluded."""
        engine = PulseEngine(PulseConfig(min_scans_for_signal=1))

        history = [_make_history_scan([("SMALL", 100_000, 0, 1e6, 1.0)])]
        markets = _make_markets([("SMALL", 100_000, 0, 2e6, 1.0)])

        result = engine.scan(all_markets=markets, asset_candles={}, scan_history=history)
        assert result.stats["qualifying"] == 0

    def test_empty_markets(self):
        engine = PulseEngine()
        result = engine.scan(all_markets=[{}, []], asset_candles={}, scan_history=[])
        assert result.signals == []
        assert result.stats["total_assets"] == 0

    def test_config_presets(self):
        from modules.pulse_config import PULSE_PRESETS
        assert "default" in PULSE_PRESETS
        assert "sensitive" in PULSE_PRESETS
        assert PULSE_PRESETS["sensitive"].oi_delta_breakout_pct < PULSE_PRESETS["default"].oi_delta_breakout_pct
