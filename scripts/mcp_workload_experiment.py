#!/usr/bin/env python3
"""Controlled live MCP workload experiment.

Seeds internal experiment subscriptions in web-auth, mints real hosted MCP
gateway tokens, runs hedge/maker/taker tool mixes, and fetches the internal
margin dashboard for evidence-backed tier analysis.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WEB_AUTH_URL = "https://web-auth-production-4d1b.up.railway.app"
GATEWAY_URL = "https://agent.nunchi.trade"
SERVER_ID = "nunchi_trading"
ADDRESSES = [
    "0xEb1Ba7Fc58b3416361a0EE07d140c91410c0AA8c",
    "0x2f0ba6686208Cb31a319F3F54587b1eF1EF0F87e",
]

PROFILES: dict[str, dict[str, Any]] = {
    "hedge": {
        "planId": "hosted-mcp-inference-growth",
        "tier": "Growth inference tier",
        "monthly": {"seats": 2, "mcpCalls": 8700, "paidComputeCalls": 30, "safetyGatedCalls": 30, "inferenceUsd": 5.0},
        "calls": [
            ("setup_check", {}),
            ("account", {"mainnet": False}),
            ("status", {}),
            ("funding_hedge_propose", {"coin": "BTC", "mainnet": False}),
            ("funding_hedge_backtest", {"coin": "BTC", "days": 7, "notional": 100000}),
            ("funding_hedge_execute", {"coin": "BTC", "dry_run": True, "mainnet": False, "confirmed": True}),
            ("openrouter_chat", {"prompt": "Summarize the risk posture for a small funding hedge experiment in one sentence.", "max_tokens": 24, "temperature": 0.1}),
        ],
    },
    "maker": {
        "planId": "hosted-mcp-tools-team",
        "tier": "Team BYO-inference tier",
        "monthly": {"seats": 5, "mcpCalls": 45150, "paidComputeCalls": 150, "safetyGatedCalls": 15000, "inferenceUsd": 0.0},
        "calls": [
            ("setup_check", {}),
            ("strategies", {}),
            ("account", {"mainnet": False}),
            ("status", {}),
            ("radar_run", {"mock": True}),
            ("reflect_run", {"since": "2026-07-01"}),
            ("run_strategy", {"strategy": "maker", "instrument": "ETH-PERP", "max_ticks": 1, "mock": True, "dry_run": True}),
            ("trade", {"instrument": "ETH-PERP", "side": "buy", "size": 0.001, "mainnet": False, "confirmed": True}),
            ("trade", {"instrument": "ETH-PERP", "side": "sell", "size": 0.001, "mainnet": False, "confirmed": True}),
        ],
    },
    "taker": {
        "planId": "hosted-mcp-inference-growth",
        "tier": "Growth inference tier",
        "monthly": {"seats": 2, "mcpCalls": 5500, "paidComputeCalls": 500, "safetyGatedCalls": 1000, "inferenceUsd": 15.0},
        "calls": [
            ("setup_check", {}),
            ("account", {"mainnet": False}),
            ("status", {}),
            ("radar_run", {"mock": True}),
            ("openrouter_chat", {"prompt": "Choose one cautious taker setup from mocked risk context and explain why in one sentence.", "max_tokens": 24, "temperature": 0.1}),
            ("trade", {"instrument": "BTC-PERP", "side": "buy", "size": 0.001, "mainnet": False, "confirmed": True}),
        ],
    },
}


class HttpError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(str(body))
        self.status = status
        self.body = body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-auth-url", default=os.environ.get("WEB_AUTH_PAIR_API_URL") or WEB_AUTH_URL)
    parser.add_argument("--gateway-url", default=os.environ.get("NUNCHI_MCP_GATEWAY_URL") or GATEWAY_URL)
    parser.add_argument("--connect-token", default=os.environ.get("NUNCHI_CONNECT_API_TOKEN", ""))
    parser.add_argument("--metering-token", default=os.environ.get("INTERNAL_MCP_EXPERIMENT_TOKEN") or os.environ.get("INTERNAL_COSTING_DASHBOARD_TOKEN", ""))
    parser.add_argument("--costing-token", default=os.environ.get("INTERNAL_COSTING_DASHBOARD_TOKEN", ""))
    parser.add_argument("--addresses", nargs="+", default=ADDRESSES)
    parser.add_argument("--profiles", nargs="+", default=list(PROFILES))
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--sleep-after", type=float, default=10.0)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("mcp-workload-%Y%m%dT%H%M%SZ"))
    parser.add_argument("--output", default="")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--cleanup", action="store_true", help="Delete internal experiment subscriptions after collecting results.")
    args = parser.parse_args()

    for name, value in {
        "NUNCHI_CONNECT_API_TOKEN": args.connect_token,
        "INTERNAL_MCP_EXPERIMENT_TOKEN": args.metering_token,
        "INTERNAL_COSTING_DASHBOARD_TOKEN": args.costing_token,
    }.items():
        if not value:
            raise SystemExit(f"missing {name}")

    results: dict[str, Any] = {
        "runId": args.run_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "addresses": args.addresses,
        "inputs": {"cycles": args.cycles, "webAuthUrl": args.web_auth_url, "gatewayUrl": args.gateway_url},
        "profiles": [],
    }
    for profile_name in args.profiles:
        profile = PROFILES[profile_name]
        for index, address in enumerate(args.addresses):
            results["profiles"].append(run_profile(args, profile_name, profile, address, index))

    if args.sleep_after:
        time.sleep(args.sleep_after)
    results["marginDashboard"] = http_json("GET", f"{args.web_auth_url.rstrip('/')}/api/billing/subscription/margin-dashboard", token=args.costing_token)
    results["tierExpectations"] = derive_expectations(results)
    if args.cleanup:
        results["cleanup"] = cleanup_experiment(args)

    out = Path(args.output or f"tmp/{args.run_id}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(out), "profileRuns": len(results["profiles"])}, indent=2))
    failures = [call for profile in results["profiles"] for call in profile["calls"] if not call["ok"]]
    if failures and args.strict:
        print(json.dumps({"failedCalls": failures[:20]}, indent=2), file=sys.stderr)
        return 1
    return 0


def run_profile(args: argparse.Namespace, profile_name: str, profile: dict[str, Any], address: str, index: int) -> dict[str, Any]:
    user_id = f"mcp-exp-{profile_name}-{address[-8:].lower()}"
    subscription_id = f"{args.run_id}-{profile_name}-{index}"
    seed_subscription(args, profile, user_id, address, subscription_id)
    minted = connect_gateway(args, profile_name, profile, user_id, address, subscription_id)
    calls = []
    for cycle in range(args.cycles):
        for tool, body in profile["calls"]:
            started = time.perf_counter()
            try:
                response = http_json("POST", f"{args.gateway_url.rstrip('/')}/v1/servers/{SERVER_ID}/tools/{tool}/call", token=minted["token"], body=body, timeout=60)
                content_error = tool_content_error(response)
                calls.append({
                    "cycle": cycle,
                    "tool": tool,
                    "ok": content_error is None,
                    "status": 200,
                    "elapsedMs": round((time.perf_counter() - started) * 1000, 3),
                    "error": content_error,
                    "preview": preview(response),
                })
            except HttpError as exc:
                calls.append({"cycle": cycle, "tool": tool, "ok": False, "status": exc.status, "elapsedMs": round((time.perf_counter() - started) * 1000, 3), "error": preview(exc.body)})
    return {
        "profile": profile_name,
        "address": address,
        "userId": user_id,
        "accountId": address,
        "subscriptionId": subscription_id,
        "planId": profile["planId"],
        "expectedTier": profile["tier"],
        "monthlyExpectation": profile["monthly"],
        "gatewayTokenId": minted.get("token_id"),
        "calls": calls,
    }


def seed_subscription(args: argparse.Namespace, profile: dict[str, Any], user_id: str, account_id: str, subscription_id: str) -> None:
    http_json(
        "POST",
        f"{args.web_auth_url.rstrip('/')}/api/internal/costing/experiment-subscription",
        token=args.costing_token,
        body={
            "userId": user_id,
            "accountId": account_id,
            "subscriptionId": subscription_id,
            "planId": profile["planId"],
            "status": "active",
            "experimentName": args.run_id,
        },
    )


def connect_gateway(args: argparse.Namespace, profile_name: str, profile: dict[str, Any], user_id: str, account_id: str, subscription_id: str) -> dict[str, Any]:
    web = args.web_auth_url.rstrip("/")
    return http_json(
        "POST",
        f"{args.gateway_url.rstrip('/')}/v1/connect/hosted-trading",
        token=args.connect_token,
        headers={"x-nunchi-user-id": user_id},
        body={
            "workspace_id": args.run_id,
            "agent_id": f"experiment-{profile_name}",
            "permission_tier": "testnet_trading",
            "network": "testnet",
            "ttl_seconds": 3600,
            "max_order_size": 0.01,
            "max_strategy_ticks": 5,
            "require_confirmation": False,
            "account_id": account_id,
            "subscription_id": subscription_id,
            "plan_id": profile["planId"],
            "metering_status_url": f"{web}/api/metering/status",
            "metering_usage_url": f"{web}/api/metering/usage",
            "metering_seats_register_url": f"{web}/api/metering/seats/register",
            "metering_seats_release_url": f"{web}/api/metering/seats/release",
            "metering_token": args.metering_token,
        },
    )


def derive_expectations(results: dict[str, Any]) -> dict[str, Any]:
    account_rows = results.get("marginDashboard", {}).get("rows", [])
    wanted = {run["subscriptionId"] for run in results["profiles"]}
    experiment_rows = [row for row in account_rows if row.get("subscriptionId") in wanted]
    return {
        "source": "Real gateway tool calls plus web-auth internal margin dashboard",
        "profileGuidance": [
            {
                "profile": run["profile"],
                "address": run["address"],
                "subscriptionId": run["subscriptionId"],
                "planId": run["planId"],
                "expectedTier": run["expectedTier"],
                "successfulCalls": sum(1 for call in run["calls"] if call["ok"]),
                "failedCalls": sum(1 for call in run["calls"] if not call["ok"]),
                "monthlyExpectation": run["monthlyExpectation"],
            }
            for run in results["profiles"]
        ],
        "experimentAccounts": experiment_rows,
        "byTier": results.get("marginDashboard", {}).get("byTier", []),
    }


def cleanup_experiment(args: argparse.Namespace) -> dict[str, Any]:
    return http_json(
        "POST",
        f"{args.web_auth_url.rstrip('/')}/api/internal/costing/experiment-subscriptions/cleanup",
        token=args.costing_token,
        body={"experimentName": args.run_id},
    )


def http_json(method: str, url: str, *, token: str = "", headers: dict[str, str] | None = None, body: Any = None, timeout: int = 30) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "accept": "application/json",
            **({"content-type": "application/json"} if body is not None else {}),
            **({"authorization": f"Bearer {token}"} if token else {}),
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        raise HttpError(exc.code, parsed) from exc


def preview(value: Any, limit: int = 500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text[:limit]


def tool_content_error(response: dict[str, Any]) -> str | None:
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    structured = result.get("structuredContent")
    if result.get("isError") is True and isinstance(structured, dict) and structured.get("error"):
        return preview(str(structured.get("error")), 300)
    contents = result.get("content")
    if not isinstance(contents, list):
        return None
    text = "\n".join(
        str(item.get("text", ""))
        for item in contents
        if isinstance(item, dict)
    )
    lowered = text.lower()
    markers = [
        '"error"',
        "traceback",
        "requires a signing context",
        "no signing context",
        "unknown tool",
    ]
    if any(marker in lowered for marker in markers):
        return preview(text, 300)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
