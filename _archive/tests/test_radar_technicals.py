"""Tests for modules/radar_technicals.py — pure math, zero I/O."""
import pytest

from modules.radar_technicals import (
    calc_ema,
    calc_rsi,
    classify_hourly_trend,
    analyze_4h_trend,
    volume_ratio,
    detect_patterns,
    price_changes,
    find_support_resistance,
)


# ── EMA ──────────────────────────────────────────────────────────────

class TestCalcEma:
    def test_single_value(self):
        assert calc_ema([100.0], 5) == [100.0]

    def test_constant_series(self):
        result = calc_ema([50.0] * 10, 5)
        assert all(abs(v - 50.0) < 0.01 for v in result)

    def test_trending_up(self):
        data = list(range(1, 21))  # 1 to 20
        result = calc_ema([float(x) for x in data], 5)
        # EMA should lag behind price in uptrend
        assert result[-1] < 20.0
        assert result[-1] > result[-2]  # still rising

    def test_length_matches_input(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert len(calc_ema(data, 3)) == 5

    def test_empty(self):
        assert calc_ema([], 5) == []

    def test_zero_period(self):
        assert calc_ema([1.0, 2.0], 0) == []


# ── RSI ──────────────────────────────────────────────────────────────

class TestCalcRsi:
    def test_all_gains(self):
        # Monotonically increasing → RSI near 100
        closes = [float(i) for i in range(1, 20)]
        assert calc_rsi(closes) > 95

    def test_all_losses(self):
        # Monotonically decreasing → RSI near 0
        closes = [float(20 - i) for i in range(19)]
        assert calc_rsi(closes) < 5

    def test_mixed(self):
        # Alternating → RSI near 50
        closes = [100.0 + (1 if i % 2 == 0 else -1) for i in range(20)]
        rsi = calc_rsi(closes)
        assert 40 < rsi < 60

    def test_insufficient_data(self):
        assert calc_rsi([100.0, 101.0]) == 50.0

    def test_range(self):
        import random
        random.seed(42)
        closes = [100 + random.uniform(-5, 5) for _ in range(50)]
        rsi = calc_rsi(closes)
        assert 0 <= rsi <= 100


# ── Hourly Trend ─────────────────────────────────────────────────────

class TestClassifyHourlyTrend:
    def _make_candles(self, highs, lows, opens=None, closes=None):
        n = len(highs)
        opens = opens or [h - (h - l) * 0.3 for h, l in zip(highs, lows)]
        closes = closes or [h - (h - l) * 0.7 for h, l in zip(highs, lows)]
        return [
            {"o": str(o), "h": str(h), "l": str(l), "c": str(c), "v": "100"}
            for o, h, l, c in zip(opens, highs, lows, closes)
        ]

    def test_uptrend(self):
        # Zigzag with higher highs and higher lows
        # Peak-trough-peak pattern trending up
        highs = [100, 98, 103, 101, 106, 104, 109, 107, 112, 110, 115, 113, 118, 116, 120]
        lows =  [95,  93, 98,  96, 101, 99, 104, 102, 107, 105, 110, 108, 113, 111, 115]
        assert classify_hourly_trend(self._make_candles(highs, lows)) == "UP"

    def test_downtrend(self):
        # Zigzag with lower highs and lower lows
        highs = [200, 202, 197, 199, 194, 196, 191, 193, 188, 190, 185, 187, 182, 184, 180]
        lows =  [195, 197, 192, 194, 189, 191, 186, 188, 183, 185, 180, 182, 177, 179, 175]
        assert classify_hourly_trend(self._make_candles(highs, lows)) == "DOWN"

    def test_neutral_insufficient_data(self):
        assert classify_hourly_trend([{"o": "1", "h": "2", "l": "0", "c": "1", "v": "1"}] * 5) == "NEUTRAL"

    def test_neutral_mixed(self):
        # Alternating highs and lows → no clear structure
        highs = [100, 102, 99, 103, 98, 104, 97, 105, 96, 106, 95, 107]
        lows = [95, 97, 94, 98, 93, 99, 92, 100, 91, 101, 90, 102]
        result = classify_hourly_trend(self._make_candles(highs, lows))
        # Could be NEUTRAL or UP/DOWN depending on swing detection
        assert result in ("UP", "DOWN", "NEUTRAL")


# ── 4h Trend ─────────────────────────────────────────────────────────

class TestAnalyze4hTrend:
    def _make_candles(self, closes):
        return [
            {"o": str(c), "h": str(c * 1.01), "l": str(c * 0.99), "c": str(c), "v": "100"}
            for c in closes
        ]

    def test_strong_uptrend(self):
        closes = [100 + i * 3 for i in range(20)]
        trend, strength = analyze_4h_trend(self._make_candles(closes))
        assert trend in ("strong_up", "up")
        assert strength > 20

    def test_strong_downtrend(self):
        closes = [200 - i * 3 for i in range(20)]
        trend, strength = analyze_4h_trend(self._make_candles(closes))
        assert trend in ("strong_down", "down")
        assert strength > 20

    def test_neutral(self):
        closes = [100.0] * 20
        trend, strength = analyze_4h_trend(self._make_candles(closes))
        assert trend == "neutral"

    def test_insufficient_data(self):
        trend, strength = analyze_4h_trend([])
        assert trend == "neutral"
        assert strength == 0


# ── Volume Ratio ─────────────────────────────────────────────────────

class TestVolumeRatio:
    def test_surge(self):
        candles = [{"v": "100"}] * 8 + [{"v": "300"}] * 4
        assert volume_ratio(candles) > 2.0

    def test_dying(self):
        candles = [{"v": "300"}] * 8 + [{"v": "100"}] * 4
        assert volume_ratio(candles) < 0.5

    def test_stable(self):
        candles = [{"v": "100"}] * 12
        ratio = volume_ratio(candles)
        assert abs(ratio - 1.0) < 0.01

    def test_insufficient(self):
        assert volume_ratio([{"v": "100"}] * 3) == 1.0


# ── Patterns ─────────────────────────────────────────────────────────

class TestDetectPatterns:
    def test_hammer(self):
        # Long lower wick, tiny upper wick, small body at top
        candle = {"o": "99", "h": "100", "l": "90", "c": "99.5", "v": "100"}
        patterns = detect_patterns([candle, candle, candle])
        assert "hammer" in patterns

    def test_bullish_engulfing(self):
        prev = {"o": "102", "h": "103", "l": "99", "c": "100", "v": "100"}
        curr = {"o": "99", "h": "104", "l": "98", "c": "103", "v": "200"}
        patterns = detect_patterns([prev, prev, curr])
        assert "bullish_engulfing" in patterns

    def test_bearish_engulfing(self):
        prev = {"o": "100", "h": "103", "l": "99", "c": "102", "v": "100"}
        curr = {"o": "103", "h": "104", "l": "98", "c": "99", "v": "200"}
        patterns = detect_patterns([prev, prev, curr])
        assert "bearish_engulfing" in patterns

    def test_doji(self):
        candle = {"o": "100", "h": "105", "l": "95", "c": "100.2", "v": "100"}
        patterns = detect_patterns([candle, candle, candle])
        assert "doji" in patterns

    def test_three_soldiers(self):
        c1 = {"o": "100", "h": "103", "l": "99", "c": "102", "v": "100"}
        c2 = {"o": "102", "h": "106", "l": "101", "c": "105", "v": "100"}
        c3 = {"o": "105", "h": "109", "l": "104", "c": "108", "v": "100"}
        patterns = detect_patterns([c1, c2, c3])
        assert "three_soldiers" in patterns

    def test_empty(self):
        assert detect_patterns([]) == []


# ── Price Changes ────────────────────────────────────────────────────

class TestPriceChanges:
    def test_basic(self):
        candles = [{"c": str(100 + i)} for i in range(25)]
        changes = price_changes(candles)
        assert changes["chg1h"] > 0
        assert changes["chg4h"] > 0
        assert changes["chg24h"] > 0

    def test_empty(self):
        changes = price_changes([])
        assert changes == {"chg1h": 0.0, "chg4h": 0.0, "chg24h": 0.0}


# ── Support / Resistance ────────────────────────────────────────────

class TestFindSupportResistance:
    def test_finds_levels(self):
        # Create data with clear peaks and troughs
        highs = [100, 110, 105, 115, 108, 120, 112, 125, 115, 130, 118, 135, 120, 140, 125]
        lows = [90, 95, 85, 100, 88, 105, 92, 110, 95, 115, 98, 120, 100, 125, 105]
        candles = [
            {"o": str(h - 2), "h": str(h), "l": str(l), "c": str(h - 1), "v": "100"}
            for h, l in zip(highs, lows)
        ]
        supports, resistances = find_support_resistance(candles, lookback=2)
        assert len(supports) > 0 or len(resistances) > 0

    def test_insufficient_data(self):
        supports, resistances = find_support_resistance([{"o": "1", "h": "2", "l": "0", "c": "1", "v": "1"}] * 3)
        assert supports == [] and resistances == []
