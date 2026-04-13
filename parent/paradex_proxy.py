"""Paradex low-level proxy/client wrapper.

This is the step-2 transport layer for Paradex.
It is intentionally adapter-agnostic: the future VenueAdapter can call into this
proxy for auth, market metadata, order submission, account snapshots, and
private-WS reconciliation.

The live SDK import is lazy so this module remains importable in environments
where paradex_py is not installed yet.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.credentials import resolve_private_key, resolve_wallet_address
from parent.paradex_private_ws import ParadexPrivateWSReconciler

log = logging.getLogger("paradex_proxy")

PARADEX_TESTNET_REST = "https://api.testnet.paradex.trade/v1"
PARADEX_MAINNET_REST = "https://api.prod.paradex.trade/v1"
PARADEX_TESTNET_WS = "wss://ws.api.testnet.paradex.trade/v1"
PARADEX_MAINNET_WS = "wss://ws.api.prod.paradex.trade/v1"


@dataclass
class ParadexFill:
    oid: str
    instrument: str
    side: str
    price: float
    quantity: float
    timestamp_ms: int
    fee: float = 0.0


class ParadexProxy:
    """Low-level Paradex proxy around the official SDK plus WS reconciliation."""

    def __init__(
        self,
        *,
        l1_private_key: Optional[str] = None,
        l1_address: Optional[str] = None,
        l2_private_key: Optional[str] = None,
        l2_address: Optional[str] = None,
        testnet: bool = True,
        jwt_refresh_after_s: int = 180,
    ):
        self.testnet = testnet
        self.l1_private_key = l1_private_key
        self._validate_l1_signer_alignment(l1_address, l1_private_key)
        self.l1_address = self._resolve_l1_address(l1_address, l1_private_key)
        self.l2_private_key = l2_private_key or resolve_private_key("paradex")
        self.l2_address = self._resolve_l2_address(l2_address)
        self.jwt_refresh_after_s = jwt_refresh_after_s

        self._client = None
        self._api_client = None
        self._ws_client = None
        self._jwt_token: str = ""
        self._authenticated = False
        self._reconciler = ParadexPrivateWSReconciler(jwt_refresh_after_s=jwt_refresh_after_s)

        self.placed_orders: List[Dict[str, Any]] = []
        self.fills: List[ParadexFill] = []

    @property
    def rest_url(self) -> str:
        return PARADEX_MAINNET_REST if self.testnet is False else PARADEX_TESTNET_REST

    @property
    def ws_url(self) -> str:
        return PARADEX_MAINNET_WS if self.testnet is False else PARADEX_TESTNET_WS

    @property
    def sdk_env(self) -> str:
        return "prod" if self.testnet is False else "testnet"

    @property
    def reconciler(self) -> ParadexPrivateWSReconciler:
        return self._reconciler

    def connect(self) -> None:
        self._ensure_client()
        self._authenticate_if_needed(force=False)

    def get_client(self):
        self._ensure_client()
        return self._client

    def get_api_client(self):
        self._ensure_client()
        return self._api_client or getattr(self._client, "api_client", None)

    def get_ws_client(self):
        self._ensure_client()
        return self._ws_client or getattr(self._client, "ws_client", None)

    def jwt_token(self) -> str:
        self._authenticate_if_needed(force=False)
        return self._jwt_token

    def should_refresh_jwt(self) -> bool:
        return self._reconciler.should_refresh_jwt()

    def build_private_ws_auth_message(self) -> Dict[str, Any]:
        return self._reconciler.authentication_message(self.jwt_token())

    def build_private_ws_subscriptions(self) -> List[Dict[str, Any]]:
        return self._reconciler.subscription_messages()

    def mark_private_ws_connected(self) -> None:
        self._reconciler.mark_connected()

    def mark_private_ws_authenticated(self) -> None:
        self._reconciler.mark_authenticated()

    def mark_private_ws_disconnected(self) -> None:
        self._reconciler.mark_disconnected()

    def handle_private_ws_message(self, message: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = self._reconciler.apply_message(message)
        normalized: List[Dict[str, Any]] = []
        for event in events:
            normalized.append(
                {
                    "channel": event.channel,
                    "payload": event.payload,
                    "received_at_ms": event.received_at_ms,
                }
            )
        return normalized

    def reconcile_private_state(self) -> Dict[str, Any]:
        self._reconciler.reconcile_with_rest(
            fetch_orders=self.fetch_orders,
            fetch_positions=self.fetch_positions,
            fetch_balances=self.fetch_balances,
        )
        return self._reconciler.snapshot_summary()

    def fetch_markets(self) -> List[Dict[str, Any]]:
        self._ensure_client()
        candidates = [
            (self.get_api_client(), "fetch_markets"),
            (self.get_api_client(), "get_markets"),
            (self._client, "fetch_markets"),
            (self._client, "get_markets"),
        ]
        result = self._call_first(candidates)
        return self._coerce_list(result)

    def fetch_balances(self) -> List[Dict[str, Any]]:
        self._authenticate_if_needed(force=False)
        candidates = [
            (self.get_api_client(), "fetch_balances"),
            (self._client, "fetch_balances"),
            (self._client, "get_balances"),
        ]
        result = self._call_first(candidates)
        return self._coerce_list(result)

    def fetch_positions(self) -> List[Dict[str, Any]]:
        self._authenticate_if_needed(force=False)
        candidates = [
            (self.get_api_client(), "fetch_positions"),
            (self._client, "fetch_positions"),
            (self._client, "get_positions"),
        ]
        result = self._call_first(candidates)
        return self._coerce_list(result)

    def fetch_orders(self) -> List[Dict[str, Any]]:
        self._authenticate_if_needed(force=False)
        candidates = [
            (self.get_api_client(), "fetch_orders"),
            (self._client, "fetch_orders"),
            (self._client, "get_orders"),
            (self._client, "fetch_open_orders"),
        ]
        result = self._call_first(candidates)
        return self._coerce_list(result)

    def fetch_fills(self) -> List[Dict[str, Any]]:
        self._authenticate_if_needed(force=False)
        candidates = [
            (self.get_api_client(), "fetch_fills"),
            (self._client, "fetch_fills"),
            (self._client, "get_fills"),
        ]
        result = self._call_first(candidates)
        return self._coerce_list(result)

    def fetch_candles(self, market: str, interval: str, lookback_ms: int) -> List[Dict[str, Any]]:
        self._ensure_client()
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - int(lookback_ms)
        candidates = [
            (self.get_api_client(), "fetch_candles"),
            (self.get_api_client(), "get_candles"),
            (self._client, "fetch_candles"),
            (self._client, "get_candles"),
        ]
        kwargs_options = [
            {"market": market, "interval": interval, "start_ms": start_ms, "end_ms": end_ms},
            {"symbol": market, "interval": interval, "start_ms": start_ms, "end_ms": end_ms},
            {"market": market, "interval": interval, "start": start_ms, "end": end_ms},
            {"symbol": market, "interval": interval, "start": start_ms, "end": end_ms},
        ]
        last_error: Optional[Exception] = None
        for obj, method_name in candidates:
            if obj is None:
                continue
            method = getattr(obj, method_name, None)
            if not callable(method):
                continue
            for kwargs in kwargs_options:
                try:
                    return self._coerce_list(method(**kwargs))
                except TypeError as e:
                    last_error = e
                    continue
                except Exception as e:
                    last_error = e
                    raise
        if last_error:
            log.debug("Paradex candle fetch fell through all signatures: %s", last_error)
        return []

    def get_market_metadata(self, instrument: str) -> Dict[str, Any]:
        instrument_upper = instrument.upper()
        for market in self.fetch_markets():
            symbol = str(market.get("symbol") or market.get("market") or market.get("instrument") or "")
            if symbol.upper() == instrument_upper:
                return market
        return {}

    def get_market_summary(self, instrument: str) -> Dict[str, Any]:
        instrument_upper = instrument.upper()
        api_client = self.get_api_client()
        if api_client is None:
            return {}
        method = getattr(api_client, "fetch_markets_summary", None)
        if not callable(method):
            return {}
        response = method({"market": instrument_upper})
        if isinstance(response, dict):
            rows = response.get("results", [])
            if isinstance(rows, list) and rows:
                return self._coerce_dict(rows[0])
        return self._coerce_dict(response)

    def get_account_state(self) -> Dict[str, Any]:
        balances = self.fetch_balances()
        positions = self.fetch_positions()
        orders = self.fetch_orders()
        return {
            "address": self.l2_address,
            "balances": balances,
            "positions": positions,
            "open_orders": orders,
            "venue": "paradex",
            "network": self.sdk_env,
            "rest_url": self.rest_url,
            "ws_url": self.ws_url,
            "ws_reconciliation": self._reconciler.snapshot_summary(),
        }

    def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        self._authenticate_if_needed(force=False)
        sdk_order = self._build_sdk_order(order)
        candidates = [
            (self.get_api_client(), "submit_order"),
            (self._client, "submit_order"),
            (self._client, "place_order"),
        ]
        result = self._call_first(candidates, kwargs={"order": sdk_order}, positional_fallback=sdk_order)
        normalized = self._coerce_dict(result)
        if normalized:
            self.placed_orders.append(normalized)
        return normalized

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        self._authenticate_if_needed(force=False)
        candidates = [
            (self.get_api_client(), "cancel_order"),
            (self._client, "cancel_order"),
        ]
        return self._coerce_dict(self._call_first(candidates, kwargs={"order_id": order_id}, positional_fallback=order_id))

    def cancel_all_orders(self) -> Dict[str, Any]:
        self._authenticate_if_needed(force=False)
        candidates = [
            (self.get_api_client(), "cancel_all_orders"),
            (self._client, "cancel_all_orders"),
        ]
        return self._coerce_dict(self._call_first(candidates))

    def record_fill(self, fill: Dict[str, Any]) -> ParadexFill:
        normalized = ParadexFill(
            oid=str(fill.get("id") or fill.get("order_id") or fill.get("client_id") or ""),
            instrument=str(fill.get("symbol") or fill.get("market") or fill.get("instrument") or ""),
            side=str(fill.get("side") or ""),
            price=float(fill.get("price") or fill.get("avg_price") or 0.0),
            quantity=float(fill.get("size") or fill.get("qty") or fill.get("quantity") or 0.0),
            timestamp_ms=int(fill.get("timestamp_ms") or fill.get("timestamp") or int(time.time() * 1000)),
            fee=float(fill.get("fee") or 0.0),
        )
        self.fills.append(normalized)
        return normalized

    def _ensure_client(self) -> None:
        if self._client is not None:
            return

        if not self.l2_private_key:
            raise RuntimeError("No Paradex private key available. Set PARADEX_PRIVATE_KEY or PARADEX_L2_PRIVATE_KEY.")
        if not self.l1_address and not self.l2_address:
            raise RuntimeError(
                "No Paradex address available. Set PARADEX_L1_ADDRESS and/or PARADEX_ADDRESS/PARADEX_L2_ADDRESS."
            )

        try:
            from paradex_py import Paradex, ParadexSubkey  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "paradex_py is not installed. Install project dependencies in a venv before using ParadexProxy."
            ) from e

        last_error: Optional[Exception] = None
        constructors = []
        if self.l1_private_key and self.l1_address:
            constructors.append(
                (
                    Paradex,
                    {
                        "env": self.sdk_env,
                        "l1_address": self.l1_address,
                        "l1_private_key": self.l1_private_key,
                        "l2_private_key": self.l2_private_key,
                    },
                )
            )
        if self.l2_address:
            constructors.append(
                (ParadexSubkey, {"env": self.sdk_env, "l2_private_key": self.l2_private_key, "l2_address": self.l2_address})
            )
        if self.l1_address:
            constructors.append((Paradex, {"env": self.sdk_env, "l1_address": self.l1_address, "l2_private_key": self.l2_private_key}))
        for klass, kwargs in constructors:
            try:
                self._client = klass(**kwargs)
                self._api_client = getattr(self._client, "api_client", None)
                self._ws_client = getattr(self._client, "ws_client", None)
                log.info("Paradex client initialized: env=%s address=%s", self.sdk_env, self.l2_address)
                return
            except TypeError as e:
                last_error = e
                continue
            except Exception as e:
                last_error = e
                break

        raise RuntimeError(f"Failed to initialize Paradex SDK client: {last_error}")

    def _authenticate_if_needed(self, *, force: bool) -> None:
        self._ensure_client()
        if force or not self._authenticated or self.should_refresh_jwt():
            self._authenticate()

    def _authenticate(self) -> None:
        self._ensure_client()
        client = self._client
        token = ""

        method_names = (
            "auth",
            "authenticate",
            "login",
            "connect",
            "initialize",
            "init_account",
        )
        for method_name in method_names:
            method = getattr(client, method_name, None)
            if callable(method):
                try:
                    result = method()
                    token = self._extract_token(result)
                    if not token:
                        token = self._extract_token(getattr(client, "api_client", None))
                    if token:
                        break
                except TypeError:
                    continue
                except Exception as e:
                    log.debug("Paradex auth method %s failed: %s", method_name, e)

        if not token:
            token = self._extract_token(getattr(client, "api_client", None))
        if not token:
            token = self._extract_token(client)

        self._jwt_token = token
        self._authenticated = True
        self._reconciler.mark_authenticated()
        if token:
            log.info("Paradex authentication token acquired")
        else:
            log.info("Paradex client authenticated (token not introspectable from SDK object)")

    @staticmethod
    def _extract_token(source: Any) -> str:
        if source is None:
            return ""
        for attr in ("jwt", "jwt_token", "token", "bearer_token", "access_token"):
            value = getattr(source, attr, None)
            if isinstance(value, str) and value:
                return value
        if isinstance(source, dict):
            for key in ("jwt", "token", "access_token", "bearer"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    @staticmethod
    def _call_first(candidates, kwargs: Optional[Dict[str, Any]] = None, positional_fallback: Any = None):
        kwargs = kwargs or {}
        last_error: Optional[Exception] = None
        for obj, method_name in candidates:
            if obj is None:
                continue
            method = getattr(obj, method_name, None)
            if not callable(method):
                continue
            try:
                if kwargs:
                    return method(**kwargs)
                return method()
            except TypeError as e:
                last_error = e
                if positional_fallback is not None:
                    try:
                        return method(positional_fallback)
                    except Exception as inner:
                        last_error = inner
                        continue
                continue
            except Exception as e:
                last_error = e
                raise
        if last_error:
            raise last_error
        raise RuntimeError("No compatible SDK method found for Paradex operation")

    @staticmethod
    def _coerce_list(result: Any) -> List[Dict[str, Any]]:
        if result is None:
            return []
        if isinstance(result, list):
            return [item if isinstance(item, dict) else {"value": item} for item in result]
        if isinstance(result, dict):
            for key in ("results", "data", "orders", "positions", "balances", "markets", "fills"):
                value = result.get(key)
                if isinstance(value, list):
                    return [item if isinstance(item, dict) else {"value": item} for item in value]
            return [result]
        return [{"value": result}]

    @staticmethod
    def _coerce_dict(result: Any) -> Dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        return {"value": result}

    @staticmethod
    def _build_sdk_order(order: Dict[str, Any]):
        from decimal import Decimal
        from paradex_py.common.order import Order, OrderSide, OrderType  # type: ignore

        symbol = str(order.get("symbol") or order.get("market") or order.get("instrument") or "")
        side = str(order.get("side") or "BUY").upper()
        size = Decimal(str(order.get("size") or order.get("qty") or order.get("quantity") or 0))
        price = Decimal(str(order.get("price") or order.get("limit_price") or 0))
        instruction = str(order.get("time_in_force") or order.get("instruction") or "GTC").upper()
        client_id = str(order.get("client_id") or "")
        reduce_only = bool(order.get("reduce_only", False))
        order_id = order.get("order_id") or order.get("id")

        return Order(
            market=symbol,
            order_type=OrderType.Limit,
            order_side=OrderSide.Buy if side == "BUY" else OrderSide.Sell,
            size=size,
            limit_price=price,
            client_id=client_id,
            instruction=instruction,
            reduce_only=reduce_only,
            order_id=str(order_id) if order_id else None,
        )

    @staticmethod
    def _resolve_l1_address(explicit_address: Optional[str], l1_private_key: Optional[str]) -> str:
        candidates = [
            explicit_address,
            ParadexProxy._derive_evm_address_from_private_key(l1_private_key),
            os.environ.get("PARADEX_L1_ADDRESS", ""),
            os.environ.get("PARADEX_EVM_ADDRESS", ""),
            os.environ.get("AGENT_WALLET_ADDRESS", ""),
        ]
        for candidate in candidates:
            addr = (candidate or "").strip()
            if re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
                return addr
        return ""

    @staticmethod
    def _validate_l1_signer_alignment(explicit_address: Optional[str], l1_private_key: Optional[str]) -> None:
        derived = ParadexProxy._derive_evm_address_from_private_key(l1_private_key)
        if not derived:
            return

        intended_candidates = [
            explicit_address,
            os.environ.get("PARADEX_L1_ADDRESS", ""),
            os.environ.get("PARADEX_EVM_ADDRESS", ""),
            os.environ.get("AGENT_WALLET_ADDRESS", ""),
        ]
        for candidate in intended_candidates:
            addr = (candidate or "").strip()
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
                continue
            if addr.lower() != derived.lower():
                raise ValueError(
                    "Paradex L1 signer mismatch: private key derives to "
                    f"{derived} but intended L1 address is {addr}."
                )

    @staticmethod
    def _resolve_l2_address(explicit_address: Optional[str]) -> str:
        candidates = [
            explicit_address,
            os.environ.get("PARADEX_L2_ADDRESS", ""),
            os.environ.get("PARADEX_ADDRESS", ""),
        ]
        for candidate in candidates:
            addr = (candidate or "").strip()
            if re.fullmatch(r"0x[0-9a-fA-F]{40,64}", addr):
                return addr
        return ""

    @staticmethod
    def _derive_evm_address_from_private_key(private_key: Optional[str]) -> str:
        key = (private_key or "").strip()
        if not key:
            return ""
        if not key.startswith("0x"):
            key = f"0x{key}"
        try:
            from eth_account import Account  # type: ignore
        except Exception:
            return ""
        try:
            return str(Account.from_key(key).address)
        except Exception:
            return ""
