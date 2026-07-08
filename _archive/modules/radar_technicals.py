"""Pure technical analysis functions for the opportunity radar.

Zero I/O — all functions take raw data and return computed values.
"""
from __future__ import annotations

from typing import Dict, List, Tuple


def calc_ema(closes: List[float], period: int) -> List[float]:
    """Exponential moving average. Returns list same length as closes."""
    if not closes or period <= 0:
        return []
    k = 2.0 / (period + 1)
    ema = [closes[0]]
    for i in range(1, len(closes)):
        ema.append(closes[i] * k + ema[-1] * (1 - k))
    return ema


def calc_rsi(closes: List[float], period: int = 14) -> float:
    """Relative Strength Index (0-100). Returns 50.0 if insufficient data."""
    if len(closes) < period + 1:
        return 50.0

    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    if len(gains) < period:
        return 50.0

    # Initial average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed (Wilder's method)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def classify_hourly_trend(candles_1h: List[Dict]) -> str:
    """Classify trend from 1h candles using swing high/low structure.

    Returns "UP", "DOWN", or "NEUTRAL".
    """
    if len(candles_1h) < 10:
        return "NEUTRAL"

    highs = [float(c["h"]) for c in candles_1h]
    lows = [float(c["l"]) for c in candles_1h]

    # Find swing points (local extremes in 3-bar windows)
    swing_highs = []
    swing_lows = []
    for i in range(1, len(highs) - 1):
        if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]:
            swing_highs.append(highs[i])
        if lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1]:
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL"

    # Check last two swings
    hh = swing_highs[-1] > swing_highs[-2]  # higher high
    hl = swing_lows[-1] > swing_lows[-2]     # higher low
    lh = swing_highs[-1] < swing_highs[-2]   # lower high
    ll = swing_lows[-1] < swing_lows[-2]     # lower low

    if hh and hl:
        return "UP"
    elif lh and ll:
        return "DOWN"
    return "NEUTRAL"


def analyze_4h_trend(candles_4h: List[Dict]) -> Tuple[str, int]:
    """Analyze 4h trend using EMA 5/13 crossover.

    Returns (trend_label, strength_0_100).
    trend_label: "strong_up", "up", "neutral", "down", "strong_down"
    """
    if len(candles_4h) < 14:
        return "neutral", 0

    closes = [float(c["c"]) for c in candles_4h]
    ema5 = calc_ema(closes, 5)
    ema13 = calc_ema(closes, 13)

    if not ema5 or not ema13:
        return "neutral", 0

    # Percentage difference between fast and slow EMA
    diff_pct = (ema5[-1] - ema13[-1]) / ema13[-1] * 100

    # Trend direction based on consecutive alignment
    aligned_bars = 0
    for i in range(len(ema5) - 1, max(len(ema5) - 10, -1), -1):
        if diff_pct > 0 and ema5[i] > ema13[i]:
            aligned_bars += 1
        elif diff_pct < 0 and ema5[i] < ema13[i]:
            aligned_bars += 1
        else:
            break

    strength = min(int(abs(diff_pct) * 20 + aligned_bars * 5), 100)

    if diff_pct > 1.0:
        return "strong_up", strength
    elif diff_pct > 0.2:
        return "up", strength
    elif diff_pct < -1.0:
        return "strong_down", strength
    elif diff_pct < -0.2:
        return "down", strength
    return "neutral", strength


def volume_ratio(candles: List[Dict], recent_n: int = 4) -> float:
    """Ratio of recent volume vs prior volume. >1 = surge, <1 = dying."""
    if len(candles) < recent_n * 2:
        return 1.0

    volumes = [float(c["v"]) for c in candles]
    recent = sum(volumes[-recent_n:])
    prior = sum(volumes[-recent_n * 2:-recent_n])
    if prior == 0:
        return 1.0
    return recent / prior


def detect_patterns(candles: List[Dict]) -> List[str]:
    """Detect candlestick patterns from recent candles.

    Returns list of pattern names found.
    """
    if len(candles) < 3:
        return []

    patterns = []

    # Last candle
    c = candles[-1]
    o, h, l, cl = float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])
    body = abs(cl - o)
    total_range = h - l
    if total_range == 0:
        return []

    upper_wick = h - max(o, cl)
    lower_wick = min(o, cl) - l

    # Doji: very small body relative to range (check first — more specific patterns override)
    is_doji = body / total_range < 0.1

    # Hammer: long lower wick, small upper wick relative to range
    if lower_wick > total_range * 0.6 and upper_wick < total_range * 0.1 and body / total_range < 0.35:
        patterns.append("hammer")
    elif is_doji:
        patterns.append("doji")

    # Engulfing (need 2 candles)
    if len(candles) >= 2:
        prev = candles[-2]
        po, pcl = float(prev["o"]), float(prev["c"])
        # Bullish engulfing
        if pcl < po and cl > o and cl > po and o < pcl:
            patterns.append("bullish_engulfing")
        # Bearish engulfing
        if pcl > po and cl < o and cl < po and o > pcl:
            patterns.append("bearish_engulfing")

    # Three white soldiers / three black crows (need 3 candles)
    if len(candles) >= 3:
        c1, c2, c3 = candles[-3], candles[-2], candles[-1]
        c1o, c1c = float(c1["o"]), float(c1["c"])
        c2o, c2c = float(c2["o"]), float(c2["c"])
        c3o, c3c = float(c3["o"]), float(c3["c"])

        if c1c > c1o and c2c > c2o and c3c > c3o:
            if c2c > c1c and c3c > c2c:
                patterns.append("three_soldiers")
        if c1c < c1o and c2c < c2o and c3c < c3o:
            if c2c < c1c and c3c < c2c:
                patterns.append("three_crows")

    return patterns


def price_changes(candles_1h: List[Dict]) -> Dict[str, float]:
    """Compute price changes over 1h, 4h, 24h from 1h candles."""
    if not candles_1h:
        return {"chg1h": 0.0, "chg4h": 0.0, "chg24h": 0.0}

    current = float(candles_1h[-1]["c"])
    result = {}

    for label, bars in [("chg1h", 1), ("chg4h", 4), ("chg24h", 24)]:
        if len(candles_1h) > bars:
            prev = float(candles_1h[-bars - 1]["c"])
            result[label] = (current - prev) / prev * 100 if prev else 0.0
        else:
            result[label] = 0.0

    return result


def find_support_resistance(
    candles: List[Dict], lookback: int = 5,
) -> Tuple[List[float], List[float]]:
    """Find recent support and resistance levels from swing points.

    Returns (supports, resistances) — each sorted by recency (most recent first).
    """
    if len(candles) < lookback * 2:
        return [], []

    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]

    resistances = []
    supports = []

    for i in range(lookback, len(candles) - lookback):
        window_highs = highs[i - lookback:i + lookback + 1]
        window_lows = lows[i - lookback:i + lookback + 1]

        if highs[i] == max(window_highs):
            resistances.append(highs[i])
        if lows[i] == min(window_lows):
            supports.append(lows[i])

    return list(reversed(supports[-5:])), list(reversed(resistances[-5:]))
