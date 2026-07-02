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
import shutil
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
OPENROUTER_PROBE_MAX_TOKENS = 16
CREDIT_RATES = {
    "mcpCallCredits": 0.01,
    "paidComputeCallCredits": 0.05,
    "inferenceUsdCredits": 1.0,
}

SECONDS_PER_HOUR = 3_600
MONTHLY_HOURS = 730

PLAN_PRICES_USD = {
    "hosted-mcp-tools-starter": 49.0,
    "hosted-mcp-tools-growth": 99.0,
    "hosted-mcp-tools-team": 299.0,
    "hosted-mcp-inference-starter": 99.0,
    "hosted-mcp-inference-growth": 199.0,
    "hosted-mcp-inference-team": 499.0,
}

RAILWAY_RATE_ENV = {
    "vcpuHourUsd": "RAILWAY_VCPU_HOUR_USD",
    "memoryGbHourUsd": "RAILWAY_MEMORY_GB_HOUR_USD",
    "networkGbUsd": "RAILWAY_NETWORK_GB_USD",
    "diskGbHourUsd": "RAILWAY_DISK_GB_HOUR_USD",
    "baselineMonthlyUsd": "RAILWAY_BASELINE_MONTHLY_USD",
}

PLAN_LIMITS = {
    "hosted-mcp-tools-starter": {"product": "hosted-mcp-tools", "seats": 5, "credits": 50, "mcpCalls": 2_000, "paidComputeCalls": 100, "inferenceUsd": 0.0},
    "hosted-mcp-tools-growth": {"product": "hosted-mcp-tools", "seats": 10, "credits": 150, "mcpCalls": 10_000, "paidComputeCalls": 500, "inferenceUsd": 0.0},
    "hosted-mcp-tools-team": {"product": "hosted-mcp-tools", "seats": 50, "credits": 500, "mcpCalls": 50_000, "paidComputeCalls": 2_500, "inferenceUsd": 0.0},
    "hosted-mcp-inference-starter": {"product": "hosted-mcp-tools-inference", "seats": 5, "credits": 100, "mcpCalls": 2_000, "paidComputeCalls": 100, "inferenceUsd": 10.0},
    "hosted-mcp-inference-growth": {"product": "hosted-mcp-tools-inference", "seats": 10, "credits": 300, "mcpCalls": 10_000, "paidComputeCalls": 500, "inferenceUsd": 50.0},
    "hosted-mcp-inference-team": {"product": "hosted-mcp-tools-inference", "seats": 50, "credits": 1_000, "mcpCalls": 50_000, "paidComputeCalls": 2_500, "inferenceUsd": 250.0},
}

JOB_PROFILES = {
    "hedge": {
        "description": "15-minute status/account reads, hedge proposal/backtest, and occasional confirmed hedge execution.",
        "usage": {"seats": 2, "freeToolCalls": 8_640, "safetyGatedCalls": 30, "paidComputeCalls": 30, "inferenceUsd": 5.0},
    },
    "maker": {
        "description": "Continuous quoting desk with frequent status/account reads and safety-gated trade submissions.",
        "usage": {"seats": 5, "freeToolCalls": 30_000, "safetyGatedCalls": 15_000, "paidComputeCalls": 150, "inferenceUsd": 0.0},
    },
    "taker": {
        "description": "Signal-driven taker using strategy runs plus bursty execution and post-trade checks.",
        "usage": {"seats": 2, "freeToolCalls": 4_000, "safetyGatedCalls": 1_000, "paidComputeCalls": 500, "inferenceUsd": 15.0},
    },
}

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


def _env_float(name: str, default: float | None = None) -> float | None:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return float(raw)


def railway_rate_card_from_env() -> dict[str, Any]:
    rates = {
        key: _env_float(env_name, 0.0 if key == "baselineMonthlyUsd" else None)
        for key, env_name in RAILWAY_RATE_ENV.items()
    }
    missing = [
        env_name
        for key, env_name in RAILWAY_RATE_ENV.items()
        if key != "baselineMonthlyUsd" and rates[key] is None
    ]
    return {
        "ok": len(missing) == 0,
        "rates": rates,
        "missing": missing,
        "env": RAILWAY_RATE_ENV,
    }


