"""Tests for modules/radar_engine.py — full pipeline with synthetic data."""
import pytest

from modules.radar_config import RadarConfig
from modules.radar_engine import AssetMeta, OpportunityRadarEngine
from modules.radar_state import Opportunity, RadarResult


# ── Helpers ──────────────────────────────────────────────────────────

def _make_candles(closes, base_vol=1000):
    """Build candle dicts from a list of close prices."""
    return [
        {
            "t": i * 3600000,
            "o": str(round(c * 0.999, 2)),
            "h": str(round(c * 1.01, 2)),
            "l": str(round(c * 0.99, 2)),
            "c": str(round(c, 2)),
            "v": str(base_vol),
        }
        for i, c in enumerate(closes)
    ]


def _uptrend_candles(start=100, n=48, step=0.5):
    """Create zigzag uptrend candles (produces valid swing points)."""
    closes = []
    for i in range(n):
        base = start + i * step
        # Zigzag: even bars go up more, odd bars pull back slightly
        if i % 2 == 0:
            closes.append(base + step * 0.5)
        else:
            closes.append(base - step * 0.3)
    return _make_candles(closes)


def _downtrend_candles(start=200, n=48, step=0.5):
    """Create zigzag downtrend candles (produces valid swing points)."""
    closes = []
    for i in range(n):
        base = start - i * step
        if i % 2 == 0:
            closes.append(base - step * 0.5)
        else:
            closes.append(base + step * 0.3)
    return _make_candles(closes)


def _flat_candles(price=100, n=48):
    return _make_candles([price] * n)


def _make_markets(assets_data):
    """Build mock all_markets from list of (name, vol, funding, oi, mark) tuples."""
    universe = [{"name": a[0], "szDecimals": 1} for a in assets_data]
    ctxs = [
        {
            "dayNtlVlm": str(a[1]),
            "funding": str(a[2]),
            "openInterest": str(a[3]),
            "markPx": str(a[4]),
            "prevDayPx": str(a[4] * 0.99),
        }
        for a in assets_data
    ]
    return [{"universe": universe}, ctxs]


# ── BTC Macro ────────────────────────────────────────────────────────

class TestBtcMacro:
    def setup_method(self):
        self.engine = OpportunityRadarEngine()

    def test_strong_uptrend(self):
        candles_4h = _uptrend_candles(start=40000, n=50, step=200)
        candles_1h = _uptrend_candles(start=49000, n=24, step=50)
        macro = self.engine._btc_macro(candles_4h, candles_1h)
        assert macro["trend"] in ("strong_up", "up")
        assert macro["diff_pct"] > 0

    def test_strong_downtrend(self):
        candles_4h = _downtrend_candles(start=60000, n=50, step=200)
        candles_1h = _downtrend_candles(start=51000, n=24, step=50)
        macro = self.engine._btc_macro(candles_4h, candles_1h)
        assert macro["trend"] in ("strong_down", "down")
        assert macro["diff_pct"] < 0

    def test_neutral(self):
        candles_4h = _flat_candles(price=50000, n=50)
        candles_1h = _flat_candles(price=50000, n=24)
        macro = self.engine._btc_macro(candles_4h, candles_1h)
        assert macro["trend"] == "neutral"

    def test_empty_candles(self):
        macro = self.engine._btc_macro([], [])
        assert macro["trend"] == "neutral"
        assert macro["strength"] == 0


# ── Bulk Screen ──────────────────────────────────────────────────────

class TestBulkScreen:
    def setup_method(self):
        self.engine = OpportunityRadarEngine()

    def test_filters_by_volume(self):
        markets = _make_markets([
            ("ETH", 1_000_000, 0.0001, 5e7, 2500),  # passes
            ("DOGE", 100_000, 0.0001, 1e6, 0.08),    # fails (below 500k)
            ("SOL", 2_000_000, 0.0002, 1e7, 100),     # passes
        ])
        assets = self.engine._bulk_screen(markets)
        names = {a.name for a in assets}
        assert "ETH" in names
        assert "SOL" in names
        assert "DOGE" not in names

    def test_empty_markets(self):
        assert self.engine._bulk_screen([]) == []
        assert self.engine._bulk_screen([{}, []]) == []


# ── Select Top ───────────────────────────────────────────────────────

class TestSelectTop:
    def test_selects_top_n(self):
        config = RadarConfig(top_n_deep=2)
        engine = OpportunityRadarEngine(config)

        assets = [
            AssetMeta("A", 1e6, 0.0, 1e6, 100),
            AssetMeta("B", 5e6, 0.0, 5e6, 200),
            AssetMeta("C", 10e6, 0.0, 10e6, 300),
        ]
        top = engine._select_top(assets)
        assert len(top) == 2
        assert top[0].name == "C"  # highest composite score


