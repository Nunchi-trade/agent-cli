"""Tests for OrderRouter and ALOStats in execution/routing.py.

Verifies:
- route() returns ALO for wide spreads with low urgency
- route() returns IOC for high urgency
- route() returns GTC for tight spreads
- route() falls back from ALO when venue doesn't support it
- ALOStats tracking (attempts, successes, fallbacks, rebates)
- ALOStats.to_dict() serialization
"""
from __future__ import annotations

import pytest

from common.models import MarketSnapshot, StrategyDecision
from common.venue_adapter import VenueCapabilities
from execution.routing import ALOStats, OrderRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_router(supports_alo: bool = True) -> OrderRouter:
    caps = VenueCapabilities(supports_alo=supports_alo)
    return OrderRouter(caps)


def _make_snapshot(spread_bps: float = 3.0, mid_price: float = 2000.0) -> MarketSnapshot:
    half_spread = mid_price * spread_bps / 20_000
    return MarketSnapshot(
        instrument="ETH-PERP",
        mid_price=mid_price,
        bid=mid_price - half_spread,
        ask=mid_price + half_spread,
        spread_bps=spread_bps,
        timestamp_ms=1000000,
    )


def _make_decision(order_type: str = "Alo") -> StrategyDecision:
    return StrategyDecision(
        action="place_order",
        instrument="ETH-PERP",
        side="buy",
        size=1.0,
        limit_price=2000.0,
        order_type=order_type,
    )


# ---------------------------------------------------------------------------
# OrderRouter.route() tests
# ---------------------------------------------------------------------------

class TestRouteWideSpreads:
    def test_wide_spread_low_urgency_returns_alo(self):
        """Wide spread (>5 bps) + low urgency (<0.5) -> ALO for maker rebates."""
        router = _make_router(supports_alo=True)
        snapshot = _make_snapshot(spread_bps=8.0)
        decision = _make_decision(order_type="Gtc")

        tif = router.route(decision, snapshot, urgency=0.3)
        assert tif == "Alo"

    def test_wide_spread_medium_urgency_uses_decision(self):
        """Wide spread but urgency >= 0.5 -> uses decision's order_type."""
        router = _make_router(supports_alo=True)
        snapshot = _make_snapshot(spread_bps=8.0)
        decision = _make_decision(order_type="Gtc")

        tif = router.route(decision, snapshot, urgency=0.5)
        assert tif == "Gtc"


class TestRouteHighUrgency:
    def test_high_urgency_returns_ioc(self):
        """Urgency >= 0.8 always returns IOC regardless of spread."""
        router = _make_router(supports_alo=True)
        snapshot = _make_snapshot(spread_bps=10.0)
        decision = _make_decision(order_type="Alo")

        tif = router.route(decision, snapshot, urgency=0.8)
        assert tif == "Ioc"

    def test_very_high_urgency_returns_ioc(self):
        """Urgency = 1.0 -> IOC."""
        router = _make_router(supports_alo=True)
        snapshot = _make_snapshot(spread_bps=1.0)
        decision = _make_decision(order_type="Alo")

        tif = router.route(decision, snapshot, urgency=1.0)
        assert tif == "Ioc"


class TestRouteTightSpreads:
    def test_tight_spread_returns_gtc(self):
        """Tight spread (<2 bps) -> GTC (ALO likely to get rejected)."""
        router = _make_router(supports_alo=True)
        snapshot = _make_snapshot(spread_bps=1.5)
        decision = _make_decision(order_type="Alo")

        tif = router.route(decision, snapshot, urgency=0.3)
        assert tif == "Gtc"

    def test_zero_spread_returns_gtc(self):
        """Zero spread -> GTC."""
        router = _make_router(supports_alo=True)
        snapshot = _make_snapshot(spread_bps=0.0)
        decision = _make_decision(order_type="Alo")

        tif = router.route(decision, snapshot, urgency=0.3)
        assert tif == "Gtc"


