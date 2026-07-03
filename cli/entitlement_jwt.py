"""Verify Nunchi entitlement JWT snapshots for hosted MCP runner context."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional

_TIERS = {"read_only", "testnet_trading", "live_trading"}
_NETWORKS = {"testnet", "mainnet"}


class EntitlementJwtError(ValueError):
    """Raised when an entitlement JWT is invalid or expired."""


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_entitlement_jwt(token: str, secret: str, now_seconds: Optional[int] = None) -> dict[str, Any]:
    if not secret.strip():
        raise EntitlementJwtError("missing entitlement secret")
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise EntitlementJwtError("malformed jwt")

    header_part, payload_part, signature_part = parts
    expected = base64.urlsafe_b64encode(
        hmac.new(secret.encode("utf-8"), f"{header_part}.{payload_part}".encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    if not hmac.compare_digest(signature_part, expected):
        raise EntitlementJwtError("invalid signature")

    header = json.loads(_b64url_decode(header_part))
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise EntitlementJwtError("unsupported jwt header")

    snapshot = json.loads(_b64url_decode(payload_part))
    _validate_snapshot(snapshot)
    now = int(time.time() if now_seconds is None else now_seconds)
    if int(snapshot["exp"]) <= now:
        raise EntitlementJwtError("entitlement expired")
    return snapshot


def snapshot_to_runner_env(snapshot: dict[str, Any]) -> dict[str, str]:
    limits = snapshot["limits"]
    env = {
        "NUNCHI_TRADING_PERMISSION_TIER": str(snapshot["tier"]),
        "NUNCHI_TRADING_NETWORK": str(snapshot["network"]),
        "NUNCHI_ALLOW_MAINNET": str(bool(limits.get("allow_mainnet"))).lower(),
        "NUNCHI_MAX_ORDER_SIZE": str(limits.get("max_order_size")),
        "NUNCHI_MAX_STRATEGY_TICKS": str(limits.get("max_strategy_ticks")),
        "NUNCHI_REQUIRE_CONFIRMATION": str(bool(limits.get("require_confirmation"))).lower(),
    }
    wallet_address = snapshot.get("wallet_address")
    if wallet_address:
        env["NUNCHI_WEB_AUTH_ADDRESS"] = str(wallet_address)
    return env


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("v") != 1:
        raise EntitlementJwtError("unsupported snapshot version")
    if not snapshot.get("sub"):
        raise EntitlementJwtError("missing sub")
    if snapshot.get("tier") not in _TIERS:
        raise EntitlementJwtError("invalid tier")
    if snapshot.get("network") not in _NETWORKS:
        raise EntitlementJwtError("invalid network")
    tools = snapshot.get("tools")
    if not isinstance(tools, list) or not tools:
        raise EntitlementJwtError("invalid tools")
    if not isinstance(snapshot.get("limits"), dict):
        raise EntitlementJwtError("invalid limits")
    if not isinstance(snapshot.get("iat"), int) or not isinstance(snapshot.get("exp"), int):
        raise EntitlementJwtError("invalid expiry")