def _metric_number(metrics: dict[str, Any], path: list[str], default: float = 0.0) -> float:
    cur: Any = metrics
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    try:
        value = float(cur)
    except (TypeError, ValueError):
        return default
    return value if value == value else default


def _hours_from_since(value: str) -> float:
    text = str(value or "1h").strip().lower()
    if text.endswith("m"):
        return max(float(text[:-1]) / 60, 0.0)
    if text.endswith("h"):
        return max(float(text[:-1]), 0.0)
    if text.endswith("d"):
        return max(float(text[:-1]) * 24, 0.0)
    try:
        return max(float(text), 0.0)
    except ValueError:
        return 1.0


def estimate_railway_cost(metrics: dict[str, Any], rate_card: dict[str, Any], *, hours: float) -> dict[str, Any]:
    rates = rate_card.get("rates", rate_card)
    if not rate_card.get("ok", True):
        return {
            "ok": False,
            "blocker": "Missing Railway rate-card env vars.",
            "missing": rate_card.get("missing", []),
        }
    cpu_vcpu = _metric_number(metrics, ["cpu", "average"])
    memory_gb = _metric_number(metrics, ["memory", "average_gb"], _metric_number(metrics, ["memory", "average_mb"]) / 1024)
    network_gb = _metric_number(metrics, ["network", "egress_gb"], _metric_number(metrics, ["network", "egress_mb"]) / 1024)
    disk_gb = _metric_number(metrics, ["disk", "average_gb"], _metric_number(metrics, ["disk", "average_mb"]) / 1024)
    baseline_monthly = float(rates.get("baselineMonthlyUsd") or 0.0)
    components = {
        "cpuUsd": cpu_vcpu * hours * float(rates["vcpuHourUsd"]),
        "memoryUsd": memory_gb * hours * float(rates["memoryGbHourUsd"]),
        "networkUsd": network_gb * float(rates["networkGbUsd"]),
        "diskUsd": disk_gb * hours * float(rates["diskGbHourUsd"]),
        "baselineUsd": baseline_monthly * (hours / MONTHLY_HOURS),
    }
    total = sum(components.values())
    return {
        "ok": True,
        "estimatedRailwayCostUsd": round(total, 6),
        "windowHours": hours,
        "monthlyRunRateUsd": round(total * MONTHLY_HOURS / hours, 2) if hours > 0 else 0.0,
        "inputs": {
            "averageVcpu": cpu_vcpu,
            "averageMemoryGb": memory_gb,
            "networkEgressGb": network_gb,
            "averageDiskGb": disk_gb,
        },
        "components": {key: round(value, 6) for key, value in components.items()},
        "rateCard": rates,
    }


def railway_metrics_probe(args: argparse.Namespace) -> dict[str, Any]:
    """Collect Railway resource metrics when the CLI is available.

    Railway metrics corroborate runtime load, but they are not invoices. C_seat
    still requires an explicit monthly dollar input.
    """
    railway = shutil.which("railway")
    if not railway:
        return {"ok": False, "blocker": "Railway CLI is not installed."}
    command = [railway, "metrics", "--json", "--since", args.railway_metrics_since]
    if args.railway_service:
        command.extend(["--service", args.railway_service])
    if args.railway_environment:
        command.extend(["--environment", args.railway_environment])
    if args.railway_project:
        command.extend(["--project", args.railway_project])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except Exception as exc:  # pragma: no cover - CLI/environment dependent
        return {"ok": False, "command": command[1:], "blocker": str(exc)}
    if result.returncode != 0:
        return {
            "ok": False,
            "command": command[1:],
            "blocker": (result.stderr or result.stdout or "railway metrics failed")[:500],
        }
    try:
        payload: Any = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"raw": result.stdout[:2000]}
    return {
        "ok": True,
        "command": command[1:],
        "source": "railway metrics --json",
        "note": "Resource metrics are converted to estimatedRailwayCostUsd when a Railway unit-rate card is configured.",
        "metrics": payload,
    }


