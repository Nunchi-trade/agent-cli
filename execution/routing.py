"""Smart order routing — decides optimal TIF based on market conditions.

Routes orders to ALO (maker rebates), GTC, or IOC based on spread width,
urgency, and venue capabilities. Tracks routing statistics for REFLECT.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from common.models import MarketSnapshot, StrategyDecision
from common.venue_adapter import VenueCapabilities


class OrderRouter:
    """Smart order routing — decides optimal TIF based on market conditions."""

    def __init__(self, capabilities: VenueCapabilities):
        self._caps = capabilities
        self._stats = ALOStats()

    def route(
        self,
        decision: StrategyDecision,
        snapshot: MarketSnapshot,
        urgency: float = 0.5,
    ) -> str:
        """Return optimal TIF for this order.

        Args:
            decision: The strategy's order decision
            snapshot: Current market state
            urgency: 0.0 (very passive) to 1.0 (very aggressive)

        Returns:
            TIF string: "Alo", "Gtc", or "Ioc"
        """
        # If venue doesn't support ALO, always use the decision's order_type
        if not self._caps.supports_alo:
            return decision.order_type if decision.order_type != "Alo" else "Gtc"

        # High urgency (exits, stop losses) -> IOC always
        if urgency >= 0.8:
            return "Ioc"

        # Wide spread -> ALO is better (more room for passive fill)
        if snapshot.spread_bps > 5.0 and urgency < 0.5:
            return "Alo"

        # Tight spread -> GTC (ALO likely to get rejected)
        if snapshot.spread_bps < 2.0:
            return "Gtc"

        # Default: use what the strategy requested
        return decision.order_type

    @property
    def stats(self) -> ALOStats:
        return self._stats


@dataclass
class ALOStats:
    """Tracks ALO routing metrics for REFLECT integration."""

    alo_attempts: int = 0
    alo_successes: int = 0
    alo_fallbacks: int = 0
    gtc_orders: int = 0
    ioc_orders: int = 0
    estimated_maker_rebate_usd: float = 0.0

    def record_alo_attempt(
        self, success: bool, size_usd: float = 0.0, rebate_bps: float = 0.2
    ):
        self.alo_attempts += 1
        if success:
            self.alo_successes += 1
            self.estimated_maker_rebate_usd += size_usd * rebate_bps / 10_000
        else:
            self.alo_fallbacks += 1

    def record_order(self, tif: str):
        if tif == "Gtc":
            self.gtc_orders += 1
        elif tif == "Ioc":
            self.ioc_orders += 1

    @property
    def alo_success_rate(self) -> float:
        return (
            self.alo_successes / self.alo_attempts * 100 if self.alo_attempts else 0.0
        )

    def to_dict(self) -> dict:
        return {
            "alo_attempts": self.alo_attempts,
            "alo_successes": self.alo_successes,
            "alo_fallbacks": self.alo_fallbacks,
            "alo_success_rate": round(self.alo_success_rate, 1),
            "gtc_orders": self.gtc_orders,
            "ioc_orders": self.ioc_orders,
            "estimated_maker_rebate_usd": round(self.estimated_maker_rebate_usd, 2),
        }
