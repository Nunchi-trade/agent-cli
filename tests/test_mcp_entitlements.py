from __future__ import annotations

import json

from cli import mcp_entitlements


def _entitlement(**overrides):
    base = {
        "ok": True,
        "tier": "hosted-mcp-tools-inference",
        "planId": "hosted-mcp-inference-starter",
        "entitled": True,
        "allowedTools": ["status", "run_strategy", "trade"],
        "toolBuckets": {
            "free": ["status"],
            "paidCompute": ["run_strategy"],
            "safetyGated": ["trade"],
        },
        "mcpCallLimit": 20,
        "mcpCallsUsed": 0,
        "modelPolicy": {"defaultModel": "openai/gpt-4.1-mini", "allowAuto": False, "allowFusion": False},
    }
    base.update(overrides)
    return base


def test_no_entitlement_keeps_local_byo_ungated(monkeypatch):
    monkeypatch.delenv("NUNCHI_MCP_ENTITLEMENT_JSON", raising=False)
    monkeypatch.delenv("NUNCHI_MCP_ENTITLEMENT_FILE", raising=False)
    monkeypatch.delenv("NUNCHI_MCP_REQUIRE_ENTITLEMENT", raising=False)
    monkeypatch.delenv("NUNCHI_CONNECTION_MODE", raising=False)

    assert mcp_entitlements.check_tool_call("run_strategy").allowed


def test_entitlement_blocks_tools_outside_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_entitlements, "STATE_PATH", tmp_path / "state.json")

    decision = mcp_entitlements.check_tool_call("radar_run", entitlement=_entitlement())

    assert not decision.allowed
    assert "outside allowedTools" in decision.reason


def test_safety_gated_tools_require_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_entitlements, "STATE_PATH", tmp_path / "state.json")

    denied = mcp_entitlements.check_tool_call("trade", entitlement=_entitlement())
    allowed = mcp_entitlements.check_tool_call("trade", entitlement=_entitlement(), confirm=True)

    assert not denied.allowed
    assert "confirm=true" in denied.reason
    assert allowed.allowed


def test_free_call_limit_counts_local_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_entitlements, "STATE_PATH", tmp_path / "state.json")
    ent = _entitlement(mcpCallLimit=1)

    assert mcp_entitlements.check_tool_call("status", entitlement=ent).allowed
    denied = mcp_entitlements.check_tool_call("status", entitlement=ent)

    assert not denied.allowed
    assert "MCP call limit exceeded" in denied.reason


def test_model_policy_blocks_auto_and_fusion(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_entitlements, "STATE_PATH", tmp_path / "state.json")
    auto = mcp_entitlements.check_tool_call("run_strategy", entitlement=_entitlement(), model="openrouter/auto")
    fusion = mcp_entitlements.check_tool_call("run_strategy", entitlement=_entitlement(), model="nunchi/fusion")

    assert not auto.allowed
    assert not fusion.allowed


def test_inline_entitlement_json_loads_from_env(monkeypatch):
    monkeypatch.setenv("NUNCHI_MCP_ENTITLEMENT_JSON", json.dumps(_entitlement()))

    loaded = mcp_entitlements.load_entitlement()

    assert loaded is not None
    assert loaded["tier"] == "hosted-mcp-tools-inference"
