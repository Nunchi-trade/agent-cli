"""Smoke tests for MCP hedge-agent helpers."""
from __future__ import annotations

import sys
import types


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


def _install_fake_mcp(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)


def test_mcp_hedge_smoke_test_builds_script_call(monkeypatch):
    _install_fake_mcp(monkeypatch)

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
    _install_fake_mcp(monkeypatch)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()

    assert server.tools["hedge_agent_smoke_test"](send_testnet_usdc="5", sam_address="0xabc") == (
        "Refusing to move testnet USDC without confirm_send_testnet_usdc=true."
    )
