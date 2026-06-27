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


def test_entrypoint_json_rpc_initialize():
    from scripts.entrypoint import handle_mcp_json_rpc

    status, response = handle_mcp_json_rpc(
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
        {},
    )

    assert status == 200
    assert response["result"]["serverInfo"]["name"] == "nunchi-agent-cli-runner"


def test_entrypoint_trade_fails_closed_without_signing_context(monkeypatch, tmp_path):
    from scripts.entrypoint import handle_mcp_json_rpc

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "trade",
            "arguments": {"instrument": "ETH-PERP", "side": "buy", "size": 0.1},
        },
    }).encode()

    status, response = handle_mcp_json_rpc(body, {})

    assert status == 200
    assert "requires a signing context" in response["result"]["content"][0]["text"]


def test_entrypoint_trade_forwards_trusted_context_to_subprocess(monkeypatch, tmp_path):
    import cli.mcp_server as mcp_server
    from scripts.entrypoint import handle_mcp_json_rpc

    captured = {}

    def fake_run_hl(*args, timeout=30, env_overrides=None):
        captured["args"] = args
        captured["env_overrides"] = env_overrides
        return "ok"

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NUNCHI_RUNNER_CONTEXT_SECRET", "shared-secret")
    monkeypatch.setattr(mcp_server, "_run_hl", fake_run_hl)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "trade",
            "arguments": {"instrument": "ETH-PERP", "side": "buy", "size": 0.1},
        },
    }).encode()
    headers = {
        "x-nunchi-secret-nunchi-runner-context-secret": "shared-secret",
        "x-nunchi-secret-nunchi-web-auth-pair-token": "pair-token",
        "x-nunchi-secret-nunchi-web-auth-address": "0x" + "4" * 40,
        "x-nunchi-trading-permission-tier": "testnet_trading",
        "x-nunchi-trading-network": "testnet",
        "x-nunchi-max-order-size": "0.5",
    }

    status, response = handle_mcp_json_rpc(body, headers)

    assert status == 200
    assert response["result"]["content"][0]["text"] == "ok"
    assert captured["args"] == ("trade", "ETH-PERP", "buy", "0.1", "--yes")
    assert captured["env_overrides"]["NUNCHI_WEB_AUTH_PAIR_TOKEN"] == "pair-token"
    assert captured["env_overrides"]["NUNCHI_WEB_AUTH_ADDRESS"] == "0x" + "4" * 40


def test_entrypoint_btcswp_execute_requires_signing_context(monkeypatch, tmp_path):
    from scripts.entrypoint import handle_mcp_json_rpc

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "btcswp_hedge_execute",
            "arguments": {"primary_side": "long", "primary_notional_usd": 150000},
        },
    }).encode()

    status, response = handle_mcp_json_rpc(body, {})

    assert status == 200
    assert "requires a signing context" in response["result"]["content"][0]["text"]


def test_entrypoint_btcswp_execute_forwards_trusted_context(monkeypatch, tmp_path):
    import cli.mcp_server as mcp_server
    from scripts.entrypoint import handle_mcp_json_rpc

    captured = {}

    def fake_run_hl(*args, timeout=30, env_overrides=None):
        captured["args"] = args
        captured["timeout"] = timeout
        captured["env_overrides"] = env_overrides
        return "executed"

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NUNCHI_RUNNER_CONTEXT_SECRET", "shared-secret")
    monkeypatch.setattr(mcp_server, "_run_hl", fake_run_hl)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "btcswp_hedge_execute",
            "arguments": {
                "primary_side": "long",
                "primary_notional_usd": 150000,
                "hedge_goal": "funding_spike",
                "btcswp_mid": 75000,
                "confirmed": True,
            },
        },
    }).encode()
    headers = {
        "x-nunchi-secret-nunchi-runner-context-secret": "shared-secret",
        "x-nunchi-secret-nunchi-web-auth-pair-token": "pair-token",
        "x-nunchi-secret-nunchi-web-auth-address": "0x" + "4" * 40,
        "x-nunchi-trading-permission-tier": "testnet_trading",
        "x-nunchi-trading-network": "testnet",
    }

    status, response = handle_mcp_json_rpc(body, headers)

    assert status == 200
    assert response["result"]["content"][0]["text"] == "executed"
    assert captured["args"] == (
        "hedge", "execute-quote",
        "--primary-side", "long",
        "--primary-notional-usd", "150000.0",
        "--primary-instrument", "BTC-PERP",
        "--hedge-goal", "funding_spike",
        "--hedge-strength", "1.0",
        "--btcswp-mid", "75000.0",
        "--yes",
    )
    assert captured["timeout"] == 120
    assert captured["env_overrides"]["NUNCHI_WEB_AUTH_PAIR_TOKEN"] == "pair-token"


def test_entrypoint_pair_execute_forwards_trusted_context(monkeypatch, tmp_path):
    import cli.mcp_server as mcp_server
    from scripts.entrypoint import handle_mcp_json_rpc

    captured = {}

    def fake_run_hl(*args, timeout=30, env_overrides=None):
        captured["args"] = args
        captured["timeout"] = timeout
        captured["env_overrides"] = env_overrides
        return "pair-executed"

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NUNCHI_RUNNER_CONTEXT_SECRET", "shared-secret")
    monkeypatch.setattr(mcp_server, "_run_hl", fake_run_hl)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "pair_trade_execute",
            "arguments": {
                "primary_side": "long",
                "primary_notional_usd": 150000,
                "btc_mid": 75000,
                "btcswp_mid": 75000,
                "confirmed": True,
            },
        },
    }).encode()
    headers = {
        "x-nunchi-secret-nunchi-runner-context-secret": "shared-secret",
        "x-nunchi-secret-nunchi-web-auth-pair-token": "pair-token",
        "x-nunchi-secret-nunchi-web-auth-address": "0x" + "4" * 40,
        "x-nunchi-trading-permission-tier": "testnet_trading",
        "x-nunchi-trading-network": "testnet",
    }

    status, response = handle_mcp_json_rpc(body, headers)

    assert status == 200
    assert response["result"]["content"][0]["text"] == "pair-executed"
    assert captured["args"] == (
        "pair", "execute",
        "--primary-side", "long",
        "--primary-notional-usd", "150000.0",
        "--hedge-goal", "auto",
        "--hedge-strength", "1.0",
        "--slippage", "0.01",
        "--leverage", "1.0",
        "--venue", "direct",
        "--btc-mid", "75000.0",
        "--btcswp-mid", "75000.0",
        "--yes",
    )
    assert captured["timeout"] == 120
    assert captured["env_overrides"]["NUNCHI_WEB_AUTH_PAIR_TOKEN"] == "pair-token"
