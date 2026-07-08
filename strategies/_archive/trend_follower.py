"""Trend follower strategy — EMA crossover with ADX strength filter.

Thesis: Simple trend following with proper risk management. Catch sustained
moves, avoid chop. ADX filter prevents whipsaw in ranging markets.

Entry: EMA cross + ADX > 25 confirms trend strength.
Exit:  Opposing EMA cross, ADX drops below 20, or ATR trailing stop.
"""
from __future__ import annotations

import math
from collections import deque
from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

# --- Parameters ---
EMA_FAST = 7
EMA_SLOW = 26
ADX_PERIOD = 14
ADX_ENTRY_THRESHOLD = 15    # lowered from 25 for thin YEX markets
ADX_EXIT_THRESHOLD = 12     # lowered from 20 for thin YEX markets
ATR_LOOKBACK = 24
ATR_STOP_MULT = 4.5
# Need enough history for ADX (2 * ADX_PERIOD + some buffer) and EMA
MIN_HISTORY = max(2 * ADX_PERIOD + 5, EMA_SLOW + 10, ATR_LOOKBACK) + 1


def _ema(values: list, span: int) -> list:
    alpha = 2.0 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _calc_adx(highs: list, lows: list, closes: list, period: int) -> float:
    """Calculate ADX (Average Directional Index)."""
    n = len(closes)
    if n < 2 * period + 2:
        return 0.0

    # True Range, +DM, -DM
    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, n):
        h, l, prev_c = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return 0.0

    # Smoothed TR, +DM, -DM (Wilder's smoothing)
    atr = sum(tr_list[:period])
    plus_dm_smooth = sum(plus_dm_list[:period])
    minus_dm_smooth = sum(minus_dm_list[:period])

    dx_values = []

    for i in range(period, len(tr_list)):
        atr = atr - atr / period + tr_list[i]
        plus_dm_smooth = plus_dm_smooth - plus_dm_smooth / period + plus_dm_list[i]
        minus_dm_smooth = minus_dm_smooth - minus_dm_smooth / period + minus_dm_list[i]

        if atr > 0:
            plus_di = 100 * plus_dm_smooth / atr
            minus_di = 100 * minus_dm_smooth / atr
            di_sum = plus_di + minus_di
            dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
            dx_values.append(dx)

    if len(dx_values) < period:
        return 0.0

    # ADX = smoothed average of DX
    adx = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period

    return adx


def _calc_atr(highs: list, lows: list, closes: list, lookback: int) -> Optional[float]:
    if len(closes) < lookback + 1:
        return None
    trs = []
    for i in range(-lookback, 0):
        h, l, prev_c = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs)


class TrendFollowerStrategy(BaseStrategy):
    """EMA crossover + ADX trend strength filter."""

    def __init__(
        self,
        strategy_id: str = "trend_follower",
        size: float = 1.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.size = size

        buf_len = MIN_HISTORY + 5
        self.closes: deque = deque(maxlen=buf_len)
        self.highs: deque = deque(maxlen=buf_len)
        self.lows: deque = deque(maxlen=buf_len)

        self.direction: int = 0
        self.entry_price: float = 0.0
        self.peak_price: float = 0.0
        self.atr_at_entry: float = 0.0
        self.prev_ema_cross: int = 0  # 1 = fast above slow, -1 = below, 0 = unknown

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

        if len(self.closes) < MIN_HISTORY:
            return []

        closes = list(self.closes)
        highs = list(self.highs)
        lows = list(self.lows)

        # EMA crossover
        ema_segment = closes[-(EMA_SLOW + 10):]
        ema_f = _ema(ema_segment, EMA_FAST)
        ema_s = _ema(ema_segment, EMA_SLOW)
        current_cross = 1 if ema_f[-1] > ema_s[-1] else -1

        # Detect actual crossover (state change)
        crossover_up = current_cross == 1 and self.prev_ema_cross == -1
        crossover_down = current_cross == -1 and self.prev_ema_cross == 1
        self.prev_ema_cross = current_cross

        # ADX
        adx = _calc_adx(highs, lows, closes, ADX_PERIOD)

        ctx = context or StrategyContext()
        orders: List[StrategyDecision] = []

        # Sync direction
        if ctx.position_qty > 0:
            self.direction = 1
        elif ctx.position_qty < 0:
            self.direction = -1
        elif self.direction != 0 and ctx.position_qty == 0:
            self.direction = 0

        signal_meta = {
            "adx": round(adx, 1),
            "ema_fast": round(ema_f[-1], 4),
            "ema_slow": round(ema_s[-1], 4),
            "ema_cross": current_cross,
        }

        if self.direction == 0:
            # Entry: EMA crossover + ADX confirms trend
            if crossover_up and adx > ADX_ENTRY_THRESHOLD:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="buy",
                    size=self.size,
                    limit_price=round(snapshot.ask, 8),
                    order_type="Ioc",
                    meta={**signal_meta, "signal": "trend_long"},
                ))
                self.direction = 1
                self.entry_price = mid
                self.peak_price = mid
                atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK)
                self.atr_at_entry = atr if atr else mid * 0.02

            elif crossover_down and adx > ADX_ENTRY_THRESHOLD:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="sell",
                    size=self.size,
                    limit_price=round(snapshot.bid, 8),
                    order_type="Ioc",
                    meta={**signal_meta, "signal": "trend_short"},
                ))
                self.direction = -1
                self.entry_price = mid
                self.peak_price = mid
                atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK)
                self.atr_at_entry = atr if atr else mid * 0.02
        else:
            # Exit logic
            atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK) or self.atr_at_entry
            exit_signal = None

            # 1. Opposing EMA cross
            if self.direction == 1 and crossover_down:
                exit_signal = "ema_cross_exit"
            elif self.direction == -1 and crossover_up:
                exit_signal = "ema_cross_exit"

            # 2. ADX drops below threshold (trend dying)
            if not exit_signal and adx < ADX_EXIT_THRESHOLD:
                exit_signal = "adx_weak"

            # 3. ATR trailing stop
            if not exit_signal:
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

            if exit_signal:
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

        return orders
