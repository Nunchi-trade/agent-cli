"""Hosted MCP entitlement policy for the local MCP server.

The policy is intentionally inactive unless Nunchi entitlement context is
configured. That keeps fully local/BYO `agent-cli` MCP usage ungated while
allowing hosted MCP/tools and Nunchi-inference modes to consume the same
entitlement JSON returned by web-auth.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import requests

from cli.web_auth import PAIR_API_BASE, get_stored_pairing
from cli.mcp_metering import report_tool_call

FREE_TOOLS = {
    "strategies",
    "builder_status",
    "wallet_list",
    "setup_check",
    "pair_status",
    "account",
    "status",
    "funding_hedge_propose",
    "funding_hedge_backtest",
    "apex_status",
    "agent_memory",
    "trade_journal",
    "judge_report",
    "obsidian_context",
    "money_bridge_status",
}
PAID_COMPUTE_TOOLS = {"run_strategy", "radar_run", "apex_run", "reflect_run", "hedge_agent_smoke_test"}
SAFETY_GATED_TOOLS = {
    "trade",
    "money_withdraw",
    "money_transfer_usd",
    "money_deposit",
    "approve_agent",
    "wallet_auto",
}
DEFAULT_TOOL_BUCKETS = {
    "free": sorted(FREE_TOOLS),
    "paidCompute": sorted(PAID_COMPUTE_TOOLS),
    "safetyGated": sorted(SAFETY_GATED_TOOLS),
}

STATE_PATH = Path(os.environ.get("NUNCHI_MCP_ENTITLEMENT_STATE", "~/.hl-agent/mcp-entitlement-state.json")).expanduser()


@dataclass
class EntitlementDecision:
    allowed: bool
    reason: str = ""

    def as_message(self, tool_name: str) -> str:
        return f"Refusing MCP tool `{tool_name}`: {self.reason}"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_json_file(path: Path) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _configured_mode_requires_entitlement() -> bool:
    mode = os.environ.get("NUNCHI_CONNECTION_MODE") or os.environ.get("NUNCHI_MCP_CONNECTION_MODE")
    if mode in {"hosted-mcp-tools", "hosted-mcp-tools-inference"}:
        return True
    return _truthy(os.environ.get("NUNCHI_MCP_REQUIRE_ENTITLEMENT"))


def load_entitlement() -> Optional[dict[str, Any]]:
    """Load entitlement JSON from env, file, or web-auth pair token.

    Returning None means local/BYO mode: do not apply hosted MCP gating.
    """
    inline = os.environ.get("NUNCHI_MCP_ENTITLEMENT_JSON")
    if inline:
        try:
            parsed = json.loads(inline)
        except json.JSONDecodeError:
            return {"ok": False, "error": "invalid_NUNCHI_MCP_ENTITLEMENT_JSON"}
        return parsed if isinstance(parsed, dict) else {"ok": False, "error": "entitlement_json_not_object"}

    file_path = os.environ.get("NUNCHI_MCP_ENTITLEMENT_FILE")
    if file_path:
        return _read_json_file(Path(file_path).expanduser()) or {"ok": False, "error": "entitlement_file_unreadable"}

    if not _configured_mode_requires_entitlement():
        return None

    pairing = get_stored_pairing()
    if pairing is None:
        return {"ok": False, "error": "hosted_mcp_entitlement_required_but_no_pairing"}
    try:
        resp = requests.get(
            f"{PAIR_API_BASE}/api/entitlements/mcp",
            headers={"Authorization": f"Bearer {pairing.token}", "Accept": "application/json"},
            timeout=10,
        )
    except requests.RequestException as exc:
        return {"ok": False, "error": f"entitlement_fetch_failed:{exc.__class__.__name__}"}
    if not resp.ok:
        return {"ok": False, "error": f"entitlement_fetch_http_{resp.status_code}"}
    try:
        parsed = resp.json()
    except ValueError:
        return {"ok": False, "error": "entitlement_fetch_invalid_json"}
    return parsed if isinstance(parsed, dict) else {"ok": False, "error": "entitlement_fetch_not_object"}


def _normalise_buckets(entitlement: Mapping[str, Any]) -> dict[str, set[str]]:
    raw = entitlement.get("toolBuckets")
    if not isinstance(raw, Mapping):
        raw = DEFAULT_TOOL_BUCKETS
    return {
        "free": set(str(item) for item in raw.get("free", []) if item),
        "paidCompute": set(str(item) for item in raw.get("paidCompute", []) if item),
        "safetyGated": set(str(item) for item in raw.get("safetyGated", []) if item),
    }


def _tool_bucket(tool_name: str, entitlement: Mapping[str, Any]) -> str:
    buckets = _normalise_buckets(entitlement)
    if tool_name in buckets["safetyGated"]:
        return "safetyGated"
    if tool_name in buckets["paidCompute"]:
        return "paidCompute"
    return "free"


def _allowed_tools(entitlement: Mapping[str, Any]) -> set[str]:
    raw = entitlement.get("allowedTools")
    if isinstance(raw, list):
        return {str(item) for item in raw if item}
    buckets = _normalise_buckets(entitlement)
    return set().union(*buckets.values())


def _policy_key(entitlement: Mapping[str, Any]) -> str:
    stable = {
        "tier": entitlement.get("tier"),
        "planId": entitlement.get("planId"),
        "subscription": (entitlement.get("subscription") or {}).get("subscriptionId")
        if isinstance(entitlement.get("subscription"), Mapping)
        else None,
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def _read_state() -> dict[str, Any]:
    return _read_json_file(STATE_PATH) or {}


def _write_state(state: Mapping[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", "utf-8")
        STATE_PATH.chmod(0o600)
    except OSError:
        pass


def _period_key() -> str:
    return time.strftime("%Y-%m")


def _local_calls_used(entitlement: Mapping[str, Any]) -> int:
    state = _read_state()
    scoped = state.get(_policy_key(entitlement), {})
    period = scoped.get(_period_key(), {})
    try:
        return int(period.get("mcpCalls", 0))
    except (TypeError, ValueError):
        return 0


def _record_allowed_call(entitlement: Mapping[str, Any], tool_name: str) -> None:
    state = _read_state()
    key = _policy_key(entitlement)
    period = _period_key()
    scoped = state.setdefault(key, {})
    bucket = scoped.setdefault(period, {})
    bucket["mcpCalls"] = int(bucket.get("mcpCalls", 0)) + 1
    bucket["updatedAt"] = int(time.time() * 1000)
    bucket["lastTool"] = tool_name
    _write_state(state)


def _model_policy_allows(model: str, entitlement: Mapping[str, Any]) -> EntitlementDecision:
    policy = entitlement.get("modelPolicy")
    if not isinstance(policy, Mapping):
        return EntitlementDecision(True)
    normalized = model.lower()
    if normalized == "openrouter/auto" and not bool(policy.get("allowAuto", False)):
        return EntitlementDecision(False, "model policy blocks openrouter/auto; use the tier default or upgrade")
    if "fusion" in normalized and not bool(policy.get("allowFusion", False)):
        return EntitlementDecision(False, "model policy blocks Fusion routes; use the tier default or upgrade")
    return EntitlementDecision(True)


def check_tool_call(
    tool_name: str,
    *,
    entitlement: Optional[Mapping[str, Any]] = None,
    confirm: bool = False,
    model: Optional[str] = None,
    record: bool = True,
) -> EntitlementDecision:
    entitlement = entitlement if entitlement is not None else load_entitlement()
    if entitlement is None:
        return EntitlementDecision(True)
    if entitlement.get("ok") is False:
        return EntitlementDecision(False, str(entitlement.get("error") or "invalid entitlement"))

    allowed = _allowed_tools(entitlement)
    if allowed and tool_name not in allowed:
        return EntitlementDecision(False, f"tool is outside allowedTools for tier {entitlement.get('tier') or 'unknown'}")

    bucket = _tool_bucket(tool_name, entitlement)
    if bucket == "safetyGated" and not confirm:
        return EntitlementDecision(False, "safety-gated tool requires confirm=true")

    if model:
        model_decision = _model_policy_allows(model, entitlement)
        if not model_decision.allowed:
            return model_decision

    limit = entitlement.get("mcpCallLimit")
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 0
    used = int(entitlement.get("mcpCallsUsed") or 0) + _local_calls_used(entitlement)
    if limit_int > 0 and used >= limit_int:
        return EntitlementDecision(False, f"MCP call limit exceeded ({used}/{limit_int}); upgrade or refresh entitlement")

    if record:
        _record_allowed_call(entitlement, tool_name)
        report_tool_call(tool_name)
    return EntitlementDecision(True)


def current_model_for_tool(tool_name: str) -> Optional[str]:
    if tool_name == "reflect_run":
        return os.environ.get("NUNCHI_REFLECT_MODEL") or os.environ.get("AI_MODEL")
    if tool_name in PAID_COMPUTE_TOOLS:
        return os.environ.get("AI_MODEL") or os.environ.get("OPENROUTER_MODEL")
    return None


def entitlement_summary() -> dict[str, Any]:
    entitlement = load_entitlement()
    if entitlement is None:
        return {"mode": "local_byo", "enforced": False}
    return {
        "mode": "configured",
        "enforced": True,
        "tier": entitlement.get("tier"),
        "planId": entitlement.get("planId"),
        "status": entitlement.get("status"),
        "mcpCallLimit": entitlement.get("mcpCallLimit"),
        "mcpCallsUsed": entitlement.get("mcpCallsUsed"),
        "localCallsUsed": _local_calls_used(entitlement),
        "modelPolicy": entitlement.get("modelPolicy"),
    }
