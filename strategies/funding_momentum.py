"""Funding momentum strategy — trade funding rate extremes with mean-reversion.

Thesis: Extreme funding rates predict mean-reversion. When funding is very
negative (shorts paying), go long and collect funding. Vice versa.

Uses a z-score of funding rate over a rolling window, confirmed by EMA direction.
"""
from __future__ import annotations

import math
from collections import deque
from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

# --- Parameters ---
FUNDING_LOOKBACK = 48       # ticks for rolling funding stats
EMA_FAST = 12
EMA_SLOW = 26
ZSCORE_ENTRY = 1.5          # lowered from 2.0 for thin YEX markets
ZSCORE_EXIT = 0.8           # lowered from 1.0 for thin YEX markets
ATR_LOOKBACK = 24
ATR_STOP_MULT = 4.0
MIN_HISTORY = max(FUNDING_LOOKBACK, EMA_SLOW + 10, ATR_LOOKBACK) + 1


def _ema(values: list, span: int) -> list:
    alpha = 2.0 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _calc_atr(highs: list, lows: list, closes: list, lookback: int) -> Optional[float]:
    if len(closes) < lookback + 1:
        return None
    trs = []
    for i in range(-lookback, 0):
        h, l, prev_c = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs)


class FundingMomentumStrategy(BaseStrategy):
    """Mean-revert on extreme funding rates, confirmed by EMA trend."""

    def __init__(
        self,
        strategy_id: str = "funding_momentum",
        size: float = 1.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.size = size

        buf_len = MIN_HISTORY + 5
        self.closes: deque = deque(maxlen=buf_len)
        self.highs: deque = deque(maxlen=buf_len)
        self.lows: deque = deque(maxlen=buf_len)
        self.funding_rates: deque = deque(maxlen=FUNDING_LOOKBACK)

        self.direction: int = 0
        self.entry_price: float = 0.0
        self.peak_price: float = 0.0
        self.atr_at_entry: float = 0.0

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
        self.funding_rates.append(snapshot.funding_rate)

        if len(self.closes) < MIN_HISTORY or len(self.funding_rates) < FUNDING_LOOKBACK:
            return []

        closes = list(self.closes)
        highs = list(self.highs)
        lows = list(self.lows)
        rates = list(self.funding_rates)

        # Funding z-score
        mean_fr = sum(rates) / len(rates)
        variance = sum((r - mean_fr) ** 2 for r in rates) / len(rates)
        std_fr = max(math.sqrt(variance), 1e-10)
        zscore = (rates[-1] - mean_fr) / std_fr

        # EMA confirmation (trend direction)
        ema_segment = closes[-(EMA_SLOW + 10):]
        ema_f = _ema(ema_segment, EMA_FAST)
        ema_s = _ema(ema_segment, EMA_SLOW)
        ema_bullish = ema_f[-1] > ema_s[-1]
        ema_bearish = ema_f[-1] < ema_s[-1]

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
            "funding_zscore": round(zscore, 2),
            "funding_rate": round(rates[-1], 6),
            "ema_bullish": ema_bullish,
        }

        if self.direction == 0:
            # Entry: extreme funding + EMA confirmation
            # Very negative funding (shorts paying) → go long (collect funding + mean revert)
            if zscore < -ZSCORE_ENTRY and ema_bullish:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="buy",
                    size=self.size,
                    limit_price=round(snapshot.ask, 8),
                    order_type="Ioc",
                    meta={**signal_meta, "signal": "funding_long"},
                ))
                self.direction = 1
                self.entry_price = mid
                self.peak_price = mid
                atr = _calc_atr(highs, lows, closes, ATR_LOOKBACK)
                self.atr_at_entry = atr if atr else mid * 0.02

            # Very positive funding (longs paying) → go short (collect funding + mean revert)
            elif zscore > ZSCORE_ENTRY and ema_bearish:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="sell",
                    size=self.size,
                    limit_price=round(snapshot.bid, 8),
                    order_type="Ioc",
                    meta={**signal_meta, "signal": "funding_short"},
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

            # 1. Funding normalized (z-score returned toward mean)
            if self.direction == 1 and zscore > -ZSCORE_EXIT:
                exit_signal = "funding_normalized"
            elif self.direction == -1 and zscore < ZSCORE_EXIT:
                exit_signal = "funding_normalized"

            # 2. ATR trailing stop
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
