"""Hedge agent — reduces excess exposure per deterministic mandate.

From KorAI spec: "reduces exposure per deterministic mandate."
Only acts when |inventory| exceeds a configurable threshold,
then places aggressive orders to bring inventory back toward zero.
"""
from __future__ import annotations

from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class HedgeAgent(BaseStrategy):
    """Deterministic hedge agent that reduces inventory when overexposed."""

    def __init__(
        self,
        strategy_id: str = "hedge_agent",
        inventory_threshold: float = 3.0,
        urgency_factor: float = 0.5,
        max_hedge_size: float = 5.0,
        slippage_bps: float = 10.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.inventory_threshold = inventory_threshold
        self.urgency_factor = urgency_factor
        self.max_hedge_size = max_hedge_size
        self.slippage_bps = slippage_bps

    def on_tick(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext] = None,
    ) -> List[StrategyDecision]:
        if snapshot.mid_price <= 0:
            return []

        q = context.position_qty if context else 0.0

        # Only hedge when inventory exceeds threshold
        if abs(q) <= self.inventory_threshold:
            return []

        excess = abs(q) - self.inventory_threshold
        hedge_size = min(excess * self.urgency_factor, self.max_hedge_size)
        hedge_size = round(hedge_size, 6)

        if hedge_size <= 0.001:
            return []

        slip = snapshot.mid_price * (self.slippage_bps / 10_000)

        if q > 0:
            # Long → sell to reduce
            price = round(snapshot.mid_price - slip, 2)
            side = "sell"
            signal = "hedge_sell"
        else:
            # Short → buy to reduce
            price = round(snapshot.mid_price + slip, 2)
            side = "buy"
            signal = "hedge_buy"

        return [StrategyDecision(
            action="place_order",
            instrument=snapshot.instrument,
            side=side,
            size=hedge_size,
            limit_price=price,
            order_type="Ioc",
            meta={
                "signal": signal,
                "inventory": q,
                "excess": round(excess, 4),
                "urgency": self.urgency_factor,
            },
        )]
