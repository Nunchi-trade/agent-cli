"""Application storage paths with backward-compatible legacy fallbacks.

New installs should use ~/.agent-cli by default, but existing Hyperliquid-first
installs may already store credentials under ~/.hl-agent. This module centralizes
path resolution so the rest of the code can gradually move to venue-neutral
naming without breaking existing users.
"""
from __future__ import annotations

import os
from pathlib import Path

PRIMARY_APP_DIRNAME = ".agent-cli"
LEGACY_APP_DIRNAME = ".hl-agent"
APP_HOME_ENV_VAR = "AGENT_CLI_HOME"


def primary_app_home() -> Path:
    return Path.home() / PRIMARY_APP_DIRNAME


def legacy_app_home() -> Path:
    return Path.home() / LEGACY_APP_DIRNAME


def resolve_app_home() -> Path:
    override = os.environ.get(APP_HOME_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()

    primary = primary_app_home()
    legacy = legacy_app_home()

    if primary.exists():
        return primary
    if legacy.exists():
        return legacy
    return primary


def keystore_dir() -> Path:
    return resolve_app_home() / "keystore"


def env_file() -> Path:
    return resolve_app_home() / "env"


def keys_dir() -> Path:
    return resolve_app_home() / "keys"
