"""Web-auth signing client for hosted/keyless agent-cli runners.

This is the runner-side slice of the existing Nunchi web-auth flow. It lets
agent-cli submit Hyperliquid EIP-712 payloads to a user-approved pairing token
instead of loading a server-side private key.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import requests

PAIR_API_BASE = os.environ.get("VITE_PAIR_API_URL", "https://web-auth-opal.vercel.app")
POLL_INTERVAL_S = 2
SIGN_TIMEOUT_S = 4 * 60

PAIR_TOKEN_ENV = "NUNCHI_WEB_AUTH_PAIR_TOKEN"
PAIR_ADDRESS_ENV = "NUNCHI_WEB_AUTH_ADDRESS"
AGENT_WALLET_ADDRESS_ENV = "NUNCHI_AGENT_WALLET_ADDRESS"
ACCOUNT_ID_ENV = "NUNCHI_ACCOUNT_ID"
AGENT_ID_ENV = "NUNCHI_AGENT_ID"
MASTER_ADDRESS_ENV = "NUNCHI_MASTER_WALLET_ADDRESS"


class WebAuthMissingError(RuntimeError):
    """No web-auth pairing token/address was provided to the runner."""


class WebAuthRejectedError(RuntimeError):
    """The user rejected a signing request in web-auth."""


class WebAuthTimedOutError(RuntimeError):
    """The web-auth signing request timed out."""


@dataclass(frozen=True)
class WebAuthPairing:
    token: str
    address: str
    account_id: str = ""
    agent_id: str = ""
    master_address: str = ""


def pairing_from_env() -> Optional[WebAuthPairing]:
    token = os.environ.get(PAIR_TOKEN_ENV, "").strip()
    address = (
        os.environ.get(PAIR_ADDRESS_ENV, "").strip()
        or os.environ.get(AGENT_WALLET_ADDRESS_ENV, "").strip()
        or os.environ.get("HL_WALLET_ADDRESS", "").strip()
        or os.environ.get("HL_VIEW_AS_USER", "").strip()
    )
    account_id = os.environ.get(ACCOUNT_ID_ENV, "").strip()
    agent_id = os.environ.get(AGENT_ID_ENV, "").strip()
    master_address = os.environ.get(MASTER_ADDRESS_ENV, "").strip()
    if not token or not address:
        return None
    return WebAuthPairing(
        token=token,
        address=address,
        account_id=account_id,
        agent_id=agent_id,
        master_address=master_address,
    )


def require_pairing_from_env() -> WebAuthPairing:
    pairing = pairing_from_env()
    if pairing is None:
        raise WebAuthMissingError(
            f"Missing web-auth pairing. Set {PAIR_TOKEN_ENV} and {PAIR_ADDRESS_ENV} "
            "for keyless hosted signing."
        )
    return pairing


def sign_typed_data_with_pair(
    typed_data: dict[str, Any],
    *,
    token: str,
    summary: str = "",
    scope: Optional[dict[str, Any]] = None,
    timeout_s: int = SIGN_TIMEOUT_S,
    on_awaiting: Optional[Callable[[], None]] = None,
) -> str:
    request_id = secrets.token_urlsafe(16).rstrip("=")
    payload: dict[str, Any] = {
        "token": token,
        "request_id": request_id,
        "typed_data": typed_data,
        "summary": summary,
    }
    if scope is not None:
        payload["scope"] = scope
    submit = requests.post(
        f"{PAIR_API_BASE.rstrip('/')}/api/sign",
        json=payload,
        timeout=15,
    )
    if submit.status_code == 401:
        raise WebAuthMissingError("web-auth rejected the pairing token")
    if not submit.ok:
        raise RuntimeError(f"/api/sign returned {submit.status_code}: {submit.text[:200]}")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if on_awaiting:
            on_awaiting()
        time.sleep(POLL_INTERVAL_S)
        try:
            poll = requests.get(f"{PAIR_API_BASE.rstrip('/')}/api/sign/{request_id}", timeout=10)
        except requests.RequestException:
            continue
        if not poll.ok and poll.status_code != 404:
            raise RuntimeError(f"sign poll returned {poll.status_code}: {poll.text[:200]}")
        body = poll.json() if poll.content else {}
        status = body.get("status")
        if status == "signed" and body.get("signature"):
            return str(body["signature"])
        if status == "rejected":
            raise WebAuthRejectedError(str(body.get("reason", "user_rejected")))
        if status in ("error", "unknown_or_expired"):
            raise WebAuthTimedOutError()

    raise WebAuthTimedOutError()


def agent_wallet_binding(pairing: WebAuthPairing) -> Optional[dict[str, Any]]:
    """Fetch the saved web-auth binding for this hosted agent."""
    if not pairing.account_id or not pairing.agent_id:
        return None
    res = requests.get(
        f"{PAIR_API_BASE.rstrip('/')}/api/agent-wallets/binding",
        params={"accountId": pairing.account_id, "agentId": pairing.agent_id},
        headers={"authorization": f"Bearer {pairing.token}"},
        timeout=10,
    )
    if res.status_code == 404:
        return None
    if not res.ok:
        raise RuntimeError(f"/api/agent-wallets/binding returned {res.status_code}: {res.text[:200]}")
    body = res.json() if res.content else {}
    if not body.get("bound"):
        return None
    binding = body.get("binding")
    return binding if isinstance(binding, dict) else None


def _int_param(params: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(params.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def infer_sign_scope(typed_data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Describe the Hyperliquid action so web-auth can enforce session policy."""
    if typed_data.get("primaryType") == "NunchiSessionPolicy":
        return None
    primary_type = str(typed_data.get("primaryType") or "").lower()
    message = typed_data.get("message") if isinstance(typed_data.get("message"), dict) else {}
    action = message.get("action") if isinstance(message.get("action"), dict) else {}
    action_type = str(action.get("type") or message.get("type") or "").lower()
    haystack = f"{primary_type} {action_type}"
    if "cancel" in haystack:
        method = "hl.cancel"
    elif "approveagent" in haystack or "approve_agent" in haystack or "approve agent" in haystack:
        method = "hl.approveAgent"
    else:
        method = "hl.order"
    return {"method": method}


