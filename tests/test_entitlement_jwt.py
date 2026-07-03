import base64
import hashlib
import hmac
import json
import time

import pytest

from cli.entitlement_jwt import EntitlementJwtError, snapshot_to_runner_env, verify_entitlement_jwt


def _sign(snapshot: dict, secret: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(snapshot).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def test_verify_entitlement_jwt_roundtrip():
    secret = "test-entitlement-secret"
    snapshot = {
        "v": 1,
        "sub": "0xabc",
        "tier": "testnet_trading",
        "network": "testnet",
        "tools": ["account", "trade"],
        "limits": {
            "max_order_size": 0.5,
            "max_strategy_ticks": 60,
            "require_confirmation": True,
            "allow_mainnet": False,
        },
        "wallet_address": "0xabc",
        "iat": 1_700_000_000,
        "exp": 1_700_003_600,
    }
    token = _sign(snapshot, secret)
    verified = verify_entitlement_jwt(token, secret, now_seconds=1_700_000_100)
    assert verified["sub"] == "0xabc"
    assert snapshot_to_runner_env(verified)["NUNCHI_MAX_ORDER_SIZE"] == "0.5"


def test_verify_entitlement_jwt_rejects_expired():
    secret = "test-entitlement-secret"
    snapshot = {
        "v": 1,
        "sub": "user",
        "tier": "read_only",
        "network": "testnet",
        "tools": ["account"],
        "limits": {
            "max_order_size": 1,
            "max_strategy_ticks": 120,
            "require_confirmation": False,
            "allow_mainnet": False,
        },
        "iat": 1_700_000_000,
        "exp": 1_700_000_060,
    }
    token = _sign(snapshot, secret)
    with pytest.raises(EntitlementJwtError, match="expired"):
        verify_entitlement_jwt(token, secret, now_seconds=1_700_010_000)
