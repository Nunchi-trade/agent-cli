#!/usr/bin/env python3
"""Exhibit A §A.6 — MCP workload experiment (5 agents × N days).

Runs concurrent MCP tool-call profiles against web-auth metering endpoints.
Use for cost-per-agent-day measurement before pricing decisions.

Example:
  python scripts/mcp_workload_experiment.py \\
    --agents 5 --days 1 --profile read_only \\
    --metering-url https://auth.nunchi.trade/api/metering/usage \\
    --dry-run

Live (requires tokens):
  export NUNCHI_MCP_TOKEN_0=...  # one bearer per agent
  python scripts/mcp_workload_experiment.py --agents 5 --days 5 --profile mixed
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = REPO_ROOT / "data" / "exhibit-a" / "workload-experiment.jsonl"

PROFILES: dict[str, dict[str, Any]] = {
    "read_only": {
        "tools": ["setup_check", "account", "funding_rates", "funding_hedge_propose"],
        "calls_per_day": 48,
        "interval_s": 1800,
    },
    "mixed": {
        "tools": ["setup_check", "account", "funding_rates", "funding_hedge_propose", "trade"],
        "calls_per_day": 96,
        "interval_s": 900,
    },
}


@dataclass
class CallResult:
    ts: str
    agent_id: str
    profile: str
    tool: str
    ok: bool
    latency_ms: float
    error: str | None = None
    metering_status: int | None = None


@dataclass
class ExperimentReport:
    run_id: str
    agents: int
    days: float
    profile: str
    dry_run: bool
    started_at: str
    ended_at: str | None = None
    total_calls: int = 0
    failed_calls: int = 0
    results: list[CallResult] = field(default_factory=list)


def _post_metering(url: str, token: str, payload: dict[str, Any], dry_run: bool) -> int | None:
    if dry_run or not url:
        return None
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return None


def _simulate_tool_call(agent_id: str, tool: str) -> tuple[bool, str | None]:
    # Local simulation when no gateway URL — counts toward experiment shape.
    time.sleep(0.01)
    if tool == "trade" and os.getenv("EXHIBIT_A_ALLOW_WRITE") != "1":
        return False, "simulated_write_blocked_set_EXHIBIT_A_ALLOW_WRITE=1"
    return True, None


def run_agent(
    *,
    agent_index: int,
    profile_name: str,
    calls_remaining: int,
    metering_url: str,
    dry_run: bool,
    run_id: str,
) -> list[CallResult]:
    profile = PROFILES[profile_name]
    agent_id = f"exhibit-a-{run_id}-agent-{agent_index}"
    token = os.getenv(f"NUNCHI_MCP_TOKEN_{agent_index}", "")
    results: list[CallResult] = []

    for i in range(calls_remaining):
        tool = profile["tools"][i % len(profile["tools"])]
        started = time.perf_counter()
        ok, err = _simulate_tool_call(agent_id, tool)
        latency_ms = (time.perf_counter() - started) * 1000

        metering_status = None
        if ok and metering_url:
            metering_status = _post_metering(
                metering_url,
                token,
                {
                    "metric_type": "mcp_call",
                    "tool": tool,
                    "agent_id": agent_id,
                    "run_id": run_id,
                },
                dry_run=dry_run,
            )

        results.append(
            CallResult(
                ts=datetime.now(timezone.utc).isoformat(),
                agent_id=agent_id,
                profile=profile_name,
                tool=tool,
                ok=ok,
                latency_ms=round(latency_ms, 2),
                error=err,
                metering_status=metering_status,
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Exhibit A MCP workload experiment")
    parser.add_argument("--agents", type=int, default=5, help="Concurrent agents (default 5)")
    parser.add_argument("--days", type=float, default=1.0, help="Experiment duration in days")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="read_only")
    parser.add_argument("--metering-url", default=os.getenv("NUNCHI_METERING_URL", ""))
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true", help="Do not POST to metering API")
    parser.add_argument("--quick", action="store_true", help="1 call per agent (smoke)")
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    profile = PROFILES[args.profile]
    calls_per_agent = 1 if args.quick else max(1, int(profile["calls_per_day"] * args.days))

    report = ExperimentReport(
        run_id=run_id,
        agents=args.agents,
        days=args.days,
        profile=args.profile,
        dry_run=args.dry_run,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    for idx in range(args.agents):
        agent_results = run_agent(
            agent_index=idx,
            profile_name=args.profile,
            calls_remaining=calls_per_agent,
            metering_url=args.metering_url,
            dry_run=args.dry_run,
            run_id=run_id,
        )
        report.results.extend(agent_results)

    report.ended_at = datetime.now(timezone.utc).isoformat()
    report.total_calls = len(report.results)
    report.failed_calls = sum(1 for r in report.results if not r.ok)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(report), separators=(",", ":")) + "\n")

    cost_per_agent_day = report.total_calls / max(args.agents * args.days, 0.001)
    print(json.dumps({
        "run_id": run_id,
        "agents": args.agents,
        "days": args.days,
        "total_calls": report.total_calls,
        "failed_calls": report.failed_calls,
        "calls_per_agent_day": round(cost_per_agent_day, 2),
        "report": str(args.report),
    }, indent=2))

    return 0 if report.failed_calls == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
