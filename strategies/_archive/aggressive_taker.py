"""Aggressive taker strategy — crosses the spread to generate fills.

Places directionally imbalanced orders each tick (alternating between
buy-heavy and sell-heavy), forcing the clearing price away from mid
and engaging MM liquidity on the opposing side.

Without imbalance, equal buy/sell from the taker self-matches at mid and
MMs never participate.  With imbalance, the surplus-maximising clearing
price shifts toward the heavy side, bringing MM orders into eligibility.
"""
from __future__ import annotations

import math
from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class AggressiveTaker(BaseStrategy):
    """Crosses the spread with directional bias to take MM liquidity."""

    def __init__(
        self,
        strategy_id: str = "aggressive_taker",
        size: float = 2.0,
        skip_ticks: int = 0,
        bias_amplitude: float = 0.35,
        bias_period: int = 4,
    ):
        super().__init__(strategy_id=strategy_id)
        self.size = size
        self.skip_ticks = skip_ticks
        self.bias_amplitude = bias_amplitude  # max fraction shift per side
        self.bias_period = bias_period  # rounds per full cycle
        self._tick_count = 0

    def on_tick(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext] = None,
    ) -> List[StrategyDecision]:
        if snapshot.mid_price <= 0:
            return []

        self._tick_count += 1
        if self.skip_ticks > 0 and self._tick_count % (self.skip_ticks + 1) != 0:
            return []

        # Sinusoidal directional bias: positive → buy-heavy, negative → sell-heavy
        phase = 2.0 * math.pi * self._tick_count / self.bias_period
        bias = self.bias_amplitude * math.sin(phase)

        buy_frac = 0.5 + bias   # e.g. 0.85 when buy-heavy
        sell_frac = 0.5 - bias  # e.g. 0.15 when buy-heavy

        buy_size = round(max(0.01, self.size * buy_frac), 4)
        sell_size = round(max(0.01, self.size * sell_frac), 4)

        return [
            StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="buy",
                size=buy_size,
                limit_price=round(snapshot.ask + 3.0, 2),  # well above ask → crosses all MMs
                order_type="Ioc",
                meta={"signal": "aggressive_buy", "bias": round(bias, 3)},
            ),
            StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="sell",
                size=sell_size,
                limit_price=round(snapshot.bid - 3.0, 2),  # well below bid → crosses all MMs
                order_type="Ioc",
                meta={"signal": "aggressive_sell", "bias": round(bias, 3)},
            ),
        ]
