"""Minimal client for the existing web-auth signing and transaction relay."""
from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from cli.config import DEFAULT_PAIRING_PATH, NUNCHI_PAIRING_PATH, TradingConfig


POLL_INTERVAL_S = 2
SIGN_TIMEOUT_S = 4 * 60
TRANSACTION_TIMEOUT_S = 4 * 60


@dataclass
class PairingResult:
    token: str
    addresses: list[str]
    selected_address: Optional[str] = None
    master_address: Optional[str] = None
    label: str = ""

    @property
    def selected_or_master_address(self) -> str:
        selected = self.selected_address or self.master_address
        if selected:
            return selected
        if self.addresses:
            return self.addresses[0]
        raise PairingMissingError("No paired wallet address found. Set HL_WEB_AUTH_ADDRESS.")


class PairingMissingError(Exception):
    """No usable web-auth pairing token was found."""


class PairingInvalidError(Exception):
    """web-auth rejected the local pairing token."""


class SignRejectedError(Exception):
    """User rejected a web-auth signature request."""


class SignTimedOutError(Exception):
    """A web-auth signature request expired."""


class TransactionRejectedError(Exception):
    """User rejected a web-auth transaction request."""


class TransactionTimedOutError(Exception):
    """A web-auth transaction request expired."""


def pair_api_base() -> str:
    return TradingConfig().web_auth_api_url.rstrip("/")


def _pairing_paths() -> list[Path]:
    raw = os.environ.get("HL_WEB_AUTH_PAIRING_PATH")
    paths = [Path(raw).expanduser()] if raw else []
    paths.extend([Path(DEFAULT_PAIRING_PATH).expanduser(), Path(NUNCHI_PAIRING_PATH).expanduser()])
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def get_stored_pairing() -> Optional[PairingResult]:
    env_token = os.environ.get("HL_WEB_AUTH_PAIR_TOKEN")
    if env_token:
        address = os.environ.get("HL_WEB_AUTH_ADDRESS")
        return PairingResult(
            token=env_token,
            addresses=[address] if address else [],
            selected_address=address,
            master_address=address,
            label="env",
        )

    for path in _pairing_paths():
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        token = raw.get("token")
        if not token:
            continue
        return PairingResult(
            token=token,
            addresses=list(raw.get("addresses") or []),
            selected_address=raw.get("selected_address") or raw.get("selectedAddress"),
            master_address=raw.get("master_address") or raw.get("masterAddress"),
            label=raw.get("label", ""),
        )
    return None


def require_pairing() -> PairingResult:
    pairing = get_stored_pairing()
    if pairing is None:
        raise PairingMissingError(
            "No web-auth pairing found. Set HL_WEB_AUTH_PAIR_TOKEN or pair with web-auth/nunchi-cli first."
        )
    return pairing


def selected_wallet_address() -> str:
    return require_pairing().selected_or_master_address


def _request_id() -> str:
    return secrets.token_urlsafe(16).rstrip("=")


def sign_with_pair(
    typed_data: dict[str, Any],
    summary: str = "",
    timeout_s: int = SIGN_TIMEOUT_S,
    on_awaiting: Optional[Callable[[], None]] = None,
    scope: Optional[dict[str, Any]] = None,
) -> str:
    """Submit EIP-712 typed data to web-auth and return a hex signature."""
    pairing = require_pairing()
    request_id = _request_id()
    payload: dict[str, Any] = {
        "token": pairing.token,
        "request_id": request_id,
        "typed_data": typed_data,
        "summary": summary,
    }
    if scope:
        payload["scope"] = scope

    submit = requests.post(f"{pair_api_base()}/api/sign", json=payload, timeout=15)
    if submit.status_code == 401:
        raise PairingInvalidError("web-auth rejected the pair token")
    if not submit.ok:
        raise RuntimeError(f"/api/sign returned {submit.status_code}: {submit.text[:200]}")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if on_awaiting:
            on_awaiting()
        time.sleep(POLL_INTERVAL_S)
        try:
            poll = requests.get(f"{pair_api_base()}/api/sign/{request_id}", timeout=10)
        except requests.RequestException:
            continue
        if not poll.ok and poll.status_code != 404:
            raise RuntimeError(f"sign poll returned {poll.status_code}: {poll.text[:200]}")
        body = poll.json() if poll.content else {}
        status = body.get("status")
        if status == "signed" and body.get("signature"):
            return body["signature"]
        if status == "rejected":
            raise SignRejectedError(body.get("reason", "user_rejected"))
        if status == "error":
            raise RuntimeError(body.get("error", "sign relay returned error"))
        if status == "unknown_or_expired":
            raise SignTimedOutError("web-auth sign request expired")

    raise SignTimedOutError("web-auth sign request timed out")


def submit_transaction(
    transaction: dict[str, Any],
    summary: str = "",
    timeout_s: int = TRANSACTION_TIMEOUT_S,
    on_awaiting: Optional[Callable[[], None]] = None,
) -> str:
    """Submit an EVM transaction through web-auth and return the tx hash."""
    pairing = require_pairing()
    request_id = _request_id()
    submit = requests.post(
        f"{pair_api_base()}/api/transaction",
        json={
            "token": pairing.token,
            "request_id": request_id,
            "transaction": transaction,
            "summary": summary,
        },
        timeout=15,
    )
    if submit.status_code == 401:
        raise PairingInvalidError("web-auth rejected the pair token")
    if not submit.ok:
        raise RuntimeError(f"/api/transaction returned {submit.status_code}: {submit.text[:200]}")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if on_awaiting:
            on_awaiting()
        time.sleep(POLL_INTERVAL_S)
        try:
            poll = requests.get(f"{pair_api_base()}/api/transaction/{request_id}", timeout=10)
        except requests.RequestException:
            continue
        if not poll.ok and poll.status_code != 404:
            raise RuntimeError(f"transaction poll returned {poll.status_code}: {poll.text[:200]}")
        body = poll.json() if poll.content else {}
        status = body.get("status")
        if status == "sent" and body.get("tx_hash"):
            return body["tx_hash"]
        if status == "rejected":
            raise TransactionRejectedError(body.get("reason", "user_rejected"))
        if status == "error":
            raise RuntimeError(body.get("error", "transaction relay returned error"))
        if status == "unknown_or_expired":
            raise TransactionTimedOutError("web-auth transaction request expired")

    raise TransactionTimedOutError("web-auth transaction request timed out")
