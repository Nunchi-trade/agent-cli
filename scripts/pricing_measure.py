#!/usr/bin/env python3
"""Measure MCP mode pricing inputs without fabricating missing live costs.

Default execution is safe: it measures local/import and MCP JSON-RPC dry-run
latency, detects credential availability as booleans, and computes only formulas
whose inputs are explicit. Use --openrouter-live to spend OpenRouter credits.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


HOSTED_TOOLS_SEATS = {
    "starter": 5,
    "growth": 10,
    "team": 50,
}

HOSTED_INFERENCE_BUDGETS = {
    "starter": 10.0,
    "growth": 50.0,
    "team": 250.0,
}

FREE_TOOL_CALL_LIMIT_RECOMMENDATION = 20

TOOL_CLASSIFICATION = [
    {
        "tool": "strategies",
        "bucket": "free_read",
        "marginalCost": "local registry read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "builder_status",
        "bucket": "free_read",
        "marginalCost": "local config read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "wallet_list",
        "bucket": "free_read",
        "marginalCost": "local keystore metadata read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "setup_check",
        "bucket": "free_read",
        "marginalCost": "local environment/config checks; no inference",
        "policy": "free tier",
    },
    {
        "tool": "pair_status",
        "bucket": "free_read",
        "marginalCost": "web-auth pair/session read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "account",
        "bucket": "free_read",
        "marginalCost": "Hyperliquid/Hydromancer info read; no inference",
        "policy": "free tier with light rate limit",
    },
    {
        "tool": "status",
        "bucket": "free_read",
        "marginalCost": "position/risk read; no inference",
        "policy": "free tier with light rate limit",
    },
    {
        "tool": "funding_hedge_propose",
        "bucket": "free_read",
        "marginalCost": "deterministic hedge proposal; no LLM by default",
        "policy": "free tier",
    },
    {
        "tool": "funding_hedge_backtest",
        "bucket": "free_read",
        "marginalCost": "bounded CPU/backtest work; no inference",
        "policy": "free tier with abuse cap",
    },
    {
        "tool": "apex_status",
        "bucket": "free_read",
        "marginalCost": "local status read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "agent_memory",
        "bucket": "free_read",
        "marginalCost": "local memory file read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "trade_journal",
        "bucket": "free_read",
        "marginalCost": "local journal read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "judge_report",
        "bucket": "free_read",
        "marginalCost": "latest report read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "obsidian_context",
        "bucket": "free_read",
        "marginalCost": "local vault/context read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "money_bridge_status",
        "bucket": "free_read",
        "marginalCost": "bridge status read; no inference",
        "policy": "free tier",
    },
    {
        "tool": "run_strategy",
        "bucket": "paid_compute",
        "marginalCost": "strategy loop CPU/API calls; may invoke inference depending on strategy",
        "policy": "paid compute quota",
    },
    {
        "tool": "radar_run",
        "bucket": "paid_compute",
        "marginalCost": "market scan CPU/API calls; may be inference-backed in premium paths",
        "policy": "paid compute quota",
    },
    {
        "tool": "apex_run",
        "bucket": "paid_compute",
        "marginalCost": "multi-slot orchestrator; highest sustained runtime/API risk",
        "policy": "paid compute quota",
    },
    {
        "tool": "reflect_run",
        "bucket": "paid_compute",
        "marginalCost": "post-trade review; likely LLM-backed when reports are generated",
        "policy": "paid compute quota",
    },
    {
        "tool": "hedge_agent_smoke_test",
        "bucket": "paid_compute",
        "marginalCost": "agent smoke/eval path; possibly inference-backed",
        "policy": "paid compute quota",
    },
    {
        "tool": "trade",
        "bucket": "safety_gated",
        "marginalCost": "order submission; not inference-heavy and can create builder economics",
        "policy": "free or low-friction, confirmation and limit gated",
    },
    {
        "tool": "funding_hedge_execute",
        "bucket": "safety_gated",
        "marginalCost": "hedge order path; fund-moving when dry_run=false",
        "policy": "confirmation gated; dry-run preview safe",
    },
    {
        "tool": "money_withdraw",
        "bucket": "safety_gated",
        "marginalCost": "fund-moving transfer; no inference",
        "policy": "confirmation and entitlement gated",
    },
    {
        "tool": "money_transfer_usd",
        "bucket": "safety_gated",
        "marginalCost": "fund-moving transfer; no inference",
        "policy": "confirmation and entitlement gated",
    },
    {
        "tool": "money_deposit",
        "bucket": "safety_gated",
        "marginalCost": "fund-moving deposit flow; no inference",
        "policy": "confirmation and entitlement gated",
    },
    {
        "tool": "approve_agent",
        "bucket": "safety_gated",
        "marginalCost": "approval/signing control; no inference",
        "policy": "confirmation and policy gated",
    },
    {
        "tool": "wallet_auto",
        "bucket": "safety_gated",
        "marginalCost": "wallet creation/write; no inference",
        "policy": "disabled on hosted keyless runner, gated elsewhere",
    },
]

INFERENCE_COST_ANCHORS = {
    "openrouter/auto": {
        "costPerHeartbeatUsd": 0.0037,
        "source": "anchor from Task 7 prompt",
    },
    "openai/gpt-4.1-mini": {
        "costPerHeartbeatUsd": 0.0002,
        "source": "anchor from Task 7 prompt",
    },
    "fusion": {
        "costPerHeartbeatUsd": 0.033,
        "source": "anchor from Task 7 prompt",
        "providedRatioVsMini": 146,
    },
}


@dataclass(frozen=True)
class Measurement:
    name: str
    ok: bool
    elapsed_ms: float
    detail: dict[str, Any]


def env_flag(name: str) -> bool:
    return bool(os.environ.get(name))


def has_wallet_credentials() -> bool:
    return any([
        env_flag("HL_PRIVATE_KEY"),
        env_flag("HL_KEYSTORE_PASSWORD"),
        Path(os.path.expanduser("~/.hl-agent/env")).exists(),
    ])


def builder_fee_rate_tenths_bps() -> int:
    raw = os.environ.get("BUILDER_FEE_TENTHS_BPS", "100")
    try:
        value = int(raw)
    except ValueError:
        value = 100
    return max(value, 0)


def builder_revenue_usd(notional_usd: float, fee_tenths_bps: int) -> float:
    return notional_usd * fee_tenths_bps / 100_000


def tool_bucket_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in TOOL_CLASSIFICATION:
        bucket = row["bucket"]
        counts[bucket] = counts.get(bucket, 0) + 1
    counts["total"] = len(TOOL_CLASSIFICATION)
    counts["costedWithoutWalletAuto"] = len([row for row in TOOL_CLASSIFICATION if row["tool"] != "wallet_auto"])
    return counts


def inference_anchor_budget_capacity(budgets: dict[str, float]) -> dict[str, dict[str, Any]]:
    return {
        model: {
            "costPerHeartbeatUsd": anchor["costPerHeartbeatUsd"],
            "source": anchor["source"],
            **({"providedRatioVsMini": anchor["providedRatioVsMini"]} if "providedRatioVsMini" in anchor else {}),
            "heartbeatsByPlan": {
                plan: budget / anchor["costPerHeartbeatUsd"]
                for plan, budget in budgets.items()
            },
        }
        for model, anchor in INFERENCE_COST_ANCHORS.items()
    }


def runtime_c_seat(runtime_monthly_usd: float | None) -> dict[str, Any]:
    if runtime_monthly_usd is None:
        return {
            "computed": False,
            "blocker": "Set --runtime-monthly-usd or RAILWAY_SHARED_RUNTIME_MONTHLY_USD from Railway billing/metrics.",
        }
    return {
        "computed": True,
        "runtimeMonthlyUsd": runtime_monthly_usd,
        "byPlan": {
            plan: {
                "seats": seats,
                "cSeatUsd": runtime_monthly_usd / seats,
            }
            for plan, seats in HOSTED_TOOLS_SEATS.items()
        },
    }


def measure_subprocess(name: str, command: list[str], timeout: float = 30) -> Measurement:
    start = time.perf_counter()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return Measurement(
            name=name,
            ok=result.returncode == 0,
            elapsed_ms=elapsed_ms,
            detail={
                "returnCode": result.returncode,
                "stdoutBytes": len(result.stdout or ""),
                "stderrBytes": len(result.stderr or ""),
            },
        )
    except Exception as exc:  # pragma: no cover - exercised in integration use
        elapsed_ms = (time.perf_counter() - start) * 1000
        return Measurement(name=name, ok=False, elapsed_ms=elapsed_ms, detail={"error": str(exc)})


def measure_entrypoint_method(method: str) -> Measurement:
    from scripts.entrypoint import handle_mcp_json_rpc

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
    }).encode()
    start = time.perf_counter()
    status, response = handle_mcp_json_rpc(body, {})
    elapsed_ms = (time.perf_counter() - start) * 1000
    result = response.get("result") if isinstance(response, dict) else None
    tool_count = len(result.get("tools", [])) if isinstance(result, dict) and isinstance(result.get("tools"), list) else None
    return Measurement(
        name=f"mcp.{method}",
        ok=status == 200,
        elapsed_ms=elapsed_ms,
        detail={
            "httpStatus": status,
            "responseBytes": len(json.dumps(response)),
            "toolCount": tool_count,
        },
    )


def measure_entrypoint_tool(name: str, arguments: dict[str, Any] | None = None) -> Measurement:
    from scripts.entrypoint import handle_mcp_json_rpc

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }).encode()
    start = time.perf_counter()
    status, response = handle_mcp_json_rpc(body, {})
    elapsed_ms = (time.perf_counter() - start) * 1000
    text = ""
    try:
        text = response["result"]["content"][0]["text"]
    except Exception:
        text = json.dumps(response)[:500]
    return Measurement(
        name=f"mcp.{name}",
        ok=status == 200,
        elapsed_ms=elapsed_ms,
        detail={
            "httpStatus": status,
            "responseBytes": len(json.dumps(response)),
            "containsConfirmationRefusal": "confirmed=true" in text,
            "containsSigningRefusal": "requires a signing context" in text,
        },
    )


def safe_trade_refusal_measurement() -> Measurement:
    if has_wallet_credentials():
        return Measurement(
            name="mcp.trade_unsigned_refusal",
            ok=True,
            elapsed_ms=0,
            detail={
                "skipped": True,
                "reason": "wallet credentials are present; skipping trade probe to avoid any order path",
            },
        )
    return measure_entrypoint_tool("trade", {"instrument": "ETH-PERP", "side": "buy", "size": 0.1})


def openrouter_probe(model: str) -> dict[str, Any]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return {"ok": False, "blocker": "OPENROUTER_API_KEY is not set."}
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 8,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "authorization": f"Bearer {key}",
            "content-type": "application/json",
            "http-referer": "https://nunchi.trade",
            "x-title": "nunchi-pricing-measurement",
        },
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = json.loads(response.read().decode())
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "ok": True,
            "model": model,
            "elapsedMs": elapsed_ms,
            "usage": body.get("usage"),
            "provider": body.get("provider"),
            "idPresent": bool(body.get("id")),
        }
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"ok": False, "elapsedMs": elapsed_ms, "status": exc.code, "blocker": exc.reason}
    except Exception as exc:  # pragma: no cover - network dependent
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"ok": False, "elapsedMs": elapsed_ms, "blocker": str(exc)}


def parse_runtime_monthly(args: argparse.Namespace) -> float | None:
    raw = args.runtime_monthly_usd or os.environ.get("RAILWAY_SHARED_RUNTIME_MONTHLY_USD")
    if raw in (None, ""):
        return None
    return float(raw)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    runtime_monthly = parse_runtime_monthly(args)
    fee_tenths_bps = builder_fee_rate_tenths_bps()
    measurements = [
        measure_subprocess("python.import_cli", [sys.executable, "-c", "import cli.main; print('ok')"]),
        measure_entrypoint_method("tools/list"),
        measure_entrypoint_tool("setup_check"),
        measure_entrypoint_tool("strategies"),
        safe_trade_refusal_measurement(),
        measure_entrypoint_tool("funding_hedge_execute", {"coin": "BTC", "dry_run": True}),
    ]
    openrouter = openrouter_probe(args.openrouter_model) if args.openrouter_live else {
        "ok": False,
        "blocker": "Run with --openrouter-live to spend OpenRouter credits for this probe.",
        "credentialPresent": env_flag("OPENROUTER_API_KEY"),
    }
    blockers: list[str] = []
    if runtime_monthly is None:
        blockers.append("missing Railway runtime monthly cost input for Mode 1 C_seat")
    if not has_wallet_credentials():
        blockers.append("missing funded-wallet/HL signing credentials for live funded-wallet measurement")
    if not env_flag("OPENROUTER_API_KEY"):
        blockers.append("missing OPENROUTER_API_KEY for Mode 2 inference spend measurement")
    if not args.openrouter_live:
        blockers.append("OpenRouter live probe not run because --openrouter-live was not set")

    return {
        "schemaVersion": 1,
        "generatedAtMs": int(time.time() * 1000),
        "freeTierRecommendation": {
            "hostedMcpFreeCallLimit": FREE_TOOL_CALL_LIMIT_RECOMMENDATION,
            "note": "Use this as a beta/free-tier cap for hosted MCP discovery/read calls; keep paid-compute and fund-moving actions separately metered/gated.",
        },
        "toolClassification": {
            "counts": tool_bucket_counts(),
            "tools": TOOL_CLASSIFICATION,
        },
        "environment": {
            "runMode": os.environ.get("RUN_MODE"),
            "hlTestnet": os.environ.get("HL_TESTNET"),
            "openrouterCredentialPresent": env_flag("OPENROUTER_API_KEY"),
            "walletCredentialPresent": has_wallet_credentials(),
            "builderAddressPresent": env_flag("BUILDER_ADDRESS"),
            "builderFeeTenthsBps": fee_tenths_bps,
        },
        "measurements": [m.__dict__ for m in measurements],
        "mode1": runtime_c_seat(runtime_monthly),
        "mode2": {
            "inferenceBudgetsUsd": HOSTED_INFERENCE_BUDGETS,
            "anchorEstimates": inference_anchor_budget_capacity(HOSTED_INFERENCE_BUDGETS),
            "openrouterProbe": openrouter,
        },
        "mode3": {
            "builderFeeTenthsBps": fee_tenths_bps,
            "builderRevenuePer100kNotionalUsd": builder_revenue_usd(100_000, fee_tenths_bps),
            "builderRevenuePer1mNotionalUsd": builder_revenue_usd(1_000_000, fee_tenths_bps),
            "note": "Builder revenue is formulaic until funded-wallet fills are measured.",
        },
        "blockers": blockers,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-monthly-usd", type=float, default=None)
    parser.add_argument("--openrouter-live", action="store_true")
    parser.add_argument("--openrouter-model", default=os.environ.get("NUNCHI_PRICING_OPENROUTER_MODEL", "openai/gpt-4.1-mini"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = build_report(args)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
