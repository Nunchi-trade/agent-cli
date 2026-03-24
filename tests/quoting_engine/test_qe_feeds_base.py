"""Tests for feeds/base.py — FeedResult, BaseFeed, TTL cache."""
import time
from quoting_engine.feeds.base import BaseFeed, FeedResult, ttl_cache


class DummyFeed(BaseFeed):
    """Test feed that increments a counter on each refresh."""

    def __init__(self):
        super().__init__()
        self._call_count = 0

    @property
    def name(self) -> str:
        return "dummy"

    def refresh(self) -> FeedResult:
        self._call_count += 1
        return FeedResult(
            value=float(self._call_count),
            timestamp_ms=int(time.time() * 1000),
            source="dummy",
        )


class CachedFeed(BaseFeed):
    """Feed with TTL caching."""

    def __init__(self, ttl_ms: int = 1000):
        super().__init__()
        self._call_count = 0
        self._ttl_ms = ttl_ms

    @property
    def name(self) -> str:
        return "cached"

    @ttl_cache(ttl_ms=100)
    def refresh(self) -> FeedResult:
        self._call_count += 1
        return FeedResult(
            value=float(self._call_count),
            timestamp_ms=int(time.time() * 1000),
            source="cached",
        )


def test_feed_result_defaults():
    r = FeedResult(value=1.0, timestamp_ms=1000, source="test")
    assert r.value == 1.0
    assert not r.stale


def test_feed_result_stale():
    r = FeedResult(value=0.0, timestamp_ms=0, source="test", stale=True)
    assert r.stale


def test_latest_before_refresh():
    f = DummyFeed()
    r = f.latest()
    assert r is None  # no cached result before first refresh


def test_ttl_cache_within_window():
    f = CachedFeed()
    r1 = f.refresh()
    r2 = f.refresh()
    assert r1.value == r2.value  # cached
    assert f._call_count == 1


def test_refresh_updates_latest():
    """After refresh with ttl_cache, latest() returns the cached result."""
    f = CachedFeed()
    f.refresh()
    r = f.latest()
    assert r is not None
    assert r.value == 1.0
    assert not r.stale