def _fits_plan(usage: dict[str, float], limits: dict[str, Any]) -> bool:
    if usage["inferenceUsd"] > 0 and limits["inferenceUsd"] <= 0:
        return False
    return (
        usage["seats"] <= limits["seats"]
        and usage["creditsUsed"] <= limits["credits"]
        and usage["mcpCalls"] <= limits["mcpCalls"]
        and usage["paidComputeCalls"] <= limits["paidComputeCalls"]
        and usage["inferenceUsd"] <= limits["inferenceUsd"]
    )


def credits_used(usage: dict[str, float]) -> float:
    credits = (
        usage["mcpCalls"] * CREDIT_RATES["mcpCallCredits"]
        + usage["paidComputeCalls"] * CREDIT_RATES["paidComputeCallCredits"]
        + usage["inferenceUsd"] * CREDIT_RATES["inferenceUsdCredits"]
    )
    return round(credits, 2)


def job_profile_simulation(profile_name: str) -> dict[str, Any]:
    profile = JOB_PROFILES[profile_name]
    raw_usage = profile["usage"]
    usage = {
        "seats": raw_usage["seats"],
        "freeToolCalls": raw_usage["freeToolCalls"],
        "safetyGatedCalls": raw_usage["safetyGatedCalls"],
        "paidComputeCalls": raw_usage["paidComputeCalls"],
        "inferenceUsd": raw_usage["inferenceUsd"],
    }
    usage["mcpCalls"] = usage["freeToolCalls"] + usage["safetyGatedCalls"] + usage["paidComputeCalls"]
    usage["creditsUsed"] = credits_used(usage)
    product_prefix = "hosted-mcp-inference" if usage["inferenceUsd"] > 0 else "hosted-mcp-tools"
    candidate_plans = [
        (plan_id, limits)
        for plan_id, limits in PLAN_LIMITS.items()
        if plan_id.startswith(product_prefix)
    ]
    best_plan = next((plan_id for plan_id, limits in candidate_plans if _fits_plan(usage, limits)), None)
    if best_plan is None:
        best_plan = candidate_plans[-1][0]
    limits = PLAN_LIMITS[best_plan]
    overage = {
        "seats": max(0, usage["seats"] - limits["seats"]),
        "credits": max(0, round((usage["creditsUsed"] - limits["credits"]) * 100) / 100),
        "mcpCalls": max(0, usage["mcpCalls"] - limits["mcpCalls"]),
        "paidComputeCalls": max(0, usage["paidComputeCalls"] - limits["paidComputeCalls"]),
        "inferenceUsd": max(0.0, usage["inferenceUsd"] - limits["inferenceUsd"]),
    }
    return {
        "profile": profile_name,
        "description": profile["description"],
        "monthlyUsage": usage,
        "recommendedPlanId": best_plan,
        "planLimits": limits,
        "creditRates": CREDIT_RATES,
        "overageAgainstRecommendedPlan": overage,
        "notes": [
            "Safety-gated execution currently increments total mcpCalls.",
            "Maker profiles are usually constrained by mcpCalls before seats.",
        ],
    }


def job_profile_simulations(selected: list[str]) -> dict[str, Any]:
    names = selected or list(JOB_PROFILES.keys())
    return {name: job_profile_simulation(name) for name in names}


