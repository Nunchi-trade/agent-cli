"""Paradex private WebSocket reconciliation scaffolding.

This module is intentionally dependency-light so it can be developed and tested
before the live Paradex SDK wiring exists.

Design goals for step 6:
- private WS is the primary source of incremental account/order/fill updates
- REST snapshots are used for initial load, reconnect recovery, and fallback
- JWT refresh timing is tracked explicitly because Paradex JWTs are short-lived
- all parsing/state transitions are testable without a live network connection
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

DEFAULT_PRIVATE_CHANNELS = ("orders", "fills", "positions", "account")


@dataclass
class ParadexPrivateEvent:
    """Normalized private-account event derived from a WS payload."""

    channel: str
    payload: Dict[str, Any]
    received_at_ms: int


@dataclass
class ParadexReconciliationState:
    """Tracks private WS health plus the latest order/account snapshots."""

    connected: bool = False
    authenticated: bool = False
    needs_rest_snapshot: bool = True
    last_connect_ms: int = 0
    last_disconnect_ms: int = 0
    last_message_ms: int = 0
    last_auth_ms: int = 0
    last_snapshot_ms: int = 0
    reconnect_count: int = 0
    open_orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    balances: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fills: List[Dict[str, Any]] = field(default_factory=list)


class ParadexPrivateWSReconciler:
    """State machine for Paradex private WS + REST reconciliation."""

    def __init__(
        self,
        *,
        channels: Iterable[str] = DEFAULT_PRIVATE_CHANNELS,
        jwt_refresh_after_s: int = 180,
        max_fill_history: int = 250,
    ):
        self.channels = tuple(dict.fromkeys(channels))
        self.jwt_refresh_after_s = jwt_refresh_after_s
        self.max_fill_history = max_fill_history
        self.state = ParadexReconciliationState()

    @staticmethod
    def authentication_message(jwt: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": "auth",
            "params": {"bearer": jwt},
            "id": 0,
        }

    def subscription_messages(self) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        for idx, channel in enumerate(self.channels, start=1):
            messages.append(
                {
                    "jsonrpc": "2.0",
                    "method": "subscribe",
                    "params": {"channel": channel},
                    "id": idx,
                }
            )
        return messages

    def mark_connected(self, now_ms: Optional[int] = None) -> None:
        now = now_ms or self._now_ms()
        self.state.connected = True
        self.state.authenticated = False
        self.state.last_connect_ms = now

    def mark_authenticated(self, now_ms: Optional[int] = None) -> None:
        now = now_ms or self._now_ms()
        self.state.authenticated = True
        self.state.last_auth_ms = now

    def mark_disconnected(self, now_ms: Optional[int] = None) -> None:
        now = now_ms or self._now_ms()
        self.state.connected = False
        self.state.authenticated = False
        self.state.needs_rest_snapshot = True
        self.state.last_disconnect_ms = now
        self.state.reconnect_count += 1

    def should_refresh_jwt(self, now_ms: Optional[int] = None) -> bool:
        if self.state.last_auth_ms == 0:
            return True
        now = now_ms or self._now_ms()
        return (now - self.state.last_auth_ms) >= self.jwt_refresh_after_s * 1000

    def needs_snapshot(self) -> bool:
        return self.state.needs_rest_snapshot or self.state.last_snapshot_ms == 0

    def apply_rest_snapshot(
        self,
        *,
        orders: Optional[List[Dict[str, Any]]] = None,
        positions: Optional[List[Dict[str, Any]]] = None,
        balances: Optional[List[Dict[str, Any]]] = None,
        now_ms: Optional[int] = None,
    ) -> None:
        now = now_ms or self._now_ms()
        self.state.open_orders = self._index_orders(orders or [])
        self.state.positions = self._index_positions(positions or [])
        self.state.balances = self._index_balances(balances or [])
        self.state.last_snapshot_ms = now
        self.state.needs_rest_snapshot = False

    def reconcile_with_rest(
        self,
        *,
        fetch_orders: Callable[[], List[Dict[str, Any]]],
        fetch_positions: Callable[[], List[Dict[str, Any]]],
        fetch_balances: Callable[[], List[Dict[str, Any]]],
        now_ms: Optional[int] = None,
    ) -> None:
        self.apply_rest_snapshot(
            orders=fetch_orders(),
            positions=fetch_positions(),
            balances=fetch_balances(),
            now_ms=now_ms,
        )

    def parse_message(self, message: Dict[str, Any], now_ms: Optional[int] = None) -> List[ParadexPrivateEvent]:
        now = now_ms or self._now_ms()
        self.state.last_message_ms = now

        if message.get("method") in {"subscription", "update"}:
            params = message.get("params", {}) or {}
            channel = self._normalize_channel(params.get("channel") or params.get("stream") or "")
            data = params.get("data")
            items = data if isinstance(data, list) else [data]
            return [
                ParadexPrivateEvent(channel=channel, payload=item or {}, received_at_ms=now)
                for item in items
                if channel
            ]

        if message.get("channel"):
            channel = self._normalize_channel(message.get("channel", ""))
            data = message.get("data")
            items = data if isinstance(data, list) else [data]
            return [
                ParadexPrivateEvent(channel=channel, payload=item or {}, received_at_ms=now)
                for item in items
                if channel
            ]

        return []

    def apply_message(self, message: Dict[str, Any], now_ms: Optional[int] = None) -> List[ParadexPrivateEvent]:
        events = self.parse_message(message, now_ms=now_ms)
        for event in events:
            self.apply_event(event)
        return events

    def apply_event(self, event: ParadexPrivateEvent) -> None:
        channel = self._normalize_channel(event.channel)
        payload = event.payload

        if channel == "orders":
            order_id = self._extract_id(payload, keys=("id", "order_id", "client_id"))
            if not order_id:
                return
            status = str(payload.get("status", "")).lower()
            if status in {"closed", "cancelled", "canceled", "filled"}:
                self.state.open_orders.pop(order_id, None)
            else:
                self.state.open_orders[order_id] = payload
            return

        if channel == "fills":
            self.state.fills.append(payload)
            if len(self.state.fills) > self.max_fill_history:
                self.state.fills = self.state.fills[-self.max_fill_history :]
            return

        if channel == "positions":
            symbol = self._extract_symbol(payload)
            if not symbol:
                return
            size = self._extract_position_size(payload)
            if size == 0:
                self.state.positions.pop(symbol, None)
            else:
                self.state.positions[symbol] = payload
            return

        if channel == "account":
            asset = self._extract_balance_asset(payload)
            if not asset:
                return
            amount = self._extract_balance_amount(payload)
            if amount == 0:
                self.state.balances.pop(asset, None)
            else:
                self.state.balances[asset] = payload
            return

    def snapshot_summary(self) -> Dict[str, Any]:
        return {
            "connected": self.state.connected,
            "authenticated": self.state.authenticated,
            "needs_rest_snapshot": self.state.needs_rest_snapshot,
            "open_orders": len(self.state.open_orders),
            "positions": len(self.state.positions),
            "balances": len(self.state.balances),
            "fills": len(self.state.fills),
            "last_snapshot_ms": self.state.last_snapshot_ms,
            "last_message_ms": self.state.last_message_ms,
            "reconnect_count": self.state.reconnect_count,
        }

    @staticmethod
    def _normalize_channel(channel: str) -> str:
        normalized = str(channel or "").strip().lower()
        aliases = {
            "order": "orders",
            "fill": "fills",
            "position": "positions",
            "balances": "account",
            "balance": "account",
            "accounts": "account",
        }
        return aliases.get(normalized, normalized)

    @staticmethod
    def _extract_id(payload: Dict[str, Any], *, keys: Iterable[str]) -> str:
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _extract_symbol(payload: Dict[str, Any]) -> str:
        return str(payload.get("market") or payload.get("symbol") or payload.get("instrument") or "")

    @staticmethod
    def _extract_position_size(payload: Dict[str, Any]) -> float:
        for key in ("size", "position_size", "quantity", "qty"):
            value = payload.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    @staticmethod
    def _extract_balance_asset(payload: Dict[str, Any]) -> str:
        return str(payload.get("asset") or payload.get("currency") or payload.get("token") or "")

    @staticmethod
    def _extract_balance_amount(payload: Dict[str, Any]) -> float:
        for key in ("available", "balance", "total", "amount"):
            value = payload.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    @staticmethod
    def _index_orders(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        indexed: Dict[str, Dict[str, Any]] = {}
        for item in items:
            order_id = ParadexPrivateWSReconciler._extract_id(item, keys=("id", "order_id", "client_id"))
            if order_id:
                indexed[order_id] = item
        return indexed

    @staticmethod
    def _index_positions(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        indexed: Dict[str, Dict[str, Any]] = {}
        for item in items:
            symbol = ParadexPrivateWSReconciler._extract_symbol(item)
            if symbol:
                indexed[symbol] = item
        return indexed

    @staticmethod
    def _index_balances(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        indexed: Dict[str, Dict[str, Any]] = {}
        for item in items:
            asset = ParadexPrivateWSReconciler._extract_balance_asset(item)
            if asset:
                indexed[asset] = item
        return indexed

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