class TestRouteNoAloSupport:
    def test_no_alo_support_gtc_passthrough(self):
        """Venue without ALO support passes through GTC."""
        router = _make_router(supports_alo=False)
        snapshot = _make_snapshot(spread_bps=10.0)
        decision = _make_decision(order_type="Gtc")

        tif = router.route(decision, snapshot, urgency=0.3)
        assert tif == "Gtc"

    def test_no_alo_support_converts_alo_to_gtc(self):
        """Venue without ALO support converts ALO -> GTC."""
        router = _make_router(supports_alo=False)
        snapshot = _make_snapshot(spread_bps=10.0)
        decision = _make_decision(order_type="Alo")

        tif = router.route(decision, snapshot, urgency=0.3)
        assert tif == "Gtc"

    def test_no_alo_support_ioc_passthrough(self):
        """Venue without ALO support passes through IOC."""
        router = _make_router(supports_alo=False)
        snapshot = _make_snapshot(spread_bps=10.0)
        decision = _make_decision(order_type="Ioc")

        tif = router.route(decision, snapshot, urgency=0.3)
        assert tif == "Ioc"


class TestRouteDefault:
    def test_mid_spread_uses_decision_order_type(self):
        """Spread between 2-5 bps, medium urgency -> uses decision's order_type."""
        router = _make_router(supports_alo=True)
        snapshot = _make_snapshot(spread_bps=3.5)
        decision = _make_decision(order_type="Alo")

        tif = router.route(decision, snapshot, urgency=0.5)
        assert tif == "Alo"


# ---------------------------------------------------------------------------
# ALOStats tests
# ---------------------------------------------------------------------------

class TestALOStats:
    def test_record_alo_success(self):
        stats = ALOStats()
        stats.record_alo_attempt(success=True, size_usd=10000.0, rebate_bps=0.2)

        assert stats.alo_attempts == 1
        assert stats.alo_successes == 1
        assert stats.alo_fallbacks == 0
        assert stats.estimated_maker_rebate_usd == pytest.approx(0.20)

    def test_record_alo_failure(self):
        stats = ALOStats()
        stats.record_alo_attempt(success=False, size_usd=10000.0)

        assert stats.alo_attempts == 1
        assert stats.alo_successes == 0
        assert stats.alo_fallbacks == 1
        assert stats.estimated_maker_rebate_usd == 0.0

    def test_record_order_gtc(self):
        stats = ALOStats()
        stats.record_order("Gtc")
        assert stats.gtc_orders == 1
        assert stats.ioc_orders == 0

    def test_record_order_ioc(self):
        stats = ALOStats()
        stats.record_order("Ioc")
        assert stats.ioc_orders == 1
        assert stats.gtc_orders == 0

    def test_success_rate_with_data(self):
        stats = ALOStats()
        stats.record_alo_attempt(success=True)
        stats.record_alo_attempt(success=True)
        stats.record_alo_attempt(success=False)

        assert stats.alo_success_rate == pytest.approx(66.6666, rel=0.01)

    def test_success_rate_no_attempts(self):
        stats = ALOStats()
        assert stats.alo_success_rate == 0.0

    def test_cumulative_rebate(self):
        stats = ALOStats()
        stats.record_alo_attempt(success=True, size_usd=50000.0, rebate_bps=0.2)
        stats.record_alo_attempt(success=True, size_usd=30000.0, rebate_bps=0.2)
        stats.record_alo_attempt(success=False, size_usd=20000.0, rebate_bps=0.2)

        # Only successes accumulate rebate: (50000 + 30000) * 0.2 / 10000 = 1.60
        assert stats.estimated_maker_rebate_usd == pytest.approx(1.60)


class TestALOStatsToDict:
    def test_to_dict_keys(self):
        stats = ALOStats()
        d = stats.to_dict()
        expected_keys = {
            "alo_attempts", "alo_successes", "alo_fallbacks",
            "alo_success_rate", "gtc_orders", "ioc_orders",
            "estimated_maker_rebate_usd",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_values(self):
        stats = ALOStats()
        stats.record_alo_attempt(success=True, size_usd=10000.0)
        stats.record_order("Gtc")
        stats.record_order("Ioc")

        d = stats.to_dict()
        assert d["alo_attempts"] == 1
        assert d["alo_successes"] == 1
        assert d["alo_fallbacks"] == 0
        assert d["alo_success_rate"] == 100.0
        assert d["gtc_orders"] == 1
        assert d["ioc_orders"] == 1
        assert d["estimated_maker_rebate_usd"] == 0.20

    def test_to_dict_rounds_values(self):
        stats = ALOStats()
        stats.record_alo_attempt(success=True, size_usd=33333.33, rebate_bps=0.3)

        d = stats.to_dict()
        # 33333.33 * 0.3 / 10000 = 1.0 (rounded to 2 decimals)
        assert d["estimated_maker_rebate_usd"] == round(33333.33 * 0.3 / 10000, 2)
        assert d["alo_success_rate"] == 100.0
