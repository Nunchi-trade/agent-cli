"""Tests for scripts/entrypoint.py — build_command, auth, redaction."""
from __future__ import annotations

import os
import re
import sys
from unittest import mock

import pytest

from scripts.entrypoint import (
    build_command,
    MAX_BODY_SIZE,
    _SECRET_RE,
    HealthHandler,
)


# ---------------------------------------------------------------------------
# build_command
# ---------------------------------------------------------------------------

class TestBuildCommand:
    def test_apex_mode_default(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "apex")
        monkeypatch.delenv("APEX_PRESET", raising=False)
        monkeypatch.delenv("APEX_BUDGET", raising=False)
        monkeypatch.delenv("APEX_SLOTS", raising=False)
        monkeypatch.delenv("APEX_LEVERAGE", raising=False)
        monkeypatch.delenv("TICK_INTERVAL", raising=False)
        monkeypatch.setenv("HL_TESTNET", "true")

        cmd = build_command()
        assert cmd[:3] == [sys.executable, "-m", "cli.main"]
        assert "apex" in cmd
        assert "run" in cmd
        assert "--data-dir" in cmd
        assert "--mainnet" not in cmd

    def test_apex_mode_with_all_options(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "apex")
        monkeypatch.setenv("APEX_PRESET", "aggressive")
        monkeypatch.setenv("APEX_BUDGET", "1000")
        monkeypatch.setenv("APEX_SLOTS", "5")
        monkeypatch.setenv("APEX_LEVERAGE", "10")
        monkeypatch.setenv("TICK_INTERVAL", "30")
        monkeypatch.setenv("HL_TESTNET", "false")

        cmd = build_command()
        assert "--preset" in cmd
        assert "aggressive" in cmd
        assert "--budget" in cmd
        assert "1000" in cmd
        assert "--slots" in cmd
        assert "5" in cmd
        assert "--leverage" in cmd
        assert "10" in cmd
        assert "--tick" in cmd
        assert "30" in cmd
        assert "--mainnet" in cmd

    def test_wolf_mode(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "wolf")
        monkeypatch.setenv("HL_TESTNET", "true")
        monkeypatch.delenv("APEX_PRESET", raising=False)
        monkeypatch.delenv("APEX_BUDGET", raising=False)
        monkeypatch.delenv("APEX_SLOTS", raising=False)
        monkeypatch.delenv("APEX_LEVERAGE", raising=False)
        monkeypatch.delenv("TICK_INTERVAL", raising=False)

        cmd = build_command()
        assert "apex" in cmd
        assert "run" in cmd

    def test_strategy_mode(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "strategy")
        monkeypatch.setenv("STRATEGY", "engine_mm")
        monkeypatch.setenv("INSTRUMENT", "BTC-PERP")
        monkeypatch.setenv("TICK_INTERVAL", "5")
        monkeypatch.setenv("HL_TESTNET", "true")

        cmd = build_command()
        assert "run" in cmd
        assert "engine_mm" in cmd
        assert "-i" in cmd
        assert "BTC-PERP" in cmd
        assert "-t" in cmd
        assert "5" in cmd
        assert "--mainnet" not in cmd

    def test_strategy_mode_mainnet(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "strategy")
        monkeypatch.setenv("HL_TESTNET", "false")

        cmd = build_command()
        assert "--mainnet" in cmd

    def test_mcp_mode(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "mcp")

        cmd = build_command()
        assert cmd == [sys.executable, "-m", "cli.main", "mcp", "serve", "--transport", "sse"]

    def test_unknown_mode_exits(self, monkeypatch):
        monkeypatch.setenv("RUN_MODE", "invalid_mode")

        with pytest.raises(SystemExit):
            build_command()


# ---------------------------------------------------------------------------
# MAX_BODY_SIZE
# ---------------------------------------------------------------------------

class TestMaxBodySize:
    def test_value(self):
        assert MAX_BODY_SIZE == 1_048_576  # 1MB


# ---------------------------------------------------------------------------
# _SECRET_RE
# ---------------------------------------------------------------------------

class TestSecretRedaction:
    def test_redacts_private_key(self):
        key = "0x" + "a" * 64
        result = _SECRET_RE.sub("0x[REDACTED]", f"cmd --key {key} --other")
        assert "0x[REDACTED]" in result
        assert key not in result

    def test_does_not_redact_short_hex(self):
        short = "0xabc123"
        result = _SECRET_RE.sub("0x[REDACTED]", f"addr={short}")
        assert short in result

    def test_multiple_keys(self):
        key1 = "0x" + "b" * 64
        key2 = "0x" + "c" * 64
        text = f"{key1} and {key2}"
        result = _SECRET_RE.sub("0x[REDACTED]", text)
        assert result.count("0x[REDACTED]") == 2
        assert key1 not in result
        assert key2 not in result

    def test_no_keys_unchanged(self):
        text = "no secrets here"
        assert _SECRET_RE.sub("0x[REDACTED]", text) == text


# ---------------------------------------------------------------------------
# _check_auth
# ---------------------------------------------------------------------------

class TestCheckAuth:
    def _make_handler(self, auth_header=""):
        """Create a minimal handler mock for testing _check_auth."""
        handler = mock.MagicMock(spec=HealthHandler)
        handler.headers = {"Authorization": auth_header}
        handler._check_auth = HealthHandler._check_auth.__get__(handler)
        handler.write = mock.MagicMock()
        return handler

    def test_no_token_configured(self, monkeypatch):
        import scripts.entrypoint as ep
        original = ep.AUTH_TOKEN
        ep.AUTH_TOKEN = None
        try:
            handler = self._make_handler()
            assert handler._check_auth() is True
        finally:
            ep.AUTH_TOKEN = original

    def test_valid_token(self, monkeypatch):
        import scripts.entrypoint as ep
        original = ep.AUTH_TOKEN
        ep.AUTH_TOKEN = "test-secret"
        try:
            handler = self._make_handler(auth_header="Bearer test-secret")
            assert handler._check_auth() is True
        finally:
            ep.AUTH_TOKEN = original

    def test_invalid_token(self, monkeypatch):
        import scripts.entrypoint as ep
        original = ep.AUTH_TOKEN
        ep.AUTH_TOKEN = "test-secret"
        try:
            handler = self._make_handler(auth_header="Bearer wrong-token")
            result = handler._check_auth()
            assert result is False
            handler.send_response.assert_called_with(401)
        finally:
            ep.AUTH_TOKEN = original

    def test_missing_auth_header(self, monkeypatch):
        import scripts.entrypoint as ep
        original = ep.AUTH_TOKEN
        ep.AUTH_TOKEN = "test-secret"
        try:
            handler = self._make_handler(auth_header="")
            result = handler._check_auth()
            assert result is False
        finally:
            ep.AUTH_TOKEN = original
