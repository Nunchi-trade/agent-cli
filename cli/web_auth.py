"""Web-auth signing client for hosted/keyless agent-cli runners.

This is the runner-side slice of the existing Nunchi web-auth flow. It lets
agent-cli submit Hyperliquid EIP-712 payloads to a user-approved pairing token
instead of loading a server-side private key.
"""
from __future__ import annotations

import os
import secrets
import time
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests

PAIR_API_BASE = os.environ.get("VITE_PAIR_API_URL", "https://web-auth-opal.vercel.app")
POLL_INTERVAL_S = 2
SIGN_TIMEOUT_S = 4 * 60

PAIR_TOKEN_ENV = "NUNCHI_WEB_AUTH_PAIR_TOKEN"
PAIR_ADDRESS_ENV = "NUNCHI_WEB_AUTH_ADDRESS"
SCOPED_TOKEN_PATH_ENV = "NUNCHI_SCOPED_TOKEN_PATH"


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


@dataclass(frozen=True)
class ScopedToken:
    token: str
    address: str
    account_id: str = ""
    permission_tier: str = "testnet_trading"
    network: str = "testnet"
    allow_mainnet: bool = False
    max_order_size: Optional[float] = None
    max_hedge_notional: Optional[float] = None
    max_strategy_ticks: Optional[int] = None
    require_confirmation: bool = True
    created_at_ms: int = 0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ScopedToken":
        def _bool(value: Any, default: bool = False) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            token=str(raw["token"]),
            address=str(raw["address"]),
            account_id=str(raw.get("account_id", "")),
            permission_tier=str(raw.get("permission_tier", "testnet_trading")),
            network=str(raw.get("network", "testnet")),
            allow_mainnet=_bool(raw.get("allow_mainnet"), False),
            max_order_size=(
                float(raw["max_order_size"])
                if raw.get("max_order_size") not in (None, "")
                else None
            ),
            max_hedge_notional=(
                float(raw["max_hedge_notional"])
                if raw.get("max_hedge_notional") not in (None, "")
                else None
            ),
            max_strategy_ticks=(
                int(raw["max_strategy_ticks"])
                if raw.get("max_strategy_ticks") not in (None, "")
                else None
            ),
            require_confirmation=_bool(raw.get("require_confirmation"), True),
            created_at_ms=int(raw.get("created_at_ms") or int(time.time() * 1000)),
        )

    def to_pairing(self) -> WebAuthPairing:
        return WebAuthPairing(token=self.token, address=self.address, account_id=self.account_id)

    def to_env(self) -> dict[str, str]:
        env = {
            PAIR_TOKEN_ENV: self.token,
            PAIR_ADDRESS_ENV: self.address,
            "NUNCHI_TRADING_PERMISSION_TIER": self.permission_tier,
            "NUNCHI_TRADING_NETWORK": self.network,
            "NUNCHI_ALLOW_MAINNET": "true" if self.allow_mainnet else "false",
            "NUNCHI_REQUIRE_CONFIRMATION": "true" if self.require_confirmation else "false",
        }
        if self.account_id:
            env["NUNCHI_ACCOUNT_ID"] = self.account_id
        if self.max_order_size is not None:
            env["NUNCHI_MAX_ORDER_SIZE"] = str(self.max_order_size)
        if self.max_hedge_notional is not None:
            env["NUNCHI_MAX_HEDGE_NOTIONAL"] = str(self.max_hedge_notional)
        if self.max_strategy_ticks is not None:
            env["NUNCHI_MAX_STRATEGY_TICKS"] = str(self.max_strategy_ticks)
        return env


def scoped_token_path() -> Path:
    return Path(os.environ.get(SCOPED_TOKEN_PATH_ENV, "~/.hl-agent/scoped-token.json")).expanduser()


def load_scoped_token() -> Optional[ScopedToken]:
    path = scoped_token_path()
    if not path.exists():
        return None
    try:
        return ScopedToken.from_json(json.loads(path.read_text("utf-8")))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_scoped_token(token: ScopedToken) -> Path:
    path = scoped_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token.to_json(), indent=2) + "\n", "utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def clear_scoped_token() -> None:
    try:
        scoped_token_path().unlink()
    except FileNotFoundError:
        pass


def scoped_token_env() -> dict[str, str]:
    token = load_scoped_token()
    return token.to_env() if token is not None else {}


def pairing_from_env() -> Optional[WebAuthPairing]:
    token = os.environ.get(PAIR_TOKEN_ENV, "").strip()
    address = (
        os.environ.get(PAIR_ADDRESS_ENV, "").strip()
        or os.environ.get("HL_WALLET_ADDRESS", "").strip()
        or os.environ.get("HL_VIEW_AS_USER", "").strip()
    )
    account_id = os.environ.get("NUNCHI_ACCOUNT_ID", "").strip()
    if not token or not address:
        scoped = load_scoped_token()
        return scoped.to_pairing() if scoped is not None else None
    return WebAuthPairing(token=token, address=address, account_id=account_id)


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
    timeout_s: int = SIGN_TIMEOUT_S,
    on_awaiting: Optional[Callable[[], None]] = None,
) -> str:
    request_id = secrets.token_urlsafe(16).rstrip("=")
    submit = requests.post(
        f"{PAIR_API_BASE.rstrip('/')}/api/sign",
        json={
            "token": token,
            "request_id": request_id,
            "typed_data": typed_data,
            "summary": summary,
        },
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

    def sign_typed_data(self, typed_data: dict[str, Any]) -> dict[str, Any]:
        signature = sign_typed_data_with_pair(
            typed_data,
            token=self.pairing.token,
            summary=f"Nunchi trading action for {self.address}",
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
