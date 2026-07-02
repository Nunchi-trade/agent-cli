#!/usr/bin/env python3
"""Continuously meter shared Railway runtime cost into web-auth.

This is the production sampler for hosted MCP Railway COGS. It is intentionally
separate from scripts/pricing_measure.py so deployments can run it as a daemon.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import pricing_measure as pricing  # noqa: E402


def internal_runtime_url(args: argparse.Namespace) -> str:
    explicit = getattr(args, "internal_runtime_url", "") or os.environ.get("NUNCHI_INTERNAL_RAILWAY_RUNTIME_URL", "")
    if explicit:
        return explicit
    base = (getattr(args, "web_auth_api_url", "") or os.environ.get("WEB_AUTH_API_URL", "") or os.environ.get("NUNCHI_WEB_AUTH_API_URL", "")).rstrip("/")
    return f"{base}/api/internal/costing/railway-runtime" if base else ""


def internal_costing_token(args: argparse.Namespace) -> str:
    return (
        getattr(args, "internal_costing_token", "")
        or os.environ.get("INTERNAL_COSTING_DASHBOARD_TOKEN", "")
        or os.environ.get("COSTING_DASHBOARD_TOKEN", "")
    )


def upload_internal_runtime_rows(url: str, token: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not url or not token:
        return {"ok": False, "blocker": "Set NUNCHI_INTERNAL_RAILWAY_RUNTIME_URL or WEB_AUTH_API_URL plus INTERNAL_COSTING_DASHBOARD_TOKEN."}
    payload = json.dumps({"rows": rows}).encode()
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
        return {"ok": True, "url": url, "accepted": body.get("accepted"), "body": body}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        return {"ok": False, "status": exc.code, "blocker": exc.reason, "detail": detail}
    except Exception as exc:  # pragma: no cover - network dependent
        return {"ok": False, "blocker": str(exc)}


def sample_once(args: argparse.Namespace) -> dict[str, Any]:
    rate_card = pricing.railway_rate_card_from_env()
    metrics_probe = pricing.railway_metrics_probe(args)
    cost_estimate = (
        pricing.estimate_railway_cost(
            metrics_probe.get("metrics", {}),
            rate_card,
            hours=pricing._hours_from_since(args.railway_metrics_since),
        )
        if metrics_probe.get("ok")
        else {"ok": False, "blocker": metrics_probe.get("blocker") or "Railway metrics unavailable."}
    )
    if not cost_estimate.get("ok"):
        return {
            "ok": False,
            "generatedAtMs": int(time.time() * 1000),
            "railwayMetricsProbe": metrics_probe,
            "railwayRateCard": rate_card,
            "railwayCostEstimate": cost_estimate,
        }
    row = pricing.railway_runtime_cost_row(
        cost_estimate,
        service=args.railway_service,
        source="railway_cost_meter",
    )
    upload = upload_internal_runtime_rows(
        internal_runtime_url(args),
        internal_costing_token(args),
        [row],
    )
    return {
        "ok": bool(upload.get("ok")),
        "generatedAtMs": int(time.time() * 1000),
        "railwayMetricsProbe": metrics_probe,
        "railwayRateCard": rate_card,
        "railwayCostEstimate": cost_estimate,
        "upload": upload,
        "rowId": row["row_id"],
    }


def run_loop(args: argparse.Namespace) -> int:
    while True:
        result = sample_once(args)
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return 0 if result.get("ok") or not args.strict else 1
        if args.strict and not result.get("ok"):
            return 1
        time.sleep(max(args.interval_seconds, 5))


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuously upload Railway runtime COGS samples to web-auth.")
    parser.add_argument("--once", action="store_true", help="Sample and upload once, then exit.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when a sample/upload fails.")
    parser.add_argument("--interval-seconds", type=int, default=int(os.environ.get("RAILWAY_COST_METER_INTERVAL_SECONDS", "60")))
    parser.add_argument("--internal-runtime-url", default=os.environ.get("NUNCHI_INTERNAL_RAILWAY_RUNTIME_URL", ""))
    parser.add_argument("--web-auth-api-url", default=os.environ.get("WEB_AUTH_API_URL", os.environ.get("NUNCHI_WEB_AUTH_API_URL", "")))
    parser.add_argument("--internal-costing-token", default=os.environ.get("INTERNAL_COSTING_DASHBOARD_TOKEN", os.environ.get("COSTING_DASHBOARD_TOKEN", "")))
    parser.add_argument("--railway-project", default=os.environ.get("RAILWAY_PROJECT_ID", ""))
    parser.add_argument("--railway-service", default=os.environ.get("RAILWAY_SERVICE_NAME", ""))
    parser.add_argument("--railway-environment", default=os.environ.get("RAILWAY_ENVIRONMENT", "production"))
    parser.add_argument("--railway-metrics-since", default=os.environ.get("RAILWAY_METRICS_SINCE", "1m"))
    args = parser.parse_args()
    raise SystemExit(run_loop(args))


if __name__ == "__main__":
    main()