def job_profile_margins(job_profiles: dict[str, Any], railway_cost_estimate: dict[str, Any]) -> dict[str, Any]:
    if not railway_cost_estimate.get("ok"):
        return {
            name: {
                "computed": False,
                "blocker": railway_cost_estimate.get("blocker", "Railway cost estimate unavailable."),
            }
            for name in job_profiles
        }
    runtime_monthly = float(railway_cost_estimate.get("monthlyRunRateUsd") or 0.0)
    margins: dict[str, Any] = {}
    for name, profile in job_profiles.items():
        plan_id = profile["recommendedPlanId"]
        limits = profile["planLimits"]
        usage = profile["monthlyUsage"]
        revenue = PLAN_PRICES_USD[plan_id]
        included_seats = max(float(limits["seats"]), 1.0)
        railway_cogs = runtime_monthly * (float(usage["seats"]) / included_seats)
        inference_cogs = float(usage["inferenceUsd"])
        total_cogs = railway_cogs + inference_cogs
        gross_margin = revenue - total_cogs
        margins[name] = {
            "computed": True,
            "planId": plan_id,
            "revenueUsd": revenue,
            "estimatedRailwayCogsUsd": round(railway_cogs, 2),
            "inferenceCogsUsd": round(inference_cogs, 2),
            "totalEstimatedCogsUsd": round(total_cogs, 2),
            "grossMarginUsd": round(gross_margin, 2),
            "grossMarginPct": round((gross_margin / revenue) * 100, 2) if revenue else None,
            "allocation": {
                "method": "profile seats / plan included seats",
                "profileSeats": usage["seats"],
                "includedSeats": limits["seats"],
                "runtimeMonthlyRunRateUsd": runtime_monthly,
            },
        }
    return margins


def railway_runtime_cost_row(cost_estimate: dict[str, Any], *, service: str = "", ts_ms: int | None = None, source: str = "pricing_measure_railway_sampler") -> dict[str, Any]:
    ts = ts_ms or int(time.time() * 1000)
    service_key = service or "shared"
    return {
        "row_id": f"railway_runtime_cost:{service_key}:{ts}",
        "metric_type": "runtime_cost",
        "row": {
            "ts": ts,
            "estimated_railway_cost_usd": cost_estimate.get("estimatedRailwayCostUsd", 0),
            "window_hours": cost_estimate.get("windowHours", 0),
            "monthly_run_rate_usd": cost_estimate.get("monthlyRunRateUsd", 0),
            "components": cost_estimate.get("components", {}),
            "service": service or None,
            "source": source,
        },
    }


