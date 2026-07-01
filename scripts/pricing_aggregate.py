#!/usr/bin/env python3
"""Aggregate MCP/inference pricing ledgers into COGS and launch pricing."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List

HOURS_PER_MONTH = Decimal(24 * 30)
RAILWAY_CPU_USD_PER_VCPU_MONTH = Decimal("20")
RAILWAY_RAM_USD_PER_GB_MONTH = Decimal("10")
RAILWAY_VOLUME_USD_PER_GB_MONTH = Decimal("0.15")
RAILWAY_EGRESS_USD_PER_GB = Decimal("0.05")


def _read_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _all_rows(input_dir: Path, filename: str) -> List[dict]:
    rows: List[dict] = []
    for path in input_dir.rglob(filename):
        rows.extend(_read_jsonl(path))
    return rows


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _percentile(values: List[Decimal], percentile: float) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * percentile))
    return ordered[idx]


def _money(value: Decimal) -> str:
    return f"${float(value):,.4f}"


def _infra_usd_per_agent_hour(args: argparse.Namespace) -> tuple[Decimal, str]:
    if args.infra_usd_per_agent_hour is not None:
        return Decimal(str(args.infra_usd_per_agent_hour)), "manual:--infra-usd-per-agent-hour"

    monthly = (
        Decimal(str(args.railway_vcpu_per_agent)) * RAILWAY_CPU_USD_PER_VCPU_MONTH
        + Decimal(str(args.railway_ram_gb_per_agent)) * RAILWAY_RAM_USD_PER_GB_MONTH
        + Decimal(str(args.railway_volume_gb_per_agent)) * RAILWAY_VOLUME_USD_PER_GB_MONTH
        + Decimal(str(args.railway_egress_gb_per_agent_month)) * RAILWAY_EGRESS_USD_PER_GB
    )
    return monthly / HOURS_PER_MONTH, "railway:cpu+ram+volume+egress"


def aggregate(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    cost_rows = _all_rows(input_dir, "cost_ledger.jsonl")
    runtime_rows = _all_rows(input_dir, "agent_runtime_ledger.jsonl")
    incident_rows = _all_rows(input_dir, "incident_ledger.jsonl")
    trade_rows = _all_rows(input_dir, "trades.jsonl")
    infra_hourly, infra_source = _infra_usd_per_agent_hour(args)

    job_types = sorted({
        *(str(r.get("job_type", "unknown")) for r in cost_rows),
        *(str(r.get("job_type", "unknown")) for r in runtime_rows),
        *(str(r.get("job_type", "unknown")) for r in trade_rows if r.get("job_type")),
    })
    if not job_types:
        print(f"No pricing ledgers found under {input_dir}")
        return 1

    report_rows = []
    for job_type in job_types:
        costs = [r for r in cost_rows if str(r.get("job_type", "unknown")) == job_type]
        runtimes = [r for r in runtime_rows if str(r.get("job_type", "unknown")) == job_type]
        trades = [r for r in trade_rows if str(r.get("job_type", "unknown")) == job_type]

        agents = {str(r.get("agent_id", "")) for r in [*costs, *runtimes, *trades] if r.get("agent_id")}
        users = {str(r.get("user_id", "")) for r in [*costs, *runtimes, *trades] if r.get("user_id")}
        accounts = {str(r.get("account_id", "")) for r in [*costs, *runtimes, *trades] if r.get("account_id")}
        subscriptions = {str(r.get("subscription_id", "")) for r in [*costs, *runtimes, *trades] if r.get("subscription_id")}
        llm_total = sum((_decimal(r.get("usd_cost")) for r in costs), Decimal("0"))
        fee_total = sum((_decimal(r.get("fee")) for r in trades), Decimal("0"))
        input_token_total = sum((_decimal(r.get("input_tokens")) for r in costs), Decimal("0"))
        cached_token_total = sum((_decimal(r.get("cached_tokens")) for r in costs), Decimal("0"))
        cache_read_total = sum((_decimal(r.get("cache_read_input_tokens")) for r in costs), Decimal("0"))
        cache_write_total = sum((_decimal(r.get("cache_creation_input_tokens")) for r in costs), Decimal("0"))
        cache_savings_total = sum((_decimal(r.get("cache_savings_usd")) for r in costs), Decimal("0"))
        cache_hit_rate = cached_token_total / input_token_total if input_token_total > 0 else Decimal("0")
        cost_by_decision = defaultdict(lambda: Decimal("0"))
        for row in costs:
            decision_call_id = row.get("decision_call_id")
            if decision_call_id:
                cost_by_decision[str(decision_call_id)] += _decimal(row.get("usd_cost"))
        linked_trade_cost = Decimal("0")
        linked_trade_count = 0
        for row in trades:
            decision_call_id = row.get("decision_call_id")
            if decision_call_id and str(decision_call_id) in cost_by_decision:
                linked_trade_count += 1
                linked_trade_cost += cost_by_decision[str(decision_call_id)]
        avg_llm_per_linked_fill = (
            linked_trade_cost / Decimal(linked_trade_count)
            if linked_trade_count
            else Decimal("0")
        )

        timestamps = [int(r.get("ts", 0)) for r in [*costs, *runtimes] if r.get("ts")]
        if timestamps:
            duration_hours = Decimal(max(1, max(timestamps) - min(timestamps))) / Decimal(1000 * 60 * 60)
        else:
            duration_hours = Decimal("0")

        agent_count = Decimal(max(1, len(agents)))
        infra_total = infra_hourly * duration_hours * agent_count
        observability_total = Decimal(str(args.observability_usd_per_agent_hour)) * duration_hours * agent_count
        total = llm_total + fee_total + infra_total + observability_total

        heartbeat_count = Decimal(len([r for r in runtimes if r.get("event_type") == "heartbeat"]) or len(costs) or 1)
        usd_per_heartbeat = total / heartbeat_count
        usd_per_hour = total / duration_hours if duration_hours > 0 else Decimal("0")
        usd_per_day = usd_per_hour * Decimal(24)
        usd_per_month = usd_per_day * Decimal(30)

        hourly = defaultdict(lambda: Decimal("0"))
        for row in costs:
            ts = int(row.get("ts", 0))
            if ts:
                hourly[ts // (1000 * 60 * 60)] += _decimal(row.get("usd_cost"))
        hourly_values = list(hourly.values()) or [Decimal("0")]
        p50_hourly = Decimal(str(statistics.median(hourly_values)))
        p95_hourly = _percentile(hourly_values, 0.95)
        max_hourly = max(hourly_values)
        fee_hourly = fee_total / duration_hours if duration_hours > 0 else Decimal("0")
        p95_monthly_cogs = (
            p95_hourly
            + (infra_hourly * agent_count)
            + (Decimal(str(args.observability_usd_per_agent_hour)) * agent_count)
            + fee_hourly
        ) * Decimal(24 * 30)

        margin_prices = {
            "70": p95_monthly_cogs / Decimal("0.30"),
            "80": p95_monthly_cogs / Decimal("0.20"),
            "85": p95_monthly_cogs / Decimal("0.15"),
            "90": p95_monthly_cogs / Decimal("0.10"),
        }
        recommended = margin_prices[str(args.target_margin)]

        report_rows.append({
            "job_type": job_type,
            "agent_count": len(agents),
            "user_count": len(users),
            "account_count": len(accounts),
            "subscription_count": len(subscriptions),
            "duration_hours": duration_hours,
            "heartbeat_count": int(heartbeat_count),
            "llm_total": llm_total,
            "infra_total": infra_total,
            "fees_total": fee_total,
            "cached_token_total": cached_token_total,
            "cache_read_total": cache_read_total,
            "cache_write_total": cache_write_total,
            "cache_hit_rate": cache_hit_rate,
            "cache_savings_total": cache_savings_total,
            "linked_trade_count": linked_trade_count,
            "avg_llm_per_linked_fill": avg_llm_per_linked_fill,
            "observability_total": observability_total,
            "total": total,
            "usd_per_heartbeat": usd_per_heartbeat,
            "usd_per_hour": usd_per_hour,
            "usd_per_day": usd_per_day,
            "usd_per_month": usd_per_month,
            "p50_hourly": p50_hourly,
            "p95_hourly": p95_hourly,
            "max_hourly": max_hourly,
            "p95_monthly_cogs": p95_monthly_cogs,
            "margin_prices": margin_prices,
            "recommended": recommended,
            "infra_hourly": infra_hourly,
            "infra_source": infra_source,
        })

    markdown = _render_markdown(input_dir, report_rows, incident_rows, args)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown)
        print(f"Pricing report saved to {out_path}")
    else:
        print(markdown)
    return 0


def _render_markdown(input_dir: Path, rows: List[dict], incidents: List[dict], args: argparse.Namespace) -> str:
    generated = time.strftime("%Y-%m-%d")
    lines = [
        "---",
        "title: MCP and Inference Pricing Results",
        f"date: {generated}",
        "tags: [pricing, mcp, inference, cost-experiment, agent-cli]",
        "---",
        "",
        "# MCP and Inference Pricing Results",
        "",
        "**Source:** [[2026-07-01-mcp-subscription-rework]]",
        "",
        f"Input directory: `{input_dir}`",
        f"Target margin: {args.target_margin}%",
        "",
        "## Mode-Specific Pricing Inputs",
        "",
        *_render_mode_summary(rows, args),
        "",
        "## Executive Recommendation",
        "",
    ]

    for row in rows:
        lines.append(
            f"- `{row['job_type']}`: p95 monthly COGS {_money(row['p95_monthly_cogs'])}; "
            f"recommended launch floor at {args.target_margin}% margin: {_money(row['recommended'])}."
        )

    lines.extend([
        "",
        "## Cost By Job Type",
        "",
        "| Job Type | Users | Accounts | Agents | Subs | Hours | Heartbeats | Linked Fills | Avg LLM/Linked Fill | Cache Hit | Cached Tokens | Cache Savings | LLM | Infra | Fees | Total | USD/Heartbeat | USD/Month | p95 Monthly COGS | Recommended |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])

    for row in rows:
        lines.append(
            f"| `{row['job_type']}` | {row['user_count']} | {row['account_count']} | {row['agent_count']} | "
            f"{row['subscription_count']} | {float(row['duration_hours']):.2f} | "
            f"{row['heartbeat_count']} | {row['linked_trade_count']} | {_money(row['avg_llm_per_linked_fill'])} | "
            f"{float(row['cache_hit_rate']) * 100:.1f}% | {int(row['cached_token_total'])} | {_money(row['cache_savings_total'])} | "
            f"{_money(row['llm_total'])} | {_money(row['infra_total'])} | "
            f"{_money(row['fees_total'])} | {_money(row['total'])} | {_money(row['usd_per_heartbeat'])} | "
            f"{_money(row['usd_per_month'])} | {_money(row['p95_monthly_cogs'])} | {_money(row['recommended'])} |"
        )

    lines.extend([
        "",
        "## Margin Sensitivity",
        "",
        "| Job Type | 70% | 80% | 85% | 90% |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for row in rows:
        prices = row["margin_prices"]
        lines.append(
            f"| `{row['job_type']}` | {_money(prices['70'])} | {_money(prices['80'])} | "
            f"{_money(prices['85'])} | {_money(prices['90'])} |"
        )

    open_incidents = [i for i in incidents if i.get("status", "open") == "open"]
    lines.extend([
        "",
        "## Runtime And Failure Summary",
        "",
        f"- Incident rows: {len(incidents)}",
        f"- Open incidents: {len(open_incidents)}",
        "",
        "## Assumptions",
        "",
        f"- Hosted MCP `C_seat` uses `--hosted-mcp-seats={args.hosted_mcp_seats}` and amortizes shared tools runtime/observability over seats; it is not a per-user autonomous hosted-agent cost.",
        f"- Infra allocation input: {_money(rows[0]['infra_hourly']) if rows else '$0.0000'}/runtime-hour ({rows[0]['infra_source'] if rows else 'unknown'}).",
        f"- Railway assumption: {args.railway_vcpu_per_agent} vCPU, {args.railway_ram_gb_per_agent} GB RAM, "
        f"{args.railway_volume_gb_per_agent} GB volume, {args.railway_egress_gb_per_agent_month} GB monthly egress for the measured runtime allocation.",
        f"- Observability allocation: ${args.observability_usd_per_agent_hour}/runtime-hour.",
        "- Cache savings are reported only when provider usage metadata includes a savings value; otherwise cached tokens and hit rate are shown without assumed dollar savings.",
        "- OpenRouter anchors from current experiments: openrouter/auto ~= $0.0036-$0.00375 per heartbeat; gpt-4.1-mini ~= $0.0002-$0.000222; Fusion capped ~= $0.033 and is premium-only until cheaper routing is proven.",
        "- Pricing uses p95 monthly COGS rather than average COGS.",
        "- Testnet measurements still need mainnet fee and production infra validation before final launch pricing.",
    ])
    return "\n".join(lines) + "\n"


def _render_mode_summary(rows: List[dict], args: argparse.Namespace) -> List[str]:
    if not rows:
        return ["No mode-specific rows available yet."]
    seats = Decimal(max(1, int(args.hosted_mcp_seats)))
    mode_2_inference = sum((row["llm_total"] for row in rows), Decimal("0"))
    mode_3_builder = sum((row["fees_total"] for row in rows), Decimal("0"))
    p95_runtime = sum((row["p95_monthly_cogs"] - row["llm_total"] - row["fees_total"] for row in rows), Decimal("0"))
    c_seat = p95_runtime / seats if seats > 0 else Decimal("0")
    return [
        "| Mode | What Nunchi Pays | Measured Input | Pricing Note |",
        "| --- | --- | ---: | --- |",
        f"| `mode_1_hosted_mcp_tools` | Shared Railway tools runtime, gateway, audit/control plane | `C_seat` ~= {_money(c_seat)} / seat-month at {int(seats)} seats | User pays their own inference; paid tools and call volume decide final tier. |",
        f"| `mode_2_hosted_mcp_tools_inference` | Mode 1 plus Nunchi/OpenRouter budget | `C_inference` observed {_money(mode_2_inference)} in input ledgers | Cheap model default; auto/Fusion are premium cost centers. |",
        f"| `mode_3_clone_local` | No hosted tools/runtime unless user opts in | Builder-fee metadata observed {_money(mode_3_builder)} | Economics are builder-code capture plus optional paid controls. |",
        "",
        "**OpenRouter anchor reminders:** `gpt-4.1-mini` ~= $0.0002/heartbeat, `openrouter/auto` ~= $0.0037/heartbeat, Fusion ~= $0.033/heartbeat in prior capped runs.",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate MCP/inference pricing ledgers")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output")
    parser.add_argument("--infra-usd-per-agent-hour", type=float, default=None)
    parser.add_argument("--railway-vcpu-per-agent", type=float, default=1.0)
    parser.add_argument("--railway-ram-gb-per-agent", type=float, default=1.0)
    parser.add_argument("--railway-volume-gb-per-agent", type=float, default=0.0)
    parser.add_argument("--railway-egress-gb-per-agent-month", type=float, default=0.0)
    parser.add_argument("--observability-usd-per-agent-hour", type=float, default=0.0)
    parser.add_argument("--hosted-mcp-seats", type=int, default=5, help="Seats used to amortize shared hosted MCP runtime cost")
    parser.add_argument("--target-margin", choices=["70", "80", "85", "90"], default="80")
    args = parser.parse_args()
    return aggregate(args)


if __name__ == "__main__":
    raise SystemExit(main())