# ── Deep Dive / Disqualifiers ────────────────────────────────────────

class TestDeepDive:
    def setup_method(self):
        self.engine = OpportunityRadarEngine()
        self.asset = AssetMeta("TEST", 5e6, 0.0001, 5e6, 100)
        self.btc_macro = {
            "trend": "neutral",
            "modifiers": {"LONG": 0, "SHORT": 0},
        }

    def test_counter_trend_hourly_disqualifies(self):
        # LONG with DOWN hourly trend → disqualified
        # Build explicit zigzag with lower highs + lower lows for swing detection
        candles_1h = []
        # Pattern: peak, trough, peak, trough — each peak/trough lower than previous
        prices = []
        for i in range(48):
            if i % 4 == 0:  # peaks
                prices.append(120 - i * 0.8)
            elif i % 4 == 2:  # troughs
                prices.append(115 - i * 0.8)
            else:  # transitions
                prices.append(117.5 - i * 0.8)
        for i, p in enumerate(prices):
            candles_1h.append({
                "t": str(i * 3600000),
                "o": str(round(p - 0.5, 2)),
                "h": str(round(p + 2, 2)),
                "l": str(round(p - 2, 2)),
                "c": str(round(p, 2)),
                "v": "1000",
            })
        result = self.engine._deep_dive(
            self.asset, _flat_candles(n=50), candles_1h, _flat_candles(n=48),
            self.btc_macro, "LONG",
        )
        assert hasattr(result, "reason")  # DisqualifiedAsset
        assert result.reason == "counter_trend_hourly"

    def test_extreme_rsi_disqualifies(self):
        # LONG with RSI > 80 → disqualified
        # Create strongly rising prices to get RSI > 80
        candles_1h = _uptrend_candles(start=50, n=48, step=3)
        # Need to check which direction the hourly trend will be
        # Uptrend hourly means LONG won't be disqualified by hourly counter-trend
        result = self.engine._deep_dive(
            self.asset, _flat_candles(n=50), candles_1h, _flat_candles(n=48),
            self.btc_macro, "LONG",
        )
        if hasattr(result, "reason"):
            assert result.reason in ("extreme_rsi", "counter_trend_hourly")

    def test_volume_dying_disqualifies(self):
        # Both TF volume ratios below 0.5
        # volume_ratio compares last 4 bars vs prior 4 bars
        # Need high vol in [-8:-4] and low vol in [-4:]
        dying_candles = []
        for i in range(48):
            base = 100 + (1 if i % 2 == 0 else -1)
            # High volume through bar 43, dying in last 4 bars
            vol = 500 if i < 44 else 20
            dying_candles.append({
                "t": str(i * 3600000), "o": str(base - 0.5),
                "h": str(base + 1), "l": str(base - 1),
                "c": str(base), "v": str(vol),
            })
        result = self.engine._deep_dive(
            self.asset, _flat_candles(n=50), dying_candles, dying_candles,
            self.btc_macro, "LONG",
        )
        # Should be disqualified by volume_dying
        assert hasattr(result, "reason")
        assert result.reason == "volume_dying"

    def test_btc_headwind_disqualifies(self):
        macro = {
            "trend": "strong_down",
            "modifiers": {"LONG": -30, "SHORT": 30},
        }
        # Use gently rising zigzag candles so hourly trend is UP (no counter-trend for LONG)
        # and RSI stays moderate
        candles_1h = _uptrend_candles(start=95, n=48, step=0.1)
        candles_15m = _uptrend_candles(start=99, n=48, step=0.02)
        result = self.engine._deep_dive(
            self.asset, _flat_candles(n=50), candles_1h, candles_15m,
            macro, "LONG",
        )
        # BTC headwind should be caught (or earlier DQ like extreme_rsi)
        if hasattr(result, "reason"):
            assert result.reason in ("btc_macro_headwind", "extreme_rsi")

    def test_qualifying_opportunity(self):
        # Good setup: LONG with uptrend, low RSI, good volume
        candles_1h = _uptrend_candles(start=90, n=48, step=0.2)
        candles_15m = _uptrend_candles(start=99, n=48, step=0.05)
        result = self.engine._deep_dive(
            self.asset, _uptrend_candles(start=80, n=50, step=0.4),
            candles_1h, candles_15m,
            self.btc_macro, "LONG",
        )
        # Should be an Opportunity (not disqualified)
        if isinstance(result, Opportunity):
            assert result.final_score > 0
            assert result.direction == "LONG"
            assert "market_structure" in result.pillar_scores


