"""Upload generic MCP subscription metering rows to web-auth."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional


def _metering_url() -> str:
    return os.environ.get("NUNCHI_METERING_URL", "").strip()


def _metering_token() -> str:
    return os.environ.get("NUNCHI_METERING_TOKEN", "").strip()


def _account_id() -> str:
    return os.environ.get("NUNCHI_ACCOUNT_ID", "").strip()


def _subscription_id() -> str:
    return os.environ.get("NUNCHI_SUBSCRIPTION_ID", "").strip()


def _plan_id(default: str = "clone-local") -> str:
    return os.environ.get("NUNCHI_PLAN_ID", default).strip() or default


def metering_enabled() -> bool:
    return bool(_metering_url() and _metering_token() and _account_id())


def _tool_bucket(tool_name: str) -> str:
    paid_compute = {
        "run_strategy",
        "radar_run",
        "apex_run",
        "reflect_run",
        "hedge_agent_smoke_test",
    }
    safety_gated = {
        "trade",
        "money_withdraw",
        "money_transfer_usd",
        "money_deposit",
        "approve_agent",
        "wallet_auto",
        "funding_hedge_execute",
    }
    if tool_name in safety_gated:
        return "safety_gated"
    if tool_name in paid_compute:
        return "paid_compute"
    return "free"


def _row_id(prefix: str, payload: Mapping[str, Any]) -> str:
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{prefix}:{stable}".encode("utf-8")).hexdigest()


def upload_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    url = _metering_url()
    token = _metering_token()
    account_id = _account_id()
    if not url or not token or not account_id or not rows:
        return {"ok": False, "skipped": "metering_not_configured"}
    body = {
        "account_id": account_id,
        "accountId": account_id,
        "subscription_id": _subscription_id() or None,
        "subscriptionId": _subscription_id() or None,
        "plan_id": _plan_id(),
        "planId": _plan_id(),
        "rows": rows,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"http_{exc.code}", "detail": detail[:500]}


def report_tool_call(tool_name: str) -> dict[str, Any]:
    if not metering_enabled():
        return {"ok": False, "skipped": "metering_not_configured"}
    ts = int(time.time() * 1000)
    row = {
        "tool_name": tool_name,
        "bucket": _tool_bucket(tool_name),
        "calls": 1,
        "ts": ts,
    }
    row_id = _row_id("mcp_tool", row)
    return upload_rows([{"row_id": row_id, "metric_type": "mcp_tool", "row": row}])


def report_inference_cost(
    *,
    inference_usd: Any,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: Optional[int] = None,
    cache_savings_usd: Optional[Any] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    if not metering_enabled():
        return {"ok": False, "skipped": "metering_not_configured"}
    ts = int(time.time() * 1000)
    row = {
        "inference_usd": inference_usd,
        "usd_cost": inference_usd,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "ts": ts,
    }
    if cached_tokens is not None:
        row["cached_tokens"] = int(cached_tokens)
    if cache_savings_usd is not None:
        row["cache_savings_usd"] = cache_savings_usd
    if model:
        row["model"] = model
    row_id = _row_id("cost", row)
    return upload_rows([{"row_id": row_id, "metric_type": "cost", "row": row}])
