"""Smoke tests for MCP money-movement wrappers."""
from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace


class FakeFastMCP:
    last = None

    def __init__(self, *args, **kwargs):
        self.tools = {}
        FakeFastMCP.last = self

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def install_fake_mcp(monkeypatch) -> None:
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)


def test_mcp_strategies_exposes_ai_agent_without_legacy_name(monkeypatch):
    install_fake_mcp(monkeypatch)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()
    payload = json.loads(server.tools["strategies"]())

    assert "ai_agent" in payload["strategies"]
    assert "Nunchi-hosted LLM trading agent" in payload["strategies"]["ai_agent"]["description"]
    assert "claude_agent" not in payload["strategies"]


def test_mcp_setup_check_treats_web_auth_pairing_as_wallet_selection(monkeypatch):
    install_fake_mcp(monkeypatch)
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)

    import cli.keystore as keystore
    import cli.web_auth as web_auth
    from cli.mcp_server import create_mcp_server

    monkeypatch.setattr(keystore, "list_keystores", lambda: [])
    monkeypatch.setattr(
        web_auth,
        "get_stored_pairing",
        lambda: SimpleNamespace(selected_or_master_address="0x1111111111111111111111111111111111111111"),
    )

    server = create_mcp_server()
    payload = json.loads(server.tools["setup_check"]())

    assert payload["web_auth"] == {
        "paired": True,
        "selected_wallet": "0x1111111111111111111111111111111111111111",
    }
    assert payload["passed"] is True
    assert not any("No private key" in issue for issue in payload["issues"])


def test_mcp_pair_status_uses_agent_cli_pair_status(monkeypatch):
    install_fake_mcp(monkeypatch)

    import cli.mcp_server as mcp_server

    calls = []
    monkeypatch.setattr(mcp_server, "_run_hl", lambda *args, timeout=30: calls.append(args) or "Pairing: NONE")

    server = mcp_server.create_mcp_server()

    assert server.tools["pair_status"]() == "Pairing: NONE"
    assert calls == [("pair", "status")]


def test_mcp_funding_hedge_propose_is_read_only_json(monkeypatch):
    install_fake_mcp(monkeypatch)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()
    payload = json.loads(server.tools["funding_hedge_propose"](
        asset="BTC",
        perp_side="long",
        perp_notional_usd=150_000.0,
        funding_apr=0.45,
    ))

    assert payload["asset"] == "BTC"
    assert payload["perp_side"] == "long"
    assert payload["perp_notional_usd"] == 150_000.0
    assert payload["hedge_notional_usd"] > 0


def test_mcp_run_strategy_builds_bounded_mock_agent_cli_call(monkeypatch):
    install_fake_mcp(monkeypatch)

    import cli.mcp_server as mcp_server

    calls = []
    monkeypatch.setattr(mcp_server, "_run_hl", lambda *args, timeout=30: calls.append((args, timeout)) or "ok")

    server = mcp_server.create_mcp_server()

    assert server.tools["run_strategy"](
        "engine_mm",
        instrument="ETH-PERP",
        tick=0,
        max_ticks=1,
        mock=True,
        dry_run=True,
    ) == "ok"
    assert calls == [
        (
            (
                "run",
                "engine_mm",
                "-i",
                "ETH-PERP",
                "-t",
                "0",
                "--max-ticks",
                "1",
                "--mock",
                "--dry-run",
            ),
            60,
        )
    ]


def test_mcp_money_tools_require_confirm(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()

    assert server.tools["money_withdraw"]("5", "0x1111111111111111111111111111111111111111") == (
        "Refusing to move funds without confirm=true."
    )
    assert server.tools["money_deposit"]("5") == "Refusing to move funds without confirm=true."
    assert server.tools["approve_agent"]() == "Refusing to approve agent without confirm=true."


def test_mcp_money_withdraw_confirm_builds_subprocess(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    import cli.mcp_server as mcp_server

    calls = []
    monkeypatch.setattr(mcp_server, "_run_hl", lambda *args, timeout=30: calls.append(args) or "ok")
    server = mcp_server.create_mcp_server()

    assert server.tools["money_withdraw"](
        "5",
        "0x1111111111111111111111111111111111111111",
        confirm=True,
        mainnet=True,
    ) == "ok"
    assert calls == [
        (
            "money",
            "withdraw",
            "5",
            "0x1111111111111111111111111111111111111111",
            "--yes",
            "--mainnet",
        )
    ]


def test_mcp_trade_requires_confirm_or_dry_run(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()

    assert server.tools["trade"]("ETH-PERP", "buy", 0.01) == (
        "Refusing to trade without confirm=true or dry_run=true."
    )


def test_mcp_trade_confirm_builds_safe_subprocess(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    import cli.mcp_server as mcp_server

    calls = []
    monkeypatch.setattr(mcp_server, "_run_hl", lambda *args, timeout=30: calls.append(args) or "ok")
    server = mcp_server.create_mcp_server()

    assert server.tools["trade"](
        "ETH-PERP",
        "buy",
        0.01,
        price=2500.0,
        tif="Alo",
        confirm=True,
        max_notional_usd=50.0,
        mainnet=True,
    ) == "ok"
    assert calls == [
        (
            "trade",
            "ETH-PERP",
            "buy",
            "0.01",
            "--price",
            "2500.0",
            "--tif",
            "Alo",
            "--yes",
            "--max-notional",
            "50.0",
            "--mainnet",
        )
    ]


def test_mcp_hedge_smoke_test_builds_script_call(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    import cli.mcp_server as mcp_server

    calls = []
    monkeypatch.setattr(mcp_server, "_run_script", lambda *args, timeout=300: calls.append(args) or "ok")
    server = mcp_server.create_mcp_server()

    assert server.tools["hedge_agent_smoke_test"](
        instrument="BTC-PERP",
        position_qty=4.0,
        notional_threshold=10000.0,
        mainnet_account_check=True,
    ) == "ok"
    assert calls == [
        (
            "test_hedge_agent.py",
            "--instrument",
            "BTC-PERP",
            "--position-qty",
            "4.0",
            "--urgency-factor",
            "0.5",
            "--max-hedge-size",
            "5.0",
            "--slippage-bps",
            "10.0",
            "--notional-threshold",
            "10000.0",
            "--mainnet-account-check",
        )
    ]


def test_mcp_hedge_smoke_test_requires_confirm_for_testnet_transfer(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()

    assert server.tools["hedge_agent_smoke_test"](send_testnet_usdc="5", sam_address="0xabc") == (
        "Refusing to move testnet USDC without confirm_send_testnet_usdc=true."
    )
