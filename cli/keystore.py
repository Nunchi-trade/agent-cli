"""Encrypted keystore — geth-compatible Web3 Secret Storage.

Uses eth_account.Account.encrypt()/decrypt() with scrypt KDF.
Keystore files live at ~/.hl-agent/keystore/<address>.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional


KEYSTORE_DIR = Path.home() / ".hl-agent" / "keystore"
ENV_FILE = Path.home() / ".hl-agent" / "env"


def _ensure_dir() -> Path:
    KEYSTORE_DIR.mkdir(parents=True, exist_ok=True)
    return KEYSTORE_DIR


def create_keystore(private_key: str, password: str) -> Path:
    """Encrypt a private key and save to keystore. Returns path to keystore file."""
    from eth_account import Account

    encrypted = Account.encrypt(private_key, password)
    address = encrypted["address"]

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
    """Load HL_KEYSTORE_PASSWORD from ~/.hl-agent/env if it exists."""
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("HL_KEYSTORE_PASSWORD="):
            return line.split("=", 1)[1]
    return ""


def get_keystore_key(address: Optional[str] = None, password: Optional[str] = None) -> Optional[str]:
    """Try to load a private key from keystore.

    If address is None, uses first available keystore.
    If password is None, checks HL_KEYSTORE_PASSWORD env var.
    Returns None if no keystore available or password not provided.
    """
    import os

    keystores = list_keystores()
    if not keystores:
        return None

    password = password or os.environ.get("HL_KEYSTORE_PASSWORD", "")
    if not password:
        # Auto-detect from ~/.hl-agent/env
        password = _load_env_password()
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
