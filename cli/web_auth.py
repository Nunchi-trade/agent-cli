"""CLI <-> web-auth pairing, EIP-712 signing, and transaction relay client."""
from __future__ import annotations

import json
import os
import secrets
import time
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import requests

from cli.config import (
    DEFAULT_PAIR_API_URL,
    DEFAULT_PAIR_AUTHORIZE_URL,
    DEFAULT_PAIR_WALLET_URL,
)


AUTHORIZE_URL = os.environ.get(
    "HL_WEB_AUTH_AUTHORIZE_URL",
    os.environ.get("VITE_PAIR_AUTHORIZE_URL", DEFAULT_PAIR_AUTHORIZE_URL),
)
PAIR_API_BASE = os.environ.get(
    "HL_WEB_AUTH_API_URL",
    os.environ.get("VITE_PAIR_API_URL", DEFAULT_PAIR_API_URL),
)
WALLET_AUTH_URL = os.environ.get(
    "HL_WEB_AUTH_WALLET_URL",
    os.environ.get("VITE_PAIR_WALLET_URL", DEFAULT_PAIR_WALLET_URL),
)
STORAGE_PATH = Path(os.environ.get("HL_WEB_AUTH_PAIRING_PATH", "~/.hl-agent/pairing.json")).expanduser()

PAIRING_MAX_AGE_S = 28 * 24 * 3600
POLL_INTERVAL_S = 2
PAIR_TIMEOUT_S = 5 * 60
SIGN_TIMEOUT_S = 4 * 60
TRANSACTION_TIMEOUT_S = 4 * 60


@dataclass
class PairingResult:
    """Persisted pairing state for web-auth."""

    token: str
    addresses: list[str]
    label: str
    paired_at_ms: int
    selected_address: Optional[str] = None
    account_id: Optional[str] = None
    master_address: Optional[str] = None
    active_session: Optional[dict[str, Any]] = None
    agent_wallet_binding: Optional[dict[str, Any]] = None
    role_addresses: Optional[dict[str, str]] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    runtime_location: str = "local"
    connection_mode: str = "clone-local"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "PairingResult":
        return cls(
            token=raw["token"],
            addresses=list(raw["addresses"]),
            label=raw.get("label", ""),
            paired_at_ms=int(raw["paired_at_ms"]),
            selected_address=raw.get("selected_address") or raw.get("selectedAddress"),
            account_id=raw.get("account_id") or raw.get("accountId"),
            master_address=raw.get("master_address") or raw.get("masterAddress"),
            active_session=raw.get("active_session") or raw.get("activeSession"),
            agent_wallet_binding=raw.get("agent_wallet_binding") or raw.get("agentWalletBinding"),
            role_addresses=raw.get("role_addresses") or raw.get("roleAddresses") or {},
            agent_id=raw.get("agent_id") or raw.get("agentId"),
            agent_name=raw.get("agent_name") or raw.get("agentName"),
            runtime_location=raw.get("runtime_location") or raw.get("runtimeLocation") or "local",
            connection_mode=raw.get("connection_mode") or raw.get("connectionMode") or "clone-local",
        )

    @property
    def selected_or_master_address(self) -> str:
        selected = self.selected_address or self.master_address
        if selected:
            return selected
        if not self.addresses:
            raise PairingInvalidError("pairing has no addresses")
        return self.addresses[0]


class PairingMissingError(Exception):
    """No pairing stored."""

    def __init__(self) -> None:
        super().__init__("No paired wallet. Run `hl pair connect` to link one.")


class PairingInvalidError(Exception):
    """web-auth rejected or cannot use the stored pair token."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Pairing invalid: {reason}. Re-pair via `hl pair connect`.")


class SignRejectedError(Exception):
    """User rejected an EIP-712 signing request."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"User rejected signing: {reason}")


class SignTimedOutError(Exception):
    """User did not approve an EIP-712 signing request in time."""

    def __init__(self) -> None:
        super().__init__("Signing request timed out.")


class PairingTimedOutError(Exception):
    """User did not complete pairing in time."""

    def __init__(self) -> None:
        super().__init__("Pairing timed out.")


