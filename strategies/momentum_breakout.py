"""Momentum breakout strategy — enter on volume + price breakout above/below N-period range."""
from __future__ import annotations

from collections import deque
from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class MomentumBreakoutStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str = "momentum_breakout",
        lookback: int = 20,
        breakout_threshold_bps: float = 20.0,   # lowered from 50 for thin YEX markets
        volume_surge_mult: float = 1.5,        # lowered from 2.0 for thin YEX markets
        trailing_stop_bps: float = 30.0,
        size: float = 1.0,
    ):
        super().__init__(strategy_id=strategy_id)
        self.lookback = lookback
        self.breakout_threshold_bps = breakout_threshold_bps
        self.volume_surge_mult = volume_surge_mult
        self.trailing_stop_bps = trailing_stop_bps
        self.size = size

        self.highs: deque = deque(maxlen=lookback)
        self.lows: deque = deque(maxlen=lookback)
        self.volumes: deque = deque(maxlen=lookback)

    def on_tick(self, snapshot: MarketSnapshot,
                context: Optional[StrategyContext] = None) -> List[StrategyDecision]:
        mid = snapshot.mid_price
        if mid <= 0:
            return []

        # Use ask as proxy high, bid as proxy low
        high = snapshot.ask if snapshot.ask > 0 else mid
        low = snapshot.bid if snapshot.bid > 0 else mid
        vol = getattr(snapshot, "volume_24h", 0) or snapshot.open_interest

        if len(self.highs) < self.lookback:
            self.highs.append(high)
            self.lows.append(low)
            self.volumes.append(vol)
            return []

        # Compute range from PREVIOUS period before adding current tick
        period_high = max(self.highs)
        period_low = min(self.lows)
        avg_vol = sum(self.volumes) / len(self.volumes) if self.volumes else 1

        # Update history with current tick
        self.highs.append(high)
        self.lows.append(low)
        self.volumes.append(vol)

        # Check for volume surge
        vol_surge = vol > avg_vol * self.volume_surge_mult if avg_vol > 0 else False

        # Check for breakout
        upside_bps = (mid - period_high) / period_high * 10_000 if period_high > 0 else 0
        downside_bps = (period_low - mid) / period_low * 10_000 if period_low > 0 else 0

        ctx = context or StrategyContext()
        orders: List[StrategyDecision] = []

        # Trailing stop for existing position
        if ctx.position_qty != 0:
            # If we have a position, check for trailing stop exit
            if ctx.position_qty > 0:
                stop_price = mid * (1 - self.trailing_stop_bps / 10_000)
                if snapshot.bid <= stop_price:
                    orders.append(StrategyDecision(
                        action="place_order",
                        instrument=snapshot.instrument,
                        side="sell",
                        size=abs(ctx.position_qty),
                        limit_price=round(snapshot.bid, 2),
                        order_type="Ioc",
                        meta={"signal": "trailing_stop_long", "stop_price": round(stop_price, 2)},
                    ))
            else:
                stop_price = mid * (1 + self.trailing_stop_bps / 10_000)
                if snapshot.ask >= stop_price:
                    orders.append(StrategyDecision(
                        action="place_order",
                        instrument=snapshot.instrument,
                        side="buy",
                        size=abs(ctx.position_qty),
                        limit_price=round(snapshot.ask, 2),
                        order_type="Ioc",
                        meta={"signal": "trailing_stop_short", "stop_price": round(stop_price, 2)},
                    ))
            return orders

        # Breakout entry (no position)
        if upside_bps > self.breakout_threshold_bps and vol_surge:
            orders.append(StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="buy",
                size=self.size,
                limit_price=round(snapshot.ask, 2),
                meta={
                    "signal": "breakout_long",
                    "breakout_bps": round(upside_bps, 2),
                    "volume_surge": True,
                },
                order_type="Ioc",
            ))
        elif downside_bps > self.breakout_threshold_bps and vol_surge:
            orders.append(StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="sell",
                size=self.size,
                limit_price=round(snapshot.bid, 2),
                meta={
                    "signal": "breakout_short",
                    "breakout_bps": round(downside_bps, 2),
                    "volume_surge": True,
                },
                order_type="Ioc",
            ))

        return orders
