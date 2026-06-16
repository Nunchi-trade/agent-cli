from __future__ import annotations

import json
import sys
import types
from typing import Callable


class FakeFastMCP:
    def __init__(self, *_args, **_kwargs):
        self.tools: dict[str, Callable] = {}

    def tool(self, name: str | None = None, **_kwargs):
        def decorator(func: Callable):
            self.tools[name or func.__name__] = func
            return func

        return decorator


def test_treadfi_tools_register_with_fastmcp(monkeypatch):
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    mcp_module = types.ModuleType("mcp")

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()

    assert "treadfi_spec_status" in server.tools
    assert "treadfi_capabilities" in server.tools
    assert "treadfi_market_params" in server.tools

    payload = json.loads(server.tools["treadfi_spec_status"]())
    assert payload["status"] == "blocked"
