"""Tests for setup auth-mode guidance."""
from __future__ import annotations

import json
import sys
import types

from typer.testing import CliRunner

from cli.commands.setup import setup_app


runner = CliRunner()


class FakeFastMCP:
    def __init__(self, *args, **kwargs):
        self.tools = {}

    def tool(self, *args, **kwargs):
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


def install_setup_fakes(monkeypatch, paired_wallet=None) -> None:
    monkeypatch.setitem(sys.modules, "hyperliquid", types.ModuleType("hyperliquid"))
    monkeypatch.setattr("cli.keystore.list_keystores", lambda: [])
    monkeypatch.setattr("cli.web_auth.pairing_from_env", lambda: paired_wallet)


def test_setup_check_warns_on_raw_key_without_pairing(monkeypatch):
    install_setup_fakes(monkeypatch)
    monkeypatch.setenv("HL_PRIVATE_KEY", "0x" + "1" * 64)

    result = runner.invoke(setup_app, ["check"])

    assert result.exit_code == 0
    assert "HL_PRIVATE_KEY set" in result.output
    assert "Raw-key mode active" in result.output
    assert "NUNCHI_WEB_AUTH_PAIR_TOKEN" in result.output


def test_mcp_setup_check_reports_auth_warnings(monkeypatch):
    install_setup_fakes(monkeypatch)
    install_fake_mcp(monkeypatch)
    monkeypatch.setenv("HL_PRIVATE_KEY", "0x" + "1" * 64)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()
    payload = json.loads(server.tools["setup_check"]())

    assert "HL_PRIVATE_KEY set" in payload["ok"]
    assert any("Raw-key mode active" in warning for warning in payload["warnings"])
    assert any("No web-auth pairing context found" in warning for warning in payload["warnings"])
