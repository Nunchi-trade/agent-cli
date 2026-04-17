"""Event subscription layer for Perpetual Agent Jobs.

Provides abstract and concrete subscribers that deliver on-chain events
to job engines.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable, List

from cli.jobs.config import JobConfig
from cli.jobs.strategy_interfaces import ChainEvent


# ---------------------------------------------------------------------------
# Abstract subscriber
# ---------------------------------------------------------------------------


class EventSubscriber(ABC):
    """Subscribes to on-chain events and dispatches them to callbacks.

    Implementations connect to a chain data source (WebSocket, RPC polling)
    and invoke a callback for each matching event.
    """

    @abstractmethod
    async def subscribe(
        self,
        event_types: List[str],
        callback: Callable[[ChainEvent], Awaitable[None]],
    ) -> None:
        """Start receiving events of the specified types.

        Parameters
        ----------
        event_types:
            Event type discriminators to listen for (e.g.
            ``["NewBlock", "OracleUpdate"]``).
        callback:
            Async callable invoked with each matching :class:`ChainEvent`.
        """
        ...

    @abstractmethod
    async def unsubscribe(self) -> None:
        """Stop receiving events and close the underlying connection."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check whether the subscriber is currently connected.

        Returns
        -------
        bool
            ``True`` if the connection is alive and subscriptions are active.
        """
        ...


# ---------------------------------------------------------------------------
# Canonical event bus (WebSocket)
# ---------------------------------------------------------------------------


class ChainEventSubscriber(EventSubscriber):
    """WebSocket connection to the canonical chain event bus.

    The event bus provides a persistent WebSocket stream of canonical
    chain events (blocks, oracle updates, contract events) with guaranteed
    ordering.

    Parameters
    ----------
    ws_url:
        WebSocket URL of the chain event bus.
    """

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._connection = None  # websocket connection handle
        self._subscribed = False

    async def subscribe(
        self,
        event_types: List[str],
        callback: Callable[[ChainEvent], Awaitable[None]],
    ) -> None:
        """Subscribe to events via the WebSocket event bus.

        Parameters
        ----------
        event_types:
            Event types to subscribe to.
        callback:
            Async handler for each received event.
        """
        raise NotImplementedError("Implementation deferred — design PR only")

    async def unsubscribe(self) -> None:
        """Close the WebSocket connection and clear subscriptions."""
        raise NotImplementedError("Implementation deferred — design PR only")

    async def is_connected(self) -> bool:
        """Return whether the WebSocket connection is alive."""
        raise NotImplementedError("Implementation deferred — design PR only")


# ---------------------------------------------------------------------------
# Log polling fallback (V1 contracts)
# ---------------------------------------------------------------------------


class LogPollingSubscriber(EventSubscriber):
    """Fallback subscriber that polls ``eth_getLogs`` for contract events.

    Used when the canonical event bus is unavailable (e.g. V1 contract
    deployments on standard EVM chains).

    Parameters
    ----------
    rpc_url:
        JSON-RPC URL to poll for logs.
    poll_interval_s:
        Seconds between ``eth_getLogs`` calls.
    """

    def __init__(self, rpc_url: str, poll_interval_s: float = 1.0) -> None:
        self._rpc_url = rpc_url
        self._poll_interval_s = poll_interval_s
        self._polling = False

    async def subscribe(
        self,
        event_types: List[str],
        callback: Callable[[ChainEvent], Awaitable[None]],
    ) -> None:
        """Start polling for log events matching the requested types.

        Parameters
        ----------
        event_types:
            Event types to filter for.
        callback:
            Async handler for each matched event.
        """
        raise NotImplementedError("Implementation deferred — design PR only")

    async def unsubscribe(self) -> None:
        """Stop the polling loop."""
        raise NotImplementedError("Implementation deferred — design PR only")

    async def is_connected(self) -> bool:
        """Return whether the polling loop is active."""
        raise NotImplementedError("Implementation deferred — design PR only")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_subscriber(config: JobConfig) -> EventSubscriber:
    """Create the appropriate event subscriber for a job configuration.

    Uses :class:`ChainEventSubscriber` if ``config.event_bus_ws`` is set,
    otherwise falls back to :class:`LogPollingSubscriber`.

    Parameters
    ----------
    config:
        Job configuration containing connection endpoints.

    Returns
    -------
    EventSubscriber
        A subscriber instance ready to be connected.
    """
    if config.event_bus_ws:
        return ChainEventSubscriber(config.event_bus_ws)
    return LogPollingSubscriber(config.chain_rpc)
