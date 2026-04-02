"""Simplified ensemble strategy — ported from auto-research exp52 (score 13.5).

6-signal voting ensemble with 4/6 threshold. Adapted from batch OHLCV to
tick-by-tick MarketSnapshot by maintaining internal rolling buffers.

Signals: momentum, v-short momentum, EMA crossover, RSI, MACD histogram,
         BB width compression.
Exits:   ATR trailing stop, RSI overbought/oversold, signal flip.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

# --- Constants (from exp52 / auto-research) ---
SHORT_WINDOW = 6
MED_WINDOW = 12
EMA_FAST = 7
EMA_SLOW = 26
RSI_PERIOD = 8
RSI_BULL = 50
RSI_BEAR = 50
RSI_OVERBOUGHT = 69
RSI_OVERSOLD = 31
MACD_FAST = 14
MACD_SLOW = 23
MACD_SIGNAL = 9
BB_PERIOD = 7
BASE_THRESHOLD = 0.012
VOL_LOOKBACK = 36
TARGET_VOL = 0.015
ATR_LOOKBACK = 24
ATR_STOP_MULT = 5.5
BASE_POSITION_PCT = 0.08
MIN_VOTES = 4
COOLDOWN_BARS = 120  # 2 hours at 60s ticks — prevents fee churn on re-entry
# Minimum ticks needed before signals are valid
MIN_HISTORY = max(MACD_SLOW + MACD_SIGNAL + 5, EMA_SLOW + 10, VOL_LOOKBACK, ATR_LOOKBACK, BB_PERIOD * 3) + 1


def _ema(values: list, span: int) -> list:
    """Exponential moving average."""
    alpha = 2.0 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _rsi(closes: list, period: int) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(-period, 0)]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _macd_histogram(closes: list) -> float:
    n = MACD_SLOW + MACD_SIGNAL + 5
    if len(closes) < n:
        return 0.0
    segment = closes[-n:]
    fast = _ema(segment, MACD_FAST)
    slow = _ema(segment, MACD_SLOW)
    macd_line = [f - s for f, s in zip(fast, slow)]
    signal_line = _ema(macd_line, MACD_SIGNAL)
    return macd_line[-1] - signal_line[-1]


def _bb_width_percentile(closes: list, period: int) -> float:
    if len(closes) < period * 3:
        return 50.0
    widths = []
    for i in range(period * 2, len(closes)):
        window = closes[i - period:i]
        sma = sum(window) / period
        if sma <= 0:
            continue
        variance = sum((x - sma) ** 2 for x in window) / period
        std = math.sqrt(variance)
        widths.append(2 * std / sma)
    if len(widths) < 2:
        return 50.0
    current = widths[-1]
    below = sum(1 for w in widths if w <= current)
    return 100 * below / len(widths)


def _calc_atr(highs: list, lows: list, closes: list, lookback: int) -> Optional[float]:
    if len(closes) < lookback + 1:
        return None
    trs = []
    for i in range(-lookback, 0):
        h, l, prev_c = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs)


class SimplifiedEnsembleStrategy(BaseStrategy):
    """6-signal ensemble (4/6 vote), ATR trailing stop, RSI exit."""

    def __init__(
        self,
        strategy_id: str = "simplified_ensemble",
        size: float = 1.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.size = size

        # Rolling buffers — maxlen covers the longest lookback
        buf_len = MIN_HISTORY + 5
        self.closes: deque = deque(maxlen=buf_len)
        self.highs: deque = deque(maxlen=buf_len)
        self.lows: deque = deque(maxlen=buf_len)

        # Position tracking
        self.direction: int = 0  # 1 = long, -1 = short, 0 = flat
        self.entry_price: float = 0.0
        self.peak_price: float = 0.0
        self.atr_at_entry: float = 0.0
        self.tick_count: int = 0
        self.exit_tick: int = -999

    def on_tick(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext] = None,
    ) -> List[StrategyDecision]:
        mid = snapshot.mid_price
        if mid <= 0:
            return []

        high = snapshot.ask if snapshot.ask > 0 else mid
        low = snapshot.bid if snapshot.bid > 0 else mid

        self.closes.append(mid)
        self.highs.append(high)
        self.lows.append(low)
        self.tick_count += 1

        if len(self.closes) < MIN_HISTORY:
            return []

        closes = list(self.closes)
        highs = list(self.highs)
        lows = list(self.lows)

        # --- Compute indicators ---
        # Volatility-adjusted threshold
        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(-VOL_LOOKBACK + 1, 0)]
        mean_r = sum(log_rets) / len(log_rets)
        realized_vol = max(math.sqrt(sum((r - mean_r) ** 2 for r in log_rets) / len(log_rets)), 1e-6)
        vol_ratio = realized_vol / TARGET_VOL
        dyn_threshold = max(0.005, min(0.020, BASE_THRESHOLD * (0.3 + vol_ratio * 0.7)))

        # Returns
        ret_short = (closes[-1] - closes[-MED_WINDOW]) / closes[-MED_WINDOW]
        ret_vshort = (closes[-1] - closes[-SHORT_WINDOW]) / closes[-SHORT_WINDOW]

        # Signal 1: Momentum
        mom_bull = ret_short > dyn_threshold
        mom_bear = ret_short < -dyn_threshold

        # Signal 2: V-short momentum
        vshort_bull = ret_vshort > dyn_threshold * 0.7
        vshort_bear = ret_vshort < -dyn_threshold * 0.7

        # Signal 3: EMA crossover
        ema_segment = closes[-(EMA_SLOW + 10):]
        ema_f = _ema(ema_segment, EMA_FAST)
        ema_s = _ema(ema_segment, EMA_SLOW)
        ema_bull = ema_f[-1] > ema_s[-1]
        ema_bear = ema_f[-1] < ema_s[-1]

        # Signal 4: RSI
        rsi = _rsi(closes, RSI_PERIOD)
        rsi_bull = rsi > RSI_BULL
        rsi_bear = rsi < RSI_BEAR

        # Signal 5: MACD histogram
        macd_hist = _macd_histogram(closes)
        macd_bull = macd_hist > 0
        macd_bear = macd_hist < 0

        # Signal 6: BB compression
        bb_pctile = _bb_width_percentile(closes, BB_PERIOD)
        bb_compressed = bb_pctile < 90

        # --- Voting ---
        bull_votes = sum([mom_bull, vshort_bull, ema_bull, rsi_bull, macd_bull, bb_compressed])
        bear_votes = sum([mom_bear, vshort_bear, ema_bear, rsi_bear, macd_bear, bb_compressed])
        bullish = bull_votes >= MIN_VOTES
        bearish = bear_votes >= MIN_VOTES

        in_cooldown = (self.tick_count - self.exit_tick) < COOLDOWN_BARS

        ctx = context or StrategyContext()
        orders: List[StrategyDecision] = []

        # Sync internal direction with context
        if ctx.position_qty > 0:
            self.direction = 1
        elif ctx.position_qty < 0:
            self.direction = -1
        elif self.direction != 0 and ctx.position_qty == 0:
            # Position was closed externally
            self.direction = 0

        signal_meta = {
            "bull_votes": bull_votes,
            "bear_votes": bear_votes,
            "rsi": round(rsi, 1),
            "macd_hist": round(macd_hist, 6),
            "bb_pctile": round(bb_pctile, 1),
            "dyn_threshold": round(dyn_threshold, 4),
        }

        if self.direction == 0:
            # --- Entry ---
            if not in_cooldown:
                if bullish:
                    orders.append(StrategyDecision(
                        action="place_order",
                        instrument=snapshot.instrument,
                        side="buy",
                        size=self.size,
                        limit_price=round(snapshot.ask, 8),
                        order_type="Ioc",
                        meta={**signal_meta, "signal": "ensemble_long"},
                    ))
                    self.direction = 1
                    self.entry_price = mid
                    self.peak_price = mid
                    atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK)
                    self.atr_at_entry = atr if atr else mid * 0.02
                elif bearish:
                    orders.append(StrategyDecision(
                        action="place_order",
                        instrument=snapshot.instrument,
                        side="sell",
                        size=self.size,
                        limit_price=round(snapshot.bid, 8),
                        order_type="Ioc",
                        meta={**signal_meta, "signal": "ensemble_short"},
                    ))
                    self.direction = -1
                    self.entry_price = mid
                    self.peak_price = mid
                    atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK)
                    self.atr_at_entry = atr if atr else mid * 0.02
        else:
            # --- Exit logic (priority order) ---
            atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK) or self.atr_at_entry
            exit_signal = None

            # 1. ATR trailing stop
            if self.direction == 1:
                self.peak_price = max(self.peak_price, mid)
                stop = self.peak_price - ATR_STOP_MULT * atr
                if mid < stop:
                    exit_signal = "atr_trailing_stop"
            else:
                self.peak_price = min(self.peak_price, mid)
                stop = self.peak_price + ATR_STOP_MULT * atr
                if mid > stop:
                    exit_signal = "atr_trailing_stop"

            # 2. RSI overbought/oversold
            if not exit_signal:
                if self.direction == 1 and rsi > RSI_OVERBOUGHT:
                    exit_signal = "rsi_overbought"
                elif self.direction == -1 and rsi < RSI_OVERSOLD:
                    exit_signal = "rsi_oversold"

            # 3. Signal flip — exit and reverse
            flip_signal = None
            if not exit_signal and not in_cooldown:
                if self.direction == 1 and bearish:
                    exit_signal = "signal_flip"
                    flip_signal = "sell"
                elif self.direction == -1 and bullish:
                    exit_signal = "signal_flip"
                    flip_signal = "buy"

            if exit_signal:
                # Close current position
                close_side = "sell" if self.direction == 1 else "buy"
                close_price = snapshot.bid if self.direction == 1 else snapshot.ask
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side=close_side,
                    size=abs(ctx.position_qty) if ctx.position_qty != 0 else self.size,
                    limit_price=round(close_price, 8),
                    order_type="Ioc",
                    meta={**signal_meta, "signal": exit_signal},
                ))
                self.direction = 0
                self.exit_tick = self.tick_count

                # Enter opposite on signal flip
                if flip_signal:
                    flip_price = snapshot.ask if flip_signal == "buy" else snapshot.bid
                    orders.append(StrategyDecision(
                        action="place_order",
                        instrument=snapshot.instrument,
                        side=flip_signal,
                        size=self.size,
                        limit_price=round(flip_price, 8),
                        order_type="Ioc",
                        meta={**signal_meta, "signal": f"ensemble_{'long' if flip_signal == 'buy' else 'short'}"},
                    ))
                    self.direction = 1 if flip_signal == "buy" else -1
                    self.entry_price = mid
                    self.peak_price = mid
                    new_atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK)
                    self.atr_at_entry = new_atr if new_atr else mid * 0.02

        return orders