def session_policy_typed_data(pairing: WebAuthPairing, binding: dict[str, Any]) -> dict[str, Any]:
    """Build the policy request; web-auth overlays the latest saved params again."""
    params = binding.get("params") if isinstance(binding.get("params"), dict) else {}
    now = int(time.time())
    master = str(binding.get("masterAddress") or pairing.master_address or pairing.address)
    agent = str(binding.get("walletAddress") or pairing.address)
    expiry = _int_param(params, "expiry", now + 30 * 24 * 60 * 60)
    allowed_methods = params.get("allowedMethods")
    if not isinstance(allowed_methods, list) or not allowed_methods:
        allowed_methods = ["hl.order", "hl.cancel"]
    return {
        "domain": {
            "name": "Nunchi",
            "version": "1",
            "chainId": 421614 if _int_param(params, "network", 0) == 0 else 42161,
        },
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "NunchiSessionPolicy": [
                {"name": "master", "type": "address"},
                {"name": "agent", "type": "address"},
                {"name": "network", "type": "uint8"},
                {"name": "issuedAt", "type": "uint64"},
                {"name": "expiry", "type": "uint64"},
                {"name": "allowedMethods", "type": "string[]"},
                {"name": "spendLimitUsdc", "type": "uint256"},
                {"name": "maxPositionSizeUsdc", "type": "uint256"},
                {"name": "maxPositions", "type": "uint32"},
                {"name": "maxLeverageX100", "type": "uint32"},
                {"name": "maxDrawdownPctBps", "type": "uint32"},
                {"name": "stopLossPctBps", "type": "uint32"},
                {"name": "dailyLossLimitUsdc", "type": "uint256"},
                {"name": "maxImpactBps", "type": "uint32"},
                {"name": "planApprovalRequired", "type": "bool"},
                {"name": "allowedInstruments", "type": "bytes32[]"},
                {"name": "agentVersionHash", "type": "bytes32"},
                {"name": "revocable", "type": "bool"},
            ],
        },
        "primaryType": "NunchiSessionPolicy",
        "message": {
            "master": master,
            "agent": agent,
            "network": _int_param(params, "network", 0),
            "issuedAt": now,
            "expiry": expiry,
            "allowedMethods": [str(method) for method in allowed_methods],
            "spendLimitUsdc": _int_param(params, "spendLimitUsdc", 0),
            "maxPositionSizeUsdc": _int_param(params, "maxPositionSizeUsdc", 0),
            "maxPositions": _int_param(params, "maxPositions", 0),
            "maxLeverageX100": _int_param(params, "maxLeverageX100", 0),
            "maxDrawdownPctBps": _int_param(params, "maxDrawdownPctBps", 0),
            "stopLossPctBps": _int_param(params, "stopLossPctBps", 0),
            "dailyLossLimitUsdc": _int_param(params, "dailyLossLimitUsdc", 0),
            "maxImpactBps": _int_param(params, "maxImpactBps", 0),
            "planApprovalRequired": bool(params.get("planApprovalRequired", True)),
            "allowedInstruments": params.get("allowedInstruments") if isinstance(params.get("allowedInstruments"), list) else [],
            "agentVersionHash": str(params.get("agentVersionHash") or "0x" + ("00" * 32)),
            "revocable": params.get("revocable") is not False,
        },
    }


