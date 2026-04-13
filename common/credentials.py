"""Standardized key management — pluggable backends with unified resolution.

Backends:
  1. MacOSKeychainBackend  — macOS Keychain via `security` CLI
  2. EncryptedKeystoreBackend — geth-compatible Web3 Secret Storage
  3. RailwayEnvBackend — Railway-injected environment variables
  4. FlatFileBackend — plaintext files under the app home keys/ directory

Storage paths default to ~/.agent-cli for new installs, with transparent
fallback to the legacy ~/.hl-agent path for existing installs.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from common.app_paths import keys_dir as default_keys_dir
from common.app_paths import resolve_app_home

log = logging.getLogger("credentials")

KEYS_DIR = default_keys_dir()
APP_HOME = resolve_app_home()

VENUE_PRIVATE_KEY_ENV_VARS = {
    "hl": ["HL_PRIVATE_KEY", "HYPERLIQUID_PRIVATE_KEY"],
    "hyperliquid": ["HL_PRIVATE_KEY", "HYPERLIQUID_PRIVATE_KEY"],
    "paradex": ["PARADEX_PRIVATE_KEY", "PARADEX_L2_PRIVATE_KEY"],
}
GENERIC_PRIVATE_KEY_ENV_VARS = ["AGENT_PRIVATE_KEY"]

VENUE_ADDRESS_ENV_VARS = {
    "hl": ["HL_WALLET_ADDRESS", "HYPERLIQUID_WALLET_ADDRESS"],
    "hyperliquid": ["HL_WALLET_ADDRESS", "HYPERLIQUID_WALLET_ADDRESS"],
    "paradex": ["PARADEX_ADDRESS", "PARADEX_L2_ADDRESS"],
}
GENERIC_ADDRESS_ENV_VARS = ["AGENT_WALLET_ADDRESS"]


def normalize_venue_name(venue: str | None) -> str:
    normalized = (venue or "hl").strip().lower()
    aliases = {
        "hyperliquid": "hl",
        "hyper-liquid": "hl",
        "pdx": "paradex",
    }
    return aliases.get(normalized, normalized)


def private_key_env_vars_for_venue(venue: str = "hl") -> List[str]:
    normalized = normalize_venue_name(venue)
    specific = VENUE_PRIVATE_KEY_ENV_VARS.get(normalized, [f"{normalized.upper()}_PRIVATE_KEY"])
    env_vars: List[str] = []
    for env_var in [*specific, *GENERIC_PRIVATE_KEY_ENV_VARS]:
        if env_var not in env_vars:
            env_vars.append(env_var)
    return env_vars


def address_env_vars_for_venue(venue: str = "hl") -> List[str]:
    normalized = normalize_venue_name(venue)
    specific = VENUE_ADDRESS_ENV_VARS.get(normalized, [f"{normalized.upper()}_ADDRESS"])
    env_vars: List[str] = []
    for env_var in [*specific, *GENERIC_ADDRESS_ENV_VARS]:
        if env_var not in env_vars:
            env_vars.append(env_var)
    return env_vars


def resolve_wallet_address(venue: str = "hl", address: Optional[str] = None) -> str:
    """Resolve a wallet/account address from arg or venue-specific env vars."""
    candidates = [address] if address else []
    candidates.extend(os.environ.get(env_var, "") for env_var in address_env_vars_for_venue(venue))

    normalized_venue = normalize_venue_name(venue)
    pattern = r"0x[0-9a-fA-F]{40,64}" if normalized_venue == "paradex" else r"0x[0-9a-fA-F]{40}"

    for candidate in candidates:
        addr = (candidate or "").strip()
        if not addr:
            continue
        if re.fullmatch(pattern, addr):
            return addr
        log.warning("Ignoring invalid %s address candidate: %s", normalized_venue, addr)
    return ""


class KeystoreBackend(ABC):
    """Abstract base class for private key storage backends."""

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def get_key(self, address: Optional[str] = None, venue: str = "hl") -> Optional[str]:
        ...

    @abstractmethod
    def store_key(self, address: str, private_key: str) -> None:
        ...

    @abstractmethod
    def list_keys(self) -> List[str]:
        ...

    @abstractmethod
    def available(self) -> bool:
        ...


class EncryptedKeystoreBackend(KeystoreBackend):
    """Wraps cli/keystore.py — geth-compatible Web3 Secret Storage."""

    def name(self) -> str:
        return "keystore"

    def get_key(self, address: Optional[str] = None, venue: str = "hl") -> Optional[str]:
        from cli.keystore import get_keystore_key, get_keystore_key_for_address

        if address:
            return get_keystore_key_for_address(address)
        return get_keystore_key()

    def store_key(self, address: str, private_key: str) -> None:
        from cli.keystore import create_keystore, _resolve_password

        password = _resolve_password()
        if not password:
            raise RuntimeError(
                "No keystore password available. Set AGENT_CLI_KEYSTORE_PASSWORD "
                "(legacy HL_KEYSTORE_PASSWORD still works) or add it to the app env file."
            )
        create_keystore(private_key, password)

    def list_keys(self) -> List[str]:
        from cli.keystore import list_keystores

        return [ks["address"] for ks in list_keystores()]

    def available(self) -> bool:
        return True


class MacOSKeychainBackend(KeystoreBackend):
    """macOS Keychain via the `security` CLI tool."""

    SERVICE = "agent-cli"

    def name(self) -> str:
        return "keychain"

    def get_key(self, address: Optional[str] = None, venue: str = "hl") -> Optional[str]:
        if not self.available():
            return None

        if address is None:
            addresses = self.list_keys()
            if not addresses:
                return None
            address = addresses[0]

        address = self._normalize(address)
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", self.SERVICE, "-a", address, "-w"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                key = result.stdout.strip()
                if key:
                    return key
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def store_key(self, address: str, private_key: str) -> None:
        if not self.available():
            raise RuntimeError("macOS Keychain not available on this platform")

        address = self._normalize(address)
        result = subprocess.run(
            ["security", "add-generic-password", "-s", self.SERVICE, "-a", address, "-w", private_key, "-U"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Keychain store failed: {result.stderr.strip()}")

    def list_keys(self) -> List[str]:
        if not self.available():
            return []

        try:
            result = subprocess.run(["security", "dump-keychain"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        addresses: List[str] = []
        lines = result.stdout.splitlines()
        in_agent_cli_entry = False

        for line in lines:
            stripped = line.strip()
            if '"svce"' in stripped and self.SERVICE in stripped:
                in_agent_cli_entry = True
            elif '"acct"' in stripped and in_agent_cli_entry:
                match = re.search(r'"acct".*?="(0x[0-9a-fA-F]+)"', stripped)
                if match:
                    addresses.append(match.group(1).lower())
                in_agent_cli_entry = False
            elif stripped.startswith("keychain:"):
                in_agent_cli_entry = False

        return addresses

    def available(self) -> bool:
        if sys.platform != "darwin":
            return False
        try:
            result = subprocess.run(["which", "security"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def _normalize(address: str) -> str:
        addr = address.lower()
        if not addr.startswith("0x"):
            addr = "0x" + addr
        return addr


class RailwayEnvBackend(KeystoreBackend):
    """Reads private keys from Railway-injected environment variables."""

    _KEY_PATTERN = re.compile(r"^([A-Z_]+)_PRIVATE_KEY$")

    def name(self) -> str:
        return "railway"

    def get_key(self, address: Optional[str] = None, venue: str = "hl") -> Optional[str]:
        if not self.available():
            return None

        for env_var in private_key_env_vars_for_venue(venue):
            key = os.environ.get(env_var)
            if key:
                return key

        for var, val in os.environ.items():
            if self._KEY_PATTERN.match(var) and val:
                return val

        return None

    def store_key(self, address: str, private_key: str) -> None:
        raise NotImplementedError("Cannot store keys in Railway env — set via Railway dashboard")

    def list_keys(self) -> List[str]:
        if not self.available():
            return []

        addresses: List[str] = []
        for var, val in os.environ.items():
            if self._KEY_PATTERN.match(var) and val:
                try:
                    from eth_account import Account

                    acct = Account.from_key(val)
                    addresses.append(acct.address.lower())
                except Exception:
                    pass
        return addresses

    def available(self) -> bool:
        return os.environ.get("RAILWAY_ENVIRONMENT") is not None


class FlatFileBackend(KeystoreBackend):
    """Plaintext key files under the resolved app home.

    WARNING: Keys are stored in plaintext. Use only for development.
    Prefer macOS Keychain or encrypted keystore for production.
    """

    def name(self) -> str:
        return "file"

    def get_key(self, address: Optional[str] = None, venue: str = "hl") -> Optional[str]:
        if address is None:
            addresses = self.list_keys()
            if not addresses:
                return None
            address = addresses[0]

        address = self._normalize(address)
        path = KEYS_DIR / f"{address}.txt"

        if not path.exists():
            return None

        log.warning("Plaintext key storage -- consider migrating to keychain or encrypted keystore")
        return path.read_text().strip()

    def store_key(self, address: str, private_key: str) -> None:
        address = self._normalize(address)
        KEYS_DIR.mkdir(parents=True, exist_ok=True)
        path = KEYS_DIR / f"{address}.txt"
        path.write_text(private_key)
        os.chmod(path, 0o600)

    def list_keys(self) -> List[str]:
        if not KEYS_DIR.exists():
            return []
        return [f.stem for f in sorted(KEYS_DIR.glob("*.txt"))]

    def available(self) -> bool:
        return True

    @staticmethod
    def _normalize(address: str) -> str:
        addr = address.lower()
        if not addr.startswith("0x"):
            addr = "0x" + addr
        return addr


_BACKENDS: List[KeystoreBackend] = [
    MacOSKeychainBackend(),
    EncryptedKeystoreBackend(),
    RailwayEnvBackend(),
    FlatFileBackend(),
]


def get_all_backends() -> List[KeystoreBackend]:
    return list(_BACKENDS)


def get_backend(name: str) -> Optional[KeystoreBackend]:
    for backend in _BACKENDS:
        if backend.name() == name:
            return backend
    return None


def resolve_private_key(venue: str = "hl", address: Optional[str] = None) -> str:
    """Resolve a private key for the selected venue.

    Explicit venue-specific env vars win over passive local storage so callers can
    override a stale key for the current session. If no env override is present,
    fall back to the configured backends in their usual priority order.
    """
    normalized_venue = normalize_venue_name(venue)

    for env_var in private_key_env_vars_for_venue(normalized_venue):
        key = os.environ.get(env_var, "")
        if key:
            log.info("Private key resolved via %s env var", env_var)
            return key

    for backend in _BACKENDS:
        if not backend.available():
            continue
        try:
            key = backend.get_key(address=address, venue=normalized_venue)
            if key:
                log.info("Private key resolved via %s backend", backend.name())
                return key
        except Exception as exc:
            log.debug("Backend %s failed: %s", backend.name(), exc)

    env_hint = " or ".join(private_key_env_vars_for_venue(normalized_venue))
    raise RuntimeError(
        "No private key available. Options:\n"
        "  1. Import a key:  hl keys import --backend keychain\n"
        "  2. Use keystore:  hl wallet import\n"
        f"  3. Set env var:   export {env_hint}=0x...\n"
        "  4. On Railway:    set one of those env vars in the dashboard"
    )
