"""OI divergence strategy — filter real moves from fakeouts via price/OI correlation.

Thesis: Price + OI agreement = strong move. Price + OI divergence = weak/fake move.

Long:  price up + OI up + volume above average (genuine demand)
Short: price down + OI down + volume above average (genuine selling)
No entry on divergence (price up + OI down = squeeze, unreliable).
"""
from __future__ import annotations

import math
from collections import deque
from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

# --- Parameters ---
LOOKBACK = 24               # ticks for momentum/OI change
VOLUME_AVG_WINDOW = 36      # ticks for average volume
VOLUME_SURGE_MULT = 1.3     # volume must exceed avg * this
MOM_THRESHOLD = 0.008       # min price return for entry signal
OI_CHANGE_THRESHOLD = 0.005 # min OI change rate for entry signal
RSI_PERIOD = 14
RSI_EXIT_HIGH = 75
RSI_EXIT_LOW = 25
ATR_LOOKBACK = 24
ATR_STOP_MULT = 4.5
MIN_HISTORY = max(LOOKBACK, VOLUME_AVG_WINDOW, RSI_PERIOD + 1, ATR_LOOKBACK) + 1


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


def _calc_atr(highs: list, lows: list, closes: list, lookback: int) -> Optional[float]:
    if len(closes) < lookback + 1:
        return None
    trs = []
    for i in range(-lookback, 0):
        h, l, prev_c = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs)


class OIDivergenceStrategy(BaseStrategy):
    """Enter on price/OI agreement, exit on divergence or RSI extreme."""

    def __init__(
        self,
        strategy_id: str = "oi_divergence",
        size: float = 1.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.size = size

        buf_len = MIN_HISTORY + 5
        self.closes: deque = deque(maxlen=buf_len)
        self.highs: deque = deque(maxlen=buf_len)
        self.lows: deque = deque(maxlen=buf_len)
        self.oi_values: deque = deque(maxlen=buf_len)
        self.volumes: deque = deque(maxlen=VOLUME_AVG_WINDOW)

        self.direction: int = 0
        self.entry_price: float = 0.0
        self.peak_price: float = 0.0
        self.atr_at_entry: float = 0.0
        self.entry_oi_direction: int = 0  # track OI direction at entry for divergence exit

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
        oi = snapshot.open_interest
        vol = snapshot.volume_24h

        self.closes.append(mid)
        self.highs.append(high)
        self.lows.append(low)
        self.oi_values.append(oi)
        self.volumes.append(vol)

        if len(self.closes) < MIN_HISTORY:
            return []

        closes = list(self.closes)
        highs = list(self.highs)
        lows = list(self.lows)
        oi_list = list(self.oi_values)
        vol_list = list(self.volumes)

        # Price momentum
        price_return = (closes[-1] - closes[-LOOKBACK]) / closes[-LOOKBACK] if closes[-LOOKBACK] > 0 else 0
        price_up = price_return > MOM_THRESHOLD
        price_down = price_return < -MOM_THRESHOLD

        # OI change
        oi_old = oi_list[-LOOKBACK] if len(oi_list) >= LOOKBACK and oi_list[-LOOKBACK] > 0 else 0
        oi_change = (oi_list[-1] - oi_old) / oi_old if oi_old > 0 else 0
        oi_up = oi_change > OI_CHANGE_THRESHOLD
        oi_down = oi_change < -OI_CHANGE_THRESHOLD

        # Volume confirmation
        avg_vol = sum(vol_list) / len(vol_list) if vol_list else 1
        vol_above_avg = vol > avg_vol * VOLUME_SURGE_MULT if avg_vol > 0 else False

        # RSI
        rsi = _rsi(closes, RSI_PERIOD)

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
            "price_return": round(price_return, 4),
            "oi_change": round(oi_change, 4),
            "vol_above_avg": vol_above_avg,
            "rsi": round(rsi, 1),
        }

        if self.direction == 0:
            # Entry: agreement + volume
            if price_up and oi_up and vol_above_avg:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="buy",
                    size=self.size,
                    limit_price=round(snapshot.ask, 8),
                    order_type="Ioc",
                    meta={**signal_meta, "signal": "oi_agreement_long"},
                ))
                self.direction = 1
                self.entry_price = mid
                self.peak_price = mid
                self.entry_oi_direction = 1
                atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK)
                self.atr_at_entry = atr if atr else mid * 0.02

            elif price_down and oi_down and vol_above_avg:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="sell",
                    size=self.size,
                    limit_price=round(snapshot.bid, 8),
                    order_type="Ioc",
                    meta={**signal_meta, "signal": "oi_agreement_short"},
                ))
                self.direction = -1
                self.entry_price = mid
                self.peak_price = mid
                self.entry_oi_direction = -1
                atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK)
                self.atr_at_entry = atr if atr else mid * 0.02
        else:
            # Exit logic
            atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK) or self.atr_at_entry
            exit_signal = None

            # 1. OI divergence (OI reverses while in position)
            if self.direction == 1 and oi_down:
                exit_signal = "oi_divergence"
            elif self.direction == -1 and oi_up:
                exit_signal = "oi_divergence"

            # 2. RSI extreme
            if not exit_signal:
                if self.direction == 1 and rsi > RSI_EXIT_HIGH:
                    exit_signal = "rsi_overbought"
                elif self.direction == -1 and rsi < RSI_EXIT_LOW:
                    exit_signal = "rsi_oversold"

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
