"""Tests for MCP tool safety annotations (readOnlyHint / destructiveHint).

The classification sets are validated without importing the optional `mcp`
package; the end-to-end wiring check is skipped unless `mcp` is installed.
"""
import pytest


def test_classification_sets_are_disjoint():
    from cli.mcp_server import _READ_ONLY_TOOLS, _DESTRUCTIVE_TOOLS
    assert _READ_ONLY_TOOLS.isdisjoint(_DESTRUCTIVE_TOOLS)


def test_destructive_set_covers_fund_movers():
    from cli.mcp_server import _DESTRUCTIVE_TOOLS
    for name in (
        "trade", "run_strategy", "apex_run", "schedule_cancel", "emergency_close_all",
        "btcswp_hedge_execute", "pair_trade_execute", "pair_trade_close",
    ):
        assert name in _DESTRUCTIVE_TOOLS


def test_read_only_set_covers_reads():
    from cli.mcp_server import _READ_ONLY_TOOLS
    for name in ("account", "status", "strategies", "order_status", "funding_rates", "btcswp_hedge_quote", "pair_trade_quote"):
        assert name in _READ_ONLY_TOOLS


def test_server_applies_annotations():
    """End-to-end: built server exposes the tools with correct hints."""
    pytest.importorskip("mcp")
    from cli.mcp_server import create_mcp_server, _READ_ONLY_TOOLS, _DESTRUCTIVE_TOOLS

    server = create_mcp_server()
    tools = server._tool_manager.list_tools()
    by_name = {t.name: t for t in tools}

    # Every classified tool is actually registered.
    for name in _READ_ONLY_TOOLS | _DESTRUCTIVE_TOOLS:
        assert name in by_name, f"{name} not registered on the MCP server"

    # Hints are wired through correctly.
    assert by_name["trade"].annotations is not None
    assert by_name["trade"].annotations.destructiveHint is True
    assert by_name["trade"].annotations.readOnlyHint is False
    assert by_name["schedule_cancel"].annotations.destructiveHint is True
    assert by_name["emergency_close_all"].annotations.destructiveHint is True
    assert by_name["account"].annotations.readOnlyHint is True
    assert by_name["funding_rates"].annotations.readOnlyHint is True