def upload_runtime_cost_row(args: argparse.Namespace, cost_estimate: dict[str, Any]) -> dict[str, Any]:
    if not getattr(args, "emit_railway_runtime_cost", False):
        return {"ok": False, "skipped": "Run with --emit-railway-runtime-cost to upload live cost samples."}
    if not cost_estimate.get("ok"):
        return {"ok": False, "blocker": cost_estimate.get("blocker", "Railway cost estimate unavailable.")}
    url = getattr(args, "metering_usage_url", "") or os.environ.get("NUNCHI_METERING_USAGE_URL") or os.environ.get("MCP_METERING_USAGE_URL")
    token = getattr(args, "metering_token", "") or os.environ.get("NUNCHI_METERING_TOKEN") or os.environ.get("NUNCHI_WEB_AUTH_PAIR_TOKEN")
    account_id = getattr(args, "account_id", "") or os.environ.get("NUNCHI_ACCOUNT_ID", "")
    subscription_id = getattr(args, "subscription_id", "") or os.environ.get("NUNCHI_SUBSCRIPTION_ID", "")
    plan_id = getattr(args, "plan_id", "") or os.environ.get("NUNCHI_PLAN_ID", "hosted-mcp-tools-starter")
    if not url or not token or not account_id:
        return {
            "ok": False,
            "blocker": "Set metering URL, token, and account ID before emitting Railway runtime cost.",
        }
    row = railway_runtime_cost_row(cost_estimate, service=args.railway_service)
    payload = json.dumps({
        "accountId": account_id,
        "subscriptionId": subscription_id,
        "planId": plan_id,
        "rows": [row],
    }).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
        return {
            "ok": True,
            "url": url,
            "rowId": row["row_id"],
            "accepted": body.get("accepted"),
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        return {"ok": False, "status": exc.code, "blocker": exc.reason, "detail": detail}
    except Exception as exc:  # pragma: no cover - network dependent
        return {"ok": False, "blocker": str(exc)}


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
        "max_tokens": OPENROUTER_PROBE_MAX_TOKENS,
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
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        return {"ok": False, "elapsedMs": elapsed_ms, "status": exc.code, "blocker": exc.reason, "detail": detail}
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
    railway_rate_card = railway_rate_card_from_env()
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
    railway_metrics = railway_metrics_probe(args) if args.railway_metrics else {
        "ok": False,
        "skipped": "Run with --railway-metrics to collect Railway resource metrics.",
    }
    railway_cost_estimate = (
        estimate_railway_cost(
            railway_metrics.get("metrics", {}),
            railway_rate_card,
            hours=_hours_from_since(args.railway_metrics_since),
        )
        if railway_metrics.get("ok")
        else {"ok": False, "blocker": railway_metrics.get("skipped") or railway_metrics.get("blocker") or "Railway metrics unavailable."}
    )
    runtime_monthly_for_c_seat = runtime_monthly
    if runtime_monthly_for_c_seat is None and railway_cost_estimate.get("ok"):
        runtime_monthly_for_c_seat = float(railway_cost_estimate["monthlyRunRateUsd"])
    job_profiles = job_profile_simulations(args.job_profile)
    railway_cost_upload = upload_runtime_cost_row(args, railway_cost_estimate)

    blockers: list[str] = []
    if runtime_monthly_for_c_seat is None:
        blockers.append("missing Railway runtime monthly cost estimate for Mode 1 C_seat")
    if getattr(args, "railway_cost_strict", False) and not railway_cost_estimate.get("ok"):
        blockers.append(railway_cost_estimate.get("blocker", "Railway cost estimate unavailable in strict mode."))
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
        "mode1": runtime_c_seat(runtime_monthly_for_c_seat),
        "railwayRateCard": railway_rate_card,
        "railwayMetricsProbe": railway_metrics,
        "railwayCostEstimate": railway_cost_estimate,
        "railwayRuntimeCostUpload": railway_cost_upload,
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
        "jobProfileSimulations": job_profiles,
        "jobProfileMargins": job_profile_margins(job_profiles, railway_cost_estimate),
        "blockers": blockers,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-monthly-usd", type=float, default=None)
    parser.add_argument("--openrouter-live", action="store_true")
    parser.add_argument("--openrouter-model", default=os.environ.get("NUNCHI_PRICING_OPENROUTER_MODEL", "openai/gpt-4.1-mini"))
    parser.add_argument("--job-profile", action="append", choices=sorted(JOB_PROFILES.keys()), default=[])
    parser.add_argument("--railway-metrics", action="store_true")
    parser.add_argument("--railway-project", default=os.environ.get("RAILWAY_PROJECT_ID", ""))
    parser.add_argument("--railway-service", default=os.environ.get("RAILWAY_SERVICE_NAME", ""))
    parser.add_argument("--railway-environment", default=os.environ.get("RAILWAY_ENVIRONMENT", "production"))
    parser.add_argument("--railway-metrics-since", default=os.environ.get("RAILWAY_METRICS_SINCE", "1h"))
    parser.add_argument("--railway-cost-strict", action="store_true")
    parser.add_argument("--emit-railway-runtime-cost", action="store_true")
    parser.add_argument("--metering-usage-url", default=os.environ.get("NUNCHI_METERING_USAGE_URL", ""))
    parser.add_argument("--metering-token", default=os.environ.get("NUNCHI_METERING_TOKEN", ""))
    parser.add_argument("--account-id", default=os.environ.get("NUNCHI_ACCOUNT_ID", ""))
    parser.add_argument("--subscription-id", default=os.environ.get("NUNCHI_SUBSCRIPTION_ID", ""))
    parser.add_argument("--plan-id", default=os.environ.get("NUNCHI_PLAN_ID", "hosted-mcp-tools-starter"))
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
