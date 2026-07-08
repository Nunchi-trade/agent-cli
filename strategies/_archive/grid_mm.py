"""Grid market maker — fixed-interval grid levels above and below mid."""
from __future__ import annotations

from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class GridMMStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str = "grid_mm",
        grid_spacing_bps: float = 10.0,
        num_levels: int = 5,
        size_per_level: float = 0.5,
        max_position: float = 5.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.grid_spacing_bps = grid_spacing_bps
        self.num_levels = num_levels
        self.size_per_level = size_per_level
        self.max_position = max_position

    def on_tick(self, snapshot: MarketSnapshot,
                context: Optional[StrategyContext] = None) -> List[StrategyDecision]:
        mid = snapshot.mid_price
        if mid <= 0:
            return []

        ctx = context or StrategyContext()
        orders: List[StrategyDecision] = []

        # Reduce only — close position, don't open new grid
        if ctx.reduce_only and ctx.position_qty != 0:
            close_side = "sell" if ctx.position_qty > 0 else "buy"
            close_price = snapshot.bid if close_side == "sell" else snapshot.ask
            orders.append(StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side=close_side,
                size=abs(ctx.position_qty),
                limit_price=round(close_price, 2),
                meta={"signal": "reduce_only_close"},
            ))
            return orders

        spacing = mid * self.grid_spacing_bps / 10_000

        for i in range(1, self.num_levels + 1):
            # Bid levels below mid
            bid_price = mid - spacing * i
            # Ask levels above mid
            ask_price = mid + spacing * i

            # Respect max position
            if ctx.position_qty + self.size_per_level <= self.max_position:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="buy",
                    size=self.size_per_level,
                    limit_price=round(bid_price, 2),
                    meta={"signal": "grid_bid", "level": i},
                ))

            if ctx.position_qty - self.size_per_level >= -self.max_position:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="sell",
                    size=self.size_per_level,
                    limit_price=round(ask_price, 2),
                    meta={"signal": "grid_ask", "level": i},
                ))

        return orders
