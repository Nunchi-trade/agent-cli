"""Simple market-making strategy — symmetric bid/ask quoting around mid.

Produces a buy and sell order each tick, placed symmetrically around
the current mid price at a configurable spread.
"""
from __future__ import annotations

from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class SimpleMMStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str = "simple_mm",
        spread_bps: float = 10.0,
        size: float = 1.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.spread_bps = spread_bps
        self.size = size

    def on_tick(self, snapshot: MarketSnapshot,
                context: Optional[StrategyContext] = None) -> List[StrategyDecision]:
        if snapshot.mid_price <= 0:
            return []

        half_spread = snapshot.mid_price * (self.spread_bps / 10_000) / 2
        bid = round(snapshot.mid_price - half_spread, 2)
        ask = round(snapshot.mid_price + half_spread, 2)

        return [
            StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="buy",
                size=self.size,
                limit_price=bid,
            ),
            StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="sell",
                size=self.size,
                limit_price=ask,
            ),
        ]
