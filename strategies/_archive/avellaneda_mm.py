"""Avellaneda-Stoikov inventory-aware market maker.

Implements the reservation price / optimal spread model from the
House Liquidity Risk Framework:

  reservation_price = mid - q * gamma * sigma^2 * T
  optimal_spread    = gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/k)

Where:
  q     = net inventory (positive = long)
  gamma = risk aversion parameter
  sigma = estimated volatility (rolling std of log returns)
  T     = time horizon (normalised to 1.0 per tick)
  k     = order-arrival intensity
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Dict, List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class AvellanedaStoikovMM(BaseStrategy):
    """Inventory-aware market maker with dynamic spread adjustment."""

    def __init__(
        self,
        strategy_id: str = "avellaneda_mm",
        gamma: float = 0.1,
        k: float = 1.5,
        base_size: float = 1.0,
        max_inventory: float = 10.0,
        min_spread_bps: float = 5.0,
        max_spread_bps: float = 200.0,
        vol_window: int = 30,
        toxicity_scorer=None,
        **kwargs,
    ):
        super().__init__(strategy_id=strategy_id)
        self.gamma = gamma
        self.k = k
        self.base_size = base_size
        self.max_inventory = max_inventory
        self.min_spread_bps = min_spread_bps
        self.max_spread_bps = max_spread_bps
        self.vol_window = vol_window

        # Optional anomaly-driven toxicity scorer (anomaly_protection.AnomalyToxicityScorer)
        self._tox_scorer = toxicity_scorer

        # Rolling price history for volatility estimation
        self._prices: deque = deque(maxlen=vol_window)
        self._log_returns: deque = deque(maxlen=vol_window)

    # ------------------------------------------------------------------
    # Volatility estimation
    # ------------------------------------------------------------------

    def _update_vol(self, mid: float) -> float:
        """Update rolling volatility and return current estimate."""
        if self._prices:
            prev = self._prices[-1]
            if prev > 0 and mid > 0:
                self._log_returns.append(math.log(mid / prev))
        self._prices.append(mid)

        if len(self._log_returns) < 3:
            # Default volatility when insufficient data
            return mid * (self.min_spread_bps / 10_000)

        mean_r = sum(self._log_returns) / len(self._log_returns)
        var = sum((r - mean_r) ** 2 for r in self._log_returns) / len(self._log_returns)
        return math.sqrt(max(var, 1e-12)) * mid

    # ------------------------------------------------------------------
    # Core model
    # ------------------------------------------------------------------

    def _reservation_price(self, mid: float, q: float, sigma: float) -> float:
        """Mid adjusted for inventory: skew toward reducing position."""
        T = 1.0  # normalised time horizon per tick
        return mid - q * self.gamma * sigma ** 2 * T

    def _optimal_spread(self, sigma: float) -> float:
        """Theoretical optimal spread from A-S model."""
        T = 1.0
        spread = self.gamma * sigma ** 2 * T
        if self.gamma > 0:
            spread += (2.0 / self.gamma) * math.log(1.0 + self.gamma / self.k)
        return spread

    def _clamp_spread(self, spread: float, mid: float) -> float:
        """Enforce min/max spread bounds."""
        min_s = mid * (self.min_spread_bps / 10_000)
        max_s = mid * (self.max_spread_bps / 10_000)
        return max(min_s, min(spread, max_s))

    def _scale_size(self, q: float) -> float:
        """Reduce order size as inventory approaches max."""
        if self.max_inventory <= 0:
            return self.base_size
        utilisation = abs(q) / self.max_inventory
        scale = max(0.1, 1.0 - utilisation)
        return round(self.base_size * scale, 6)

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def on_tick(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext] = None,
    ) -> List[StrategyDecision]:
        mid = snapshot.mid_price
        if mid <= 0:
            return []

        # Inventory from context, else assume flat
        q = context.position_qty if context else 0.0
        reduce_only = context.reduce_only if context else False

        sigma = self._update_vol(mid)
        r_price = self._reservation_price(mid, q, sigma)
        raw_spread = self._optimal_spread(sigma)

        # Anomaly-driven spread widening (h_tox is half-spread in price units)
        h_tox = 0.0
        if self._tox_scorer is not None:
            h_tox = self._tox_scorer.score(mid, snapshot.bid, snapshot.ask, snapshot.timestamp_ms)
            raw_spread += 2 * h_tox

        half_spread = self._clamp_spread(raw_spread, mid) / 2.0

        bid = round(r_price - half_spread, 2)
        ask = round(r_price + half_spread, 2)
        size = self._scale_size(q)

        orders: List[StrategyDecision] = []

        if reduce_only:
            # Only place orders that reduce position
            if q > 0:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="sell",
                    size=min(size, abs(q)),
                    limit_price=ask,
                    meta={"signal": "reduce_only_sell", "inventory": q},
                ))
            elif q < 0:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="buy",
                    size=min(size, abs(q)),
                    limit_price=bid,
                    meta={"signal": "reduce_only_buy", "inventory": q},
                ))
            return orders

        # Normal two-sided quoting
        orders.append(StrategyDecision(
            action="place_order",
            instrument=snapshot.instrument,
            side="buy",
            size=size,
            limit_price=bid,
            meta={
                "signal": "as_bid",
                "reservation_price": round(r_price, 2),
                "spread": round(raw_spread, 4),
                "sigma": round(sigma, 4),
                "inventory": q,
                "h_tox": round(h_tox, 6),
            },
        ))
        orders.append(StrategyDecision(
            action="place_order",
            instrument=snapshot.instrument,
            side="sell",
            size=size,
            limit_price=ask,
            meta={
                "signal": "as_ask",
                "reservation_price": round(r_price, 2),
                "spread": round(raw_spread, 4),
                "sigma": round(sigma, 4),
                "inventory": q,
                "h_tox": round(h_tox, 6),
            },
        ))

        return orders
