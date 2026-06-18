from __future__ import annotations

import json
from types import SimpleNamespace


def _ctx(headers=None, meta=None):
    request = SimpleNamespace(headers=headers or {})
    return SimpleNamespace(request_context=SimpleNamespace(request=request, meta=meta))


def test_untrusted_gateway_context_is_ignored(monkeypatch):
    from cli.mcp_server import _trusted_context_env_overrides

    monkeypatch.delenv("NUNCHI_RUNNER_CONTEXT_SECRET", raising=False)
    ctx = _ctx({
        "x-nunchi-web-auth-pair-token": "pair-token",
        "x-nunchi-web-auth-address": "0x" + "1" * 40,
    })

    assert _trusted_context_env_overrides(ctx) == {}


def test_trusted_gateway_headers_become_scoped_env(monkeypatch):
    from cli.mcp_server import _trusted_context_env_overrides

    monkeypatch.setenv("NUNCHI_RUNNER_CONTEXT_SECRET", "shared-secret")
    ctx = _ctx({
        "x-nunchi-runner-context-secret": "shared-secret",
        "x-nunchi-web-auth-pair-token": "pair-token",
        "x-nunchi-web-auth-address": "0x" + "2" * 40,
        "x-nunchi-trading-permission-tier": "testnet_trading",
        "x-nunchi-trading-network": "testnet",
        "x-nunchi-max-order-size": "0.5",
        "x-nunchi-max-strategy-ticks": "12",
    })

    env = _trusted_context_env_overrides(ctx)

    assert env["NUNCHI_WEB_AUTH_PAIR_TOKEN"] == "pair-token"
    assert env["NUNCHI_WEB_AUTH_ADDRESS"] == "0x" + "2" * 40
    assert env["NUNCHI_MAX_ORDER_SIZE"] == "0.5"
    assert env["NUNCHI_MAX_STRATEGY_TICKS"] == "12"
    policy = json.loads(env["NUNCHI_SESSION_POLICY"])
    assert policy["wallets"] == ["0x" + "2" * 40]
    assert policy["network"] == "testnet"
    assert "trade" in policy["allowed_actions"]


def test_context_limits_fail_closed_without_signing_context(monkeypatch, tmp_path):
    from cli.mcp_server import _context_limit_error

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("HL_KEYSTORE_PASSWORD", raising=False)
    monkeypatch.delenv("NUNCHI_WEB_AUTH_PAIR_TOKEN", raising=False)
    monkeypatch.delenv("NUNCHI_WEB_AUTH_ADDRESS", raising=False)

    error = _context_limit_error("trade", {}, require_signing=True)

    assert error is not None
    assert "requires a signing context" in error


def test_context_limits_enforce_consent(monkeypatch, tmp_path):
    from cli.mcp_server import _context_limit_error

    monkeypatch.setenv("HOME", str(tmp_path))
    env = {
        "NUNCHI_WEB_AUTH_PAIR_TOKEN": "pair-token",
        "NUNCHI_WEB_AUTH_ADDRESS": "0x" + "3" * 40,
        "NUNCHI_TRADING_PERMISSION_TIER": "testnet_trading",
        "NUNCHI_TRADING_NETWORK": "testnet",
        "NUNCHI_REQUIRE_CONFIRMATION": "true",
        "NUNCHI_MAX_ORDER_SIZE": "0.5",
        "NUNCHI_MAX_STRATEGY_TICKS": "10",
    }

    assert "confirmed=true" in (_context_limit_error("trade", env, size=0.1) or "")
    assert "max order size" in (_context_limit_error("trade", env, size=1.0, confirmed=True) or "")
    assert "max strategy ticks" in (
        _context_limit_error("run_strategy", env, max_ticks=11, confirmed=True) or ""
    )
    assert "live_trading permission" in (
        _context_limit_error("trade", env, mainnet=True, size=0.1, confirmed=True) or ""
    )


def test_run_hl_applies_env_overrides_only_to_subprocess(monkeypatch):
    import cli.mcp_server as mcp_server

    captured = {}

    class Result:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout, env):
        captured["cmd"] = cmd
        captured["env"] = env
        return Result()

    monkeypatch.delenv("NUNCHI_WEB_AUTH_PAIR_TOKEN", raising=False)
    monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)

    output = mcp_server._run_hl(
        "status",
        env_overrides={"NUNCHI_WEB_AUTH_PAIR_TOKEN": "pair-token"},
    )

    assert output == "ok"
    assert captured["env"]["NUNCHI_WEB_AUTH_PAIR_TOKEN"] == "pair-token"
    assert "NUNCHI_WEB_AUTH_PAIR_TOKEN" not in mcp_server.os.environ