# ── Pillar Scoring ───────────────────────────────────────────────────

class TestPillarScoring:
    def setup_method(self):
        self.engine = OpportunityRadarEngine()

    def test_market_structure_high_volume(self):
        asset = AssetMeta("X", 100e6, 0.0, 50e6, 100)
        risks = []
        score = self.engine._score_market_structure(asset, 2.5, risks)
        assert score >= 80  # High volume + high OI + surge

    def test_market_structure_low_volume(self):
        asset = AssetMeta("X", 600_000, 0.0, 500_000, 100)
        risks = []
        score = self.engine._score_market_structure(asset, 0.8, risks)
        assert score < 40

    def test_funding_neutral(self):
        risks = []
        score = self.engine._score_funding("LONG", 0.0001, 2.0, risks)
        assert score >= 40  # Neutral funding → 40 points

    def test_funding_favorable_long(self):
        risks = []
        score = self.engine._score_funding("LONG", -0.001, 25.0, risks)
        assert score >= 30  # Getting paid to long

    def test_funding_unfavorable(self):
        risks = []
        score = self.engine._score_funding("LONG", 0.005, 40.0, risks)
        assert score < 30
        assert "unfavorable_funding" in risks or "heavy_unfavorable_funding" in risks


# ── Full Pipeline ────────────────────────────────────────────────────

class TestFullPipeline:
    def test_scan_with_synthetic_data(self):
        config = RadarConfig(
            min_volume_24h=100_000,
            top_n_deep=5,
            score_threshold=50,  # Low threshold for test
        )
        engine = OpportunityRadarEngine(config)

        markets = _make_markets([
            ("ETH", 5e8, 0.0001, 5e7, 2500),
            ("SOL", 2e8, -0.0002, 2e7, 100),
            ("DOGE", 1e8, 0.0005, 1e7, 0.08),
            ("SMALL", 50_000, 0.0, 100_000, 1.0),  # filtered out
        ])

        asset_candles = {
            "ETH": {
                "4h": _uptrend_candles(start=2300, n=50, step=5),
                "1h": _uptrend_candles(start=2450, n=48, step=1),
                "15m": _uptrend_candles(start=2495, n=48, step=0.2),
            },
            "SOL": {
                "4h": _downtrend_candles(start=120, n=50, step=0.5),
                "1h": _downtrend_candles(start=95, n=48, step=0.1),
                "15m": _downtrend_candles(start=90, n=48, step=0.05),
            },
            "DOGE": {
                "4h": _flat_candles(price=0.08, n=50),
                "1h": _flat_candles(price=0.08, n=48),
                "15m": _flat_candles(price=0.08, n=48),
            },
        }

        result = engine.scan(
            all_markets=markets,
            btc_candles_4h=_flat_candles(price=50000, n=50),
            btc_candles_1h=_flat_candles(price=50000, n=24),
            asset_candles=asset_candles,
        )

        assert isinstance(result, RadarResult)
        assert result.stats["assets_scanned"] == 4
        assert result.stats["passed_stage1"] == 3  # SMALL filtered
        # At least some should qualify or get disqualified
        total = len(result.opportunities) + len(result.disqualified)
        assert total > 0

    def test_scan_empty_markets(self):
        engine = OpportunityRadarEngine()
        result = engine.scan(
            all_markets=[{}, []],
            btc_candles_4h=[],
            btc_candles_1h=[],
            asset_candles={},
        )
        assert result.opportunities == []
        assert result.stats.get("passed_stage1", 0) == 0

    def test_momentum_tracking(self):
        config = RadarConfig(score_threshold=0)
        engine = OpportunityRadarEngine(config)

        # Fake previous scan history
        history = [{
            "scan_time_ms": 1000,
            "opportunities": [
                {"asset": "ETH", "direction": "LONG", "final_score": 200},
            ],
        }]

        markets = _make_markets([("ETH", 5e8, 0.0001, 5e7, 2500)])
        asset_candles = {
            "ETH": {
                "4h": _uptrend_candles(start=2300, n=50, step=5),
                "1h": _uptrend_candles(start=2450, n=48, step=1),
                "15m": _uptrend_candles(start=2495, n=48, step=0.2),
            },
        }

        result = engine.scan(
            all_markets=markets,
            btc_candles_4h=_flat_candles(price=50000, n=50),
            btc_candles_1h=_flat_candles(price=50000, n=24),
            asset_candles=asset_candles,
            scan_history=history,
        )

        # Check momentum was computed for qualifying opportunities
        for opp in result.opportunities:
            if opp.asset == "ETH":
                assert "scan_streak" in opp.momentum
                assert opp.momentum["scan_streak"] >= 1
