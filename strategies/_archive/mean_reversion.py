"""Mean reversion strategy — trade when price deviates from SMA."""
from __future__ import annotations

from collections import deque
from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class MeanReversionStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str = "mean_reversion",
        window: int = 20,
        threshold_bps: float = 15.0,           # lowered from 30 for thin YEX markets
        size: float = 1.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.window = window
        self.threshold_bps = threshold_bps
        self.size = size
        self.prices: deque = deque(maxlen=window)

    def on_tick(self, snapshot: MarketSnapshot,
                context: Optional[StrategyContext] = None) -> List[StrategyDecision]:
        self.prices.append(snapshot.mid_price)

        if len(self.prices) < self.window:
            return []

        sma = sum(self.prices) / len(self.prices)
        deviation_bps = (snapshot.mid_price - sma) / sma * 10_000

        if deviation_bps > self.threshold_bps:
            return [StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="sell",
                size=self.size,
                limit_price=round(snapshot.mid_price, 2),
                order_type="Ioc",
                meta={"signal": "overbought", "deviation_bps": round(deviation_bps, 2)},
            )]
        elif deviation_bps < -self.threshold_bps:
            return [StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="buy",
                size=self.size,
                limit_price=round(snapshot.mid_price, 2),
                order_type="Ioc",
                meta={"signal": "oversold", "deviation_bps": round(deviation_bps, 2)},
            )]
        else:
            return []
