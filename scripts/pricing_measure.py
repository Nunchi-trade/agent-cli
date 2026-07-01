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
        },
    )


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
        measure_entrypoint_tool("strategies"),
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
