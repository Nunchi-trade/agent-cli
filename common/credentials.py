"""Standardized key management — pluggable backends with unified resolution.

Backends:
  1. MacOSKeychainBackend  — macOS Keychain via `security` CLI
  2. EncryptedKeystoreBackend — geth-compatible Web3 Secret Storage (existing)
  3. RailwayEnvBackend — Railway-injected environment variables
  4. FlatFileBackend — plaintext files at ~/.hl-agent/keys/ (dev only)

Resolution order for resolve_private_key():
  macOS Keychain -> encrypted keystore -> Railway env -> flat file -> env var -> error
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

log = logging.getLogger("credentials")

KEYS_DIR = Path.home() / ".hl-agent" / "keys"


class KeystoreBackend(ABC):
    """Abstract base class for private key storage backends."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name."""
        ...

    @abstractmethod
    def get_key(self, address: Optional[str] = None) -> Optional[str]:
        """Retrieve a private key. Returns None if not found."""
        ...

    @abstractmethod
    def store_key(self, address: str, private_key: str) -> None:
        """Store a private key for the given address."""
        ...

    @abstractmethod
    def list_keys(self) -> List[str]:
        """Return list of addresses stored in this backend."""
        ...

    @abstractmethod
    def available(self) -> bool:
        """Return True if this backend can be used on the current system."""
        ...


class EncryptedKeystoreBackend(KeystoreBackend):
    """Wraps existing cli/keystore.py — geth-compatible Web3 Secret Storage."""

    def name(self) -> str:
        return "keystore"

    def get_key(self, address: Optional[str] = None) -> Optional[str]:
        from cli.keystore import get_keystore_key, get_keystore_key_for_address

        if address:
            return get_keystore_key_for_address(address)
        return get_keystore_key()

    def store_key(self, address: str, private_key: str) -> None:
        from cli.keystore import create_keystore, _resolve_password

        password = _resolve_password()
        if not password:
            raise RuntimeError(
                "No keystore password available. Set HL_KEYSTORE_PASSWORD or "
                "add it to ~/.hl-agent/env"
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

    def get_key(self, address: Optional[str] = None) -> Optional[str]:
        if not self.available():
            return None

        if address is None:
            # Use first available address
            addresses = self.list_keys()
            if not addresses:
                return None
            address = addresses[0]

        address = self._normalize(address)
        try:
            result = subprocess.run(
                ["security", "find-generic-password",
                 "-s", self.SERVICE, "-a", address, "-w"],
                capture_output=True, text=True, timeout=10,
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
            ["security", "add-generic-password",
             "-s", self.SERVICE, "-a", address, "-w", private_key, "-U"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Keychain store failed: {result.stderr.strip()}")

    def list_keys(self) -> List[str]:
        if not self.available():
            return []

        try:
            result = subprocess.run(
                ["security", "dump-keychain"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        addresses: List[str] = []
        lines = result.stdout.splitlines()
        in_agent_cli_entry = False

        for line in lines:
            stripped = line.strip()
            # Detect service matching our service name
            if '"svce"' in stripped and self.SERVICE in stripped:
                in_agent_cli_entry = True
            elif '"acct"' in stripped and in_agent_cli_entry:
                # Extract account value — format: "acct"<blob>="0xaddress..."
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
            result = subprocess.run(
                ["which", "security"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def _normalize(address: str) -> str:
        """Normalize address to lowercase with 0x prefix."""
        addr = address.lower()
        if not addr.startswith("0x"):
            addr = "0x" + addr
        return addr


class RailwayEnvBackend(KeystoreBackend):
    """Reads private keys from Railway-injected environment variables.

    Looks for HL_PRIVATE_KEY and {VENUE}_PRIVATE_KEY patterns.
    Cannot store keys — those must be set via the Railway dashboard.
    """

    _KEY_PATTERN = re.compile(r"^([A-Z_]+)_PRIVATE_KEY$")

    def name(self) -> str:
        return "railway"

    def get_key(self, address: Optional[str] = None) -> Optional[str]:
        if not self.available():
            return None

        # Try HL_PRIVATE_KEY first
        key = os.environ.get("HL_PRIVATE_KEY")
        if key:
            return key

        # Try any {VENUE}_PRIVATE_KEY
        for var, val in os.environ.items():
            if self._KEY_PATTERN.match(var) and val:
                return val

        return None

    def store_key(self, address: str, private_key: str) -> None:
        raise NotImplementedError(
            "Cannot store keys in Railway env — set via Railway dashboard"
        )

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
    """Plaintext key files at ~/.hl-agent/keys/{address}.txt.

    WARNING: Keys are stored in plaintext. Use only for development.
    Prefer macOS Keychain or encrypted keystore for production.
    """

    def name(self) -> str:
        return "file"

    def get_key(self, address: Optional[str] = None) -> Optional[str]:
        if address is None:
            addresses = self.list_keys()
            if not addresses:
                return None
            address = addresses[0]

        address = self._normalize(address)
        path = KEYS_DIR / f"{address}.txt"

        if not path.exists():
            return None

        log.warning(
            "Plaintext key storage -- consider migrating to keychain or encrypted keystore"
        )
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
        addresses = []
        for f in sorted(KEYS_DIR.glob("*.txt")):
            addresses.append(f.stem)
        return addresses

    def available(self) -> bool:
        return True

    @staticmethod
    def _normalize(address: str) -> str:
        addr = address.lower()
        if not addr.startswith("0x"):
            addr = "0x" + addr
        return addr


# ---------------------------------------------------------------------------
# Backend registry & unified resolver
# ---------------------------------------------------------------------------

# Resolution order: keychain -> keystore -> railway -> flat file -> env var
_BACKENDS: List[KeystoreBackend] = [
    MacOSKeychainBackend(),
    EncryptedKeystoreBackend(),
    RailwayEnvBackend(),
    FlatFileBackend(),
]


def get_all_backends() -> List[KeystoreBackend]:
    """Return all registered backends."""
    return list(_BACKENDS)


def get_backend(name: str) -> Optional[KeystoreBackend]:
    """Look up a backend by name."""
    for b in _BACKENDS:
        if b.name() == name:
            return b
    return None


def resolve_private_key(venue: str = "hl", address: Optional[str] = None) -> str:
    """Resolve a private key by trying backends in priority order.

    Resolution order:
      1. macOS Keychain
      2. Encrypted keystore (geth-compatible)
      3. Railway environment
      4. Flat .txt file
      5. {VENUE}_PRIVATE_KEY env var (direct)

    Raises RuntimeError if no key is found.
    """
    for backend in _BACKENDS:
        if not backend.available():
            continue
        try:
            key = backend.get_key(address)
            if key:
                log.info("Private key resolved via %s backend", backend.name())
                return key
        except Exception as exc:
            log.debug("Backend %s failed: %s", backend.name(), exc)

    # Final fallback: direct env var
    env_var = f"{venue.upper()}_PRIVATE_KEY"
    key = os.environ.get(env_var, "")
    if key:
        log.info("Private key resolved via %s env var", env_var)
        return key

    raise RuntimeError(
        "No private key available. Options:\n"
        "  1. Import a key:  hl keys import --backend keychain\n"
        "  2. Use keystore:  hl wallet import\n"
        "  3. Set env var:   export HL_PRIVATE_KEY=0x...\n"
        "  4. On Railway:    set HL_PRIVATE_KEY in dashboard"
    )