class TransactionRejectedError(Exception):
    """User rejected an EVM transaction request."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"User rejected transaction: {reason}")


class TransactionTimedOutError(Exception):
    """User did not send an EVM transaction in time."""

    def __init__(self) -> None:
        super().__init__("Transaction request timed out.")


def get_stored_pairing() -> Optional[PairingResult]:
    """Return the persisted pairing, or None if missing, malformed, or stale."""
    if not STORAGE_PATH.exists():
        return None
    try:
        raw = json.loads(STORAGE_PATH.read_text("utf-8"))
        result = PairingResult.from_json(raw)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError):
        return None

    age_s = (time.time() * 1000 - result.paired_at_ms) / 1000
    if age_s > PAIRING_MAX_AGE_S:
        try:
            STORAGE_PATH.unlink()
        except OSError:
            pass
        return None
    return result


def require_pairing() -> PairingResult:
    pairing = get_stored_pairing()
    if pairing is None:
        raise PairingMissingError()
    return pairing


def get_selected_pairing_address() -> str:
    return require_pairing().selected_or_master_address


def _persist(result: PairingResult) -> None:
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORAGE_PATH.write_text(json.dumps(result.to_json(), indent=2) + "\n", "utf-8")
    try:
        STORAGE_PATH.chmod(0o600)
    except OSError:
        pass


def select_pairing_address(address_or_index: str) -> PairingResult:
    pairing = require_pairing()
    selected = None
    if address_or_index.isdigit():
        idx = int(address_or_index)
        if 0 <= idx < len(pairing.addresses):
            selected = pairing.addresses[idx]
    if selected is None:
        selected = next((addr for addr in pairing.addresses if addr.lower() == address_or_index.lower()), None)
    if selected is None:
        raise ValueError(f"wallet {address_or_index!r} is not in the current pairing")
    pairing.selected_address = selected
    _persist(pairing)
    return pairing


def clear_pairing() -> None:
    token: Optional[str] = None
    if STORAGE_PATH.exists():
        try:
            token = json.loads(STORAGE_PATH.read_text("utf-8")).get("token")
        except (json.JSONDecodeError, OSError):
            pass
        try:
            STORAGE_PATH.unlink()
        except OSError:
            pass

    if token:
        try:
            requests.post(f"{PAIR_API_BASE}/api/pair/revoke", json={"token": token}, timeout=5)
        except requests.RequestException:
            pass


def _auth_headers(pairing: PairingResult) -> dict[str, str]:
    return {"Authorization": f"Bearer {pairing.token}", "Accept": "application/json"}


def normalize_connection_mode(value: Optional[str]) -> str:
    if value in {"clone-local", "hosted-mcp-tools", "hosted-mcp-tools-inference"}:
        return value
    return "clone-local"


def open_wallet_ui(
    *,
    no_browser: bool = False,
    account_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    runtime_location: str = "local",
    connection_mode: str = "clone-local",
    include_pair_token: bool = False,
) -> str:
    pairing = get_stored_pairing()
    if include_pair_token and pairing is None:
        raise PairingMissingError()
    params: list[tuple[str, str]] = []
    if account_id or agent_id:
        params.append(("view", "agent-wallets"))
        if account_id:
            params.append(("accountId", account_id))
        if agent_id:
            params.append(("agentId", agent_id))
        if agent_name:
            params.append(("agentName", agent_name))
        params.append(("runtimeLocation", runtime_location or "local"))
        params.append(("connectionMode", normalize_connection_mode(connection_mode)))
        if include_pair_token and pairing is not None:
            params.append(("pairToken", pairing.token))
    separator = "&" if "?" in WALLET_AUTH_URL else "?"
    url = f"{WALLET_AUTH_URL}{separator}{urlencode(params)}" if params else WALLET_AUTH_URL
    _open_browser(url, no_browser=no_browser)
    return url


def fetch_agent_wallet_binding(account_id: str, agent_id: str) -> dict[str, Any]:
    pairing = require_pairing()
    resp = requests.get(
        f"{PAIR_API_BASE}/api/agent-wallets/binding",
        params={"accountId": account_id, "agentId": agent_id},
        headers=_auth_headers(pairing),
        timeout=10,
    )
    if resp.status_code == 401:
        raise PairingInvalidError("token rejected by web-auth (401)")
    if not resp.ok:
        raise RuntimeError(f"/api/agent-wallets/binding returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def wait_for_agent_wallet_binding(
    *,
    account_id: str,
    agent_id: str,
    role: str,
    timeout_s: int = PAIR_TIMEOUT_S,
    on_polling: Optional[Callable[[], None]] = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = fetch_agent_wallet_binding(account_id, agent_id)
        binding = body.get("binding") if body.get("bound") else None
        address = binding.get("walletAddress") if isinstance(binding, dict) else None
        if address:
            pairing = require_pairing()
            role_addresses = dict(pairing.role_addresses or {})
            role_addresses[role] = address
            pairing.role_addresses = role_addresses
            pairing.agent_wallet_binding = binding
            _persist(pairing)
            return binding
        if on_polling:
            on_polling()
        time.sleep(POLL_INTERVAL_S)
    raise PairingTimedOutError()


def fetch_pending_scoped_requests() -> list[dict[str, Any]]:
    pairing = require_pairing()
    resp = requests.get(
        f"{PAIR_API_BASE}/api/sign/pending-scoped",
        headers=_auth_headers(pairing),
        timeout=10,
    )
    if resp.status_code == 401:
        raise PairingInvalidError("token rejected by web-auth (401)")
    if not resp.ok:
        raise RuntimeError(f"/api/sign/pending-scoped returned {resp.status_code}: {resp.text[:200]}")
    return list((resp.json() or {}).get("pending") or [])


def approve_scoped_request(request_id: str, approval: str = "approve") -> dict[str, Any]:
    pairing = require_pairing()
    resp = requests.post(
        f"{PAIR_API_BASE}/api/sign/approve-scoped",
        headers=_auth_headers(pairing),
        json={"request_id": request_id, "approval": approval},
        timeout=15,
    )
    if resp.status_code == 401:
        raise PairingInvalidError("token rejected by web-auth (401)")
    if not resp.ok:
        raise RuntimeError(f"/api/sign/approve-scoped returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _random_code() -> str:
    return secrets.token_urlsafe(24).rstrip("=")


def _random_request_id() -> str:
    return secrets.token_urlsafe(16).rstrip("=")


def _open_browser(url: str, no_browser: bool = False) -> None:
    if no_browser:
        return
    try:
        webbrowser.open(url, new=2, autoraise=True)
    except webbrowser.Error:
        pass


def start_pairing(
    app_name: str = "HL Agent CLI",
    deep_link: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    connection_mode: str = "clone-local",
    on_polling: Optional[Callable[[], None]] = None,
    no_browser: bool = False,
    on_url: Optional[Callable[[str], None]] = None,
    timeout_s: int = PAIR_TIMEOUT_S,
) -> PairingResult:
    """Start the browser pairing handshake and persist the claimed token."""
    code = _random_code()
    params: list[tuple[str, str]] = [("code", code), ("app", app_name)]
    if deep_link:
        params.append(("redirect", deep_link))
    if agent_id:
        params.append(("agentId", agent_id))
    if agent_name:
        params.append(("agentName", agent_name))
    params.append(("runtimeLocation", "local"))
    params.append(("connectionMode", normalize_connection_mode(connection_mode)))
    url = f"{AUTHORIZE_URL}?{urlencode(params)}"

    if on_url:
        on_url(url)
    _open_browser(url, no_browser=no_browser)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{PAIR_API_BASE}/api/pair/{code}", timeout=10)
        except requests.RequestException:
            if on_polling:
                on_polling()
            time.sleep(POLL_INTERVAL_S)
            continue

        if resp.status_code == 404:
            if on_polling:
                on_polling()
            time.sleep(POLL_INTERVAL_S)
            continue
        if not resp.ok:
            raise RuntimeError(f"pair server returned {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        if body.get("status") != "claimed":
            if on_polling:
                on_polling()
            time.sleep(POLL_INTERVAL_S)
            continue

        result = PairingResult(
            token=body["token"],
            addresses=list(body["addresses"]),
            label=body.get("label", ""),
            paired_at_ms=int(time.time() * 1000),
            account_id=body.get("accountId"),
            master_address=body.get("masterAddress"),
            active_session=body.get("activeSession"),
            agent_wallet_binding=body.get("agentWalletBinding"),
            agent_id=body.get("agentId") or agent_id,
            agent_name=body.get("agentName") or agent_name or app_name,
            runtime_location=body.get("runtimeLocation") or "local",
            connection_mode=body.get("connectionMode") or normalize_connection_mode(connection_mode),
        )
        _persist(result)
        return result

    raise PairingTimedOutError()


def verify_pairing() -> Optional[dict[str, Any]]:
    """Best-effort pair-token introspection."""
    pairing = get_stored_pairing()
    if pairing is None:
        return None
    try:
        resp = requests.get(
            f"{PAIR_API_BASE}/api/pair/verify",
            headers={"Authorization": f"Bearer {pairing.token}"},
            timeout=5,
        )
    except requests.RequestException:
        return None
    if resp.status_code == 401:
        raise PairingInvalidError("token rejected by web-auth (401)")
    if not resp.ok:
        return None
    return resp.json()


def register_agent(
    *,
    account_id: Optional[str] = None,
    agent_id: str,
    agent_name: Optional[str] = None,
    connection_mode: str = "clone-local",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    pairing = require_pairing()
    resolved_account_id = account_id or pairing.account_id
    if not resolved_account_id:
        raise PairingInvalidError("pairing does not include an account id")
    mode = normalize_connection_mode(connection_mode)
    name = agent_name or pairing.agent_name or agent_id
    record: dict[str, Any] = {
        "agentId": agent_id,
        "agent_id": agent_id,
        "agentName": name,
        "name": name,
        "runtimeLocation": "local",
        "runtime_location": "local",
        "connectionMode": mode,
        "connection_mode": mode,
        "accountId": resolved_account_id,
        "account_id": resolved_account_id,
    }
    if extra:
        record.update(extra)
    resp = requests.post(
        f"{PAIR_API_BASE}/api/agents/register",
        headers={**_auth_headers(pairing), "Content-Type": "application/json"},
        json={"accountId": resolved_account_id, "agentId": agent_id, "agent": record},
        timeout=15,
    )
    if resp.status_code == 401:
        raise PairingInvalidError("token rejected by web-auth (401)")
    if not resp.ok:
        raise RuntimeError(f"/api/agents/register returned {resp.status_code}: {resp.text[:200]}")
    pairing.agent_id = agent_id
    pairing.agent_name = name
    pairing.runtime_location = "local"
    pairing.connection_mode = mode
    _persist(pairing)
    return resp.json()


def sign_with_pair(
    typed_data: dict[str, Any],
    summary: str = "",
    timeout_s: int = SIGN_TIMEOUT_S,
    on_awaiting: Optional[Callable[[], None]] = None,
    scope: Optional[dict[str, Any]] = None,
) -> str:
    """Submit EIP-712 typed data to the paired wallet and return the hex signature."""
    pairing = require_pairing()
    request_id = _random_request_id()
    payload: dict[str, Any] = {
        "token": pairing.token,
        "request_id": request_id,
        "typed_data": typed_data,
        "summary": summary,
    }
    if scope is not None:
        payload["scope"] = scope

    submit = requests.post(f"{PAIR_API_BASE}/api/sign", json=payload, timeout=15)
    if submit.status_code == 401:
        raise PairingInvalidError("token rejected by web-auth (401)")
    if not submit.ok:
        raise RuntimeError(f"/api/sign returned {submit.status_code}: {submit.text[:200]}")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if on_awaiting:
            on_awaiting()
        time.sleep(POLL_INTERVAL_S)
        try:
            poll = requests.get(f"{PAIR_API_BASE}/api/sign/{request_id}", timeout=10)
        except requests.RequestException:
            continue
        if not poll.ok and poll.status_code != 404:
            raise RuntimeError(f"poll error {poll.status_code}: {poll.text[:200]}")
        body = poll.json() if poll.content else {}
        status = body.get("status")
        if status == "signed" and body.get("signature"):
            return body["signature"]
        if status == "rejected":
            raise SignRejectedError(body.get("reason", "user_rejected"))
        if status == "error":
            raise RuntimeError(body.get("error", "sign relay returned error"))
        if status == "unknown_or_expired":
            raise SignTimedOutError()

    raise SignTimedOutError()


def submit_transaction(
    transaction: dict[str, Any],
    summary: str = "",
    timeout_s: int = TRANSACTION_TIMEOUT_S,
    on_awaiting: Optional[Callable[[], None]] = None,
) -> str:
    """Submit an EVM transaction request and return the broadcast tx hash."""
    pairing = require_pairing()
    request_id = _random_request_id()
    submit = requests.post(
        f"{PAIR_API_BASE}/api/transaction",
        json={
            "token": pairing.token,
            "request_id": request_id,
            "transaction": transaction,
            "summary": summary,
        },
        timeout=15,
    )
    if submit.status_code == 401:
        raise PairingInvalidError("token rejected by web-auth (401)")
    if not submit.ok:
        raise RuntimeError(f"/api/transaction returned {submit.status_code}: {submit.text[:200]}")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if on_awaiting:
            on_awaiting()
        time.sleep(POLL_INTERVAL_S)
        try:
            poll = requests.get(f"{PAIR_API_BASE}/api/transaction/{request_id}", timeout=10)
        except requests.RequestException:
            continue
        if not poll.ok and poll.status_code != 404:
            raise RuntimeError(f"poll error {poll.status_code}: {poll.text[:200]}")
        body = poll.json() if poll.content else {}
        status = body.get("status")
        if status == "sent" and body.get("tx_hash"):
            return body["tx_hash"]
        if status == "rejected":
            raise TransactionRejectedError(body.get("reason", "user_rejected"))
        if status == "error":
            raise RuntimeError(body.get("error", "transaction relay returned error"))
        if status == "unknown_or_expired":
            raise TransactionTimedOutError()

    raise TransactionTimedOutError()


def fetch_health() -> Optional[dict[str, Any]]:
    try:
        resp = requests.get(f"{PAIR_API_BASE}/api/health", timeout=5)
        if resp.ok:
            return resp.json()
    except requests.RequestException:
        pass
    return None
