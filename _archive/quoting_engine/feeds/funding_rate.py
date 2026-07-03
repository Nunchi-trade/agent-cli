"""Cross-venue funding rate aggregator.

Phase 2: HL adapter + constant mock + PushFundingRate for external venues.
Real Binance/OKX/Bybit polling adapters live in parent/external_feeds.py
(parent-side only, since the enclave has no network access).
"""
from __future__ import annotations

import statistics
import time
from abc import ABC, abstractmethod
from typing import List, Optional

from quoting_engine.feeds.base import BaseFeed, FeedResult


class FundingRateSource(ABC):
    """Interface for a single venue's funding rate."""

    @property
    @abstractmethod
    def venue(self) -> str:
        """Venue name (e.g. 'binance', 'hyperliquid')."""
        ...

    @abstractmethod
    def fetch(self) -> Optional[float]:
        """Return the current annualized funding rate, or None on failure."""
        ...


class ConstantFundingRate(FundingRateSource):
    """Returns a fixed funding rate — useful for testing and mocks."""

    def __init__(self, rate: float, venue_name: str = "constant"):
        self._rate = rate
        self._venue = venue_name

    @property
    def venue(self) -> str:
        return self._venue

    def fetch(self) -> Optional[float]:
        return self._rate


class HyperliquidFundingRate(FundingRateSource):
    """Reads funding rate from the value pushed into the feed externally.

    In the TEE loop, CompositeMMStrategy sets this from
    MarketSnapshot.funding_rate on each tick.
    """

    def __init__(self):
        self._rate: Optional[float] = None

    @property
    def venue(self) -> str:
        return "hyperliquid"

    def update(self, rate: float) -> None:
        """Called by the strategy to push the latest HL funding rate."""
        self._rate = rate

    def fetch(self) -> Optional[float]:
        return self._rate


class PushFundingRate(FundingRateSource):
    """Generic push-based funding rate source with configurable venue name.

    Used by the strategy to bridge snapshot.external_funding_rates
    (parent-fetched) into the CrossVenueFundingRate aggregator.
    """

    def __init__(self, venue_name: str):
        self._venue_name = venue_name
        self._rate: Optional[float] = None

    @property
    def venue(self) -> str:
        return self._venue_name

    def update(self, rate: float) -> None:
        self._rate = rate

    def fetch(self) -> Optional[float]:
        return self._rate


class CrossVenueFundingRate(BaseFeed):
    """Aggregates funding rates from multiple venues.

    Returns the median of all available rates.  Falls back to the
    single available rate if only one source responds.
    """

    def __init__(self, sources: List[FundingRateSource]):
        self._sources = sources
        self._cached_result: Optional[FeedResult] = None

    @property
    def name(self) -> str:
        return "cross_venue_funding_rate"

    def refresh(self) -> FeedResult:
        rates: List[float] = []
        venues_ok: List[str] = []

        for src in self._sources:
            val = src.fetch()
            if val is not None:
                rates.append(val)
                venues_ok.append(src.venue)

        now_ms = int(time.time() * 1000)

        if not rates:
            result = FeedResult(
                value=0.0,
                timestamp_ms=now_ms,
                source="none",
                stale=True,
            )
        else:
            median_rate = statistics.median(rates)
            result = FeedResult(
                value=median_rate,
                timestamp_ms=now_ms,
                source=",".join(venues_ok),
                stale=False,
            )

        self._cached_result = result
        return result
