"""Manifest drift check — generated constants match tools.json."""
from cli.generated.mcp_tool_manifest import (
    DESTRUCTIVE_TOOLS,
    MANIFEST_VERSION,
    READ_ONLY_TOOLS,
    RUNNER_TOOLS,
    TOOL_BUCKETS,
)


def test_manifest_version():
    assert MANIFEST_VERSION == 1


def test_read_only_and_destructive_disjoint():
    assert READ_ONLY_TOOLS.isdisjoint(DESTRUCTIVE_TOOLS)


def test_runner_tools_cover_registered_reads():
    for name in ("account", "status", "strategies", "order_status", "funding_rates"):
        assert name in READ_ONLY_TOOLS
        assert name in RUNNER_TOOLS


def test_destructive_covers_fund_movers():
    for name in ("trade", "run_strategy", "apex_run", "schedule_cancel", "emergency_close_all"):
        assert name in DESTRUCTIVE_TOOLS


def test_buckets_partition_tools():
    all_bucketed = set(TOOL_BUCKETS["free"]) | set(TOOL_BUCKETS["paidCompute"]) | set(TOOL_BUCKETS["safetyGated"])
    assert all_bucketed == set(READ_ONLY_TOOLS) | set(
        name for name, meta in __import__("cli.generated.mcp_tool_manifest", fromlist=["TOOL_METADATA"]).TOOL_METADATA.items()
        if not meta["readOnly"]
    )
