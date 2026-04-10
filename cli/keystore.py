"""Encrypted keystore — geth-compatible Web3 Secret Storage.

Uses eth_account.Account.encrypt()/decrypt() with scrypt KDF.
Keystore files live at ~/.agent-cli/keystore/<address>.json by default.
Legacy installs under ~/.hl-agent remain supported automatically.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from common.app_paths import env_file as default_env_file
from common.app_paths import keystore_dir as default_keystore_dir

KEYSTORE_DIR = default_keystore_dir()
ENV_FILE = default_env_file()
PASSWORD_ENV_VARS = (
    "AGENT_CLI_KEYSTORE_PASSWORD",
    "KEYSTORE_PASSWORD",
    "HL_KEYSTORE_PASSWORD",  # legacy compatibility
)
PASSWORD_FILE_KEYS = PASSWORD_ENV_VARS


def _ensure_dir() -> Path:
    KEYSTORE_DIR.mkdir(parents=True, exist_ok=True)
    return KEYSTORE_DIR


def create_keystore(private_key: str, password: str) -> Path:
    """Encrypt a private key and save to keystore. Returns path to keystore file."""
    from eth_account import Account

    encrypted = Account.encrypt(private_key, password)
    address = encrypted["address"].lower()

    ks_dir = _ensure_dir()
    ks_path = ks_dir / f"{address}.json"
    ks_path.write_text(json.dumps(encrypted, indent=2))
    return ks_path


def load_keystore(address: str, password: str) -> str:
    """Decrypt a keystore file and return the private key hex string."""
    from eth_account import Account

    address = address.lower().replace("0x", "")
    ks_path = KEYSTORE_DIR / f"{address}.json"

    if not ks_path.exists():
        raise FileNotFoundError(f"No keystore found for address {address}")

    with open(ks_path) as f:
        keystore = json.load(f)

    key_bytes = Account.decrypt(keystore, password)
    return "0x" + key_bytes.hex()


def list_keystores() -> List[dict]:
    """List all keystore files. Returns list of {address, path}."""
    ks_dir = _ensure_dir()
    result = []
    for f in sorted(ks_dir.glob("*.json")):
        address = f.stem
        result.append({
            "address": f"0x{address}",
            "path": str(f),
        })
    return result


def _load_env_password() -> str:
    """Load keystore password from the persisted env file if it exists."""
    if not ENV_FILE.exists():
        return ""

    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in PASSWORD_FILE_KEYS:
            return value.strip()
    return ""


def _resolve_password(password: Optional[str] = None) -> str:
    """Resolve keystore password from argument, env var, or env file."""
    if password:
        return password

    for env_var in PASSWORD_ENV_VARS:
        value = os.environ.get(env_var, "")
        if value:
            return value

    return _load_env_password()


def get_keystore_key(address: Optional[str] = None, password: Optional[str] = None) -> Optional[str]:
    """Try to load a private key from keystore.

    If address is None, uses first available keystore.
    Returns None if no keystore is available or password is not provided.
    """
    keystores = list_keystores()
    if not keystores:
        return None

    password = _resolve_password(password)
    if not password:
        return None

    if address:
        address = address.lower().replace("0x", "")
    else:
        address = keystores[0]["address"].lower().replace("0x", "")

    try:
        return load_keystore(address, password)
    except Exception:
        return None


def get_keystore_key_for_address(address: str, password: Optional[str] = None) -> Optional[str]:
    """Load private key for a specific wallet address.

    Used by multi-wallet mode to get keys for per-strategy wallets.
    Returns None if address is not found or password is unavailable.
    """
    if not address:
        return None

    password = _resolve_password(password)
    if not password:
        return None

    addr = address.lower().replace("0x", "")
    try:
        return load_keystore(addr, password)
    except Exception:
        return None
