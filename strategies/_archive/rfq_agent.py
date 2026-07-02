"""RFQ Agent — block-size liquidity for Dark RFQ flow.

Provides wider-spread, larger-size liquidity for block trades.
KorAI spec Section 4.2.2, Flow 2 (Month 2).
"""
from __future__ import annotations

from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class RFQAgent(BaseStrategy):
    def __init__(self, strategy_id="rfq_agent",
                 min_size: float = 0.5,
                 spread_bps: float = 15.0,
                 max_position: float = 15.0):
        super().__init__(strategy_id=strategy_id)
        self.min_size = min_size
        self.spread_bps = spread_bps
        self.max_position = max_position

    def on_tick(self, snapshot: MarketSnapshot,
                context: Optional[StrategyContext] = None) -> List[StrategyDecision]:
        mid = snapshot.mid_price
        if mid <= 0:
            return []

        q = context.position_qty if context else 0.0
        reduce_only = context.reduce_only if context else False
        remaining = self.max_position - abs(q)
        if remaining < self.min_size:
            return []

        half_spread = mid * (self.spread_bps / 10_000) / 2
        size = min(self.min_size, remaining)
        orders = []

        if not reduce_only or q < 0:
            orders.append(StrategyDecision(
                action="place_order", instrument=snapshot.instrument,
                side="buy", size=size,
                limit_price=round(mid - half_spread, 2),
                meta={"signal": "rfq_bid", "capacity": remaining},
            ))
        if not reduce_only or q > 0:
            orders.append(StrategyDecision(
                action="place_order", instrument=snapshot.instrument,
                side="sell", size=size,
                limit_price=round(mid + half_spread, 2),
                meta={"signal": "rfq_ask", "capacity": remaining},
            ))
        return orders