def split_signature(signature: str) -> dict[str, Any]:
    raw = signature[2:] if signature.startswith("0x") else signature
    if len(raw) != 130:
        raise ValueError("expected 65-byte hex signature")
    sig = bytes.fromhex(raw)
    v = sig[64]
    if v < 27:
        v += 27
    return {
        "r": "0x" + sig[:32].hex(),
        "s": "0x" + sig[32:64].hex(),
        "v": v,
    }


class WebAuthWallet:
    """Wallet-like object consumed by Hyperliquid's SDK signing helpers."""

    def __init__(self, pairing: WebAuthPairing):
        self.pairing = pairing
        self.address = pairing.address
        self._authorized_binding_updated_at: Optional[int] = None

    def ensure_session_policy(self) -> None:
        binding = agent_wallet_binding(self.pairing)
        if not binding:
            return
        updated_at = _int_param(binding, "updatedAt", 0)
        if updated_at > 0 and self._authorized_binding_updated_at == updated_at:
            return
        policy = session_policy_typed_data(self.pairing, binding)
        sign_typed_data_with_pair(
            policy,
            token=self.pairing.token,
            summary="Nunchi agent wallet policy update",
        )
        self._authorized_binding_updated_at = updated_at or int(time.time())

    def sign_typed_data(self, typed_data: dict[str, Any]) -> dict[str, Any]:
        if typed_data.get("primaryType") != "NunchiSessionPolicy":
            self.ensure_session_policy()
        signature = sign_typed_data_with_pair(
            typed_data,
            token=self.pairing.token,
            summary=f"Nunchi trading action for {self.address}",
            scope=infer_sign_scope(typed_data),
        )
        return split_signature(signature)


def install_hyperliquid_web_auth_signer() -> None:
    """Patch Hyperliquid's sign_inner to support WebAuthWallet.

    The SDK builds the exact typed-data payload inside `sign_inner`. Patching at
    that boundary lets us reuse all existing Exchange/order code while swapping
    only the signing mechanism.
    """
    import hyperliquid.utils.signing as signing

    if getattr(signing.sign_inner, "_nunchi_web_auth_patched", False):
        return

    original = signing.sign_inner

    def sign_inner(wallet: Any, data: dict[str, Any]) -> dict[str, Any]:
        if hasattr(wallet, "sign_typed_data"):
            return wallet.sign_typed_data(data)
        return original(wallet, data)

    sign_inner._nunchi_web_auth_patched = True  # type: ignore[attr-defined]
    signing.sign_inner = sign_inner
