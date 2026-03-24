"""Base feed infrastructure: ABC, FeedResult, and TTL caching."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Optional


@dataclass
class FeedResult:
    """Result from a data feed query."""
    value: float
    timestamp_ms: int
    source: str
    stale: bool = False


class BaseFeed(ABC):
    """Abstract base class for all data feeds.

    Feeds follow a pull model: cache the latest value and return it
    on each query.  Explicit refresh() calls update the cache.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable feed name."""
        ...

    @abstractmethod
    def refresh(self) -> FeedResult:
        """Fetch a fresh value from the underlying source.

        Implementations should handle errors internally and return a
        FeedResult with stale=True when the source is unreachable.
        """
        ...

    def latest(self) -> Optional[FeedResult]:
        """Return the most recently cached result, or None if never refreshed."""
        return getattr(self, "_cached_result", None)


def ttl_cache(ttl_ms: int):
    """Decorator that caches a BaseFeed.refresh() result for *ttl_ms* milliseconds.

    Within the TTL window, subsequent calls return the cached FeedResult
    without hitting the underlying source.  After expiry the real refresh()
    is called and the cache is updated.
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(self, *args, **kwargs) -> FeedResult:
            now_ms = int(time.time() * 1000)
            cached: Optional[FeedResult] = getattr(self, "_cached_result", None)
            cache_ts: int = getattr(self, "_cache_ts_ms", 0)

            if cached is not None and (now_ms - cache_ts) < ttl_ms:
                return cached

            result = fn(self, *args, **kwargs)
            self._cached_result = result
            self._cache_ts_ms = now_ms
            return result
        return wrapper
    return decorator
