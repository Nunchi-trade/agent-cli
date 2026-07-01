#!/usr/bin/env python3
"""Measure prompt-cache hit rate and savings for hosted-agent pricing.

Runs a stable system prompt with repeated user ticks so later calls can reuse
provider prompt cache. Writes rows to cost_ledger.jsonl via CostMeter.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.cost_metering import CostMeter  # noqa: E402
from modules.openrouter_usage import extract_cache_metrics, usage_cost, usage_value  # noqa: E402


SYSTEM_PROMPT = """\
You are a hosted Hyperliquid monitoring agent. Keep answers short.
Use the same operational context every tick:
- Venue: Hyperliquid testnet
- Instruments: ETH-PERP, osrs:BTCSWP
- Risk: alert on drift, stale data, or abnormal funding
- Output: one sentence status only
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure OpenRouter prompt cache savings")
    parser.add_argument("--rounds", type=int, default=6, help="Number of repeated heartbeat calls")
    parser.add_argument("--model", default=os.environ.get("AI_MODEL", "openai/gpt-4o-mini"))
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--experiment-id", default=os.environ.get("NUNCHI_EXPERIMENT_ID", ""))
    parser.add_argument("--data-dir", default=os.environ.get("NUNCHI_COST_DATA_DIR", "data/cache_experiment"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.experiment_id:
        raise SystemExit("Set NUNCHI_EXPERIMENT_ID before running cache_savings_experiment.py")

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY or AI_API_KEY is required")

    os.environ.setdefault("NUNCHI_EXPERIMENT_ID", args.experiment_id)
    os.environ.setdefault("NUNCHI_JOB_TYPE", "cache_experiment")
    os.environ.setdefault("NUNCHI_COST_DATA_DIR", args.data_dir)

    import openai

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "https://agent.nunchi.trade"),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Nunchi Cache Savings Experiment"),
        },
    )

    meter = CostMeter.from_env("cache_savings_experiment")
    if meter is None:
        raise SystemExit("CostMeter disabled; set NUNCHI_EXPERIMENT_ID")

    summaries: List[dict] = []
    for tick in range(1, args.rounds + 1):
        user_prompt = (
            f"Tick {tick}: report portfolio heartbeat status. "
            f"Funding stable, position flat, no action required."
        )
        started = time.time()
        response = client.chat.completions.create(
            model=args.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=64,
        )
        elapsed_ms = (time.time() - started) * 1000
        usage = response.usage
        input_tokens = usage_value(usage, "prompt_tokens", "input_tokens")
        output_tokens = usage_value(usage, "completion_tokens", "output_tokens")
        cache_metrics = extract_cache_metrics(usage, input_tokens=input_tokens)
        decision_call_id = f"cache_savings_experiment:{meter.context.run_id}:tick-{tick}"
        meter.record_llm_call(
            provider="openrouter",
            requested_model=args.model,
            resolved_model=getattr(response, "model", None) or args.model,
            route=args.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tick_index=tick,
            elapsed_ms=elapsed_ms,
            decision_call_id=decision_call_id,
            actual_usd_cost=usage_cost(usage),
            route_metadata={"generation_id": getattr(response, "id", None)},
            **cache_metrics,
        )
        summary = {
            "tick": tick,
            "input_tokens": input_tokens,
            "cached_tokens": cache_metrics.get("cached_tokens", 0),
            "cache_hit_rate": cache_metrics.get("cache_hit_rate", 0.0),
            "cache_savings_usd": cache_metrics.get("cache_savings_usd"),
            "usd_cost": usage_cost(usage),
        }
        summaries.append(summary)
        print(
            f"tick={tick} input={input_tokens} cached={summary['cached_tokens']} "
            f"hit_rate={summary['cache_hit_rate']} cost={summary['usd_cost']}"
        )
        if tick < args.rounds and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    cached_total = sum(int(row.get("cached_tokens") or 0) for row in summaries)
    input_total = sum(int(row.get("input_tokens") or 0) for row in summaries)
    hit_rate = (cached_total / input_total) if input_total else 0.0
    print(f"Aggregate cache hit rate: {hit_rate:.2%} ({cached_total}/{input_total} tokens)")
    print(f"Ledgers written under: {args.data_dir}")
    print(f"Aggregate with: python scripts/pricing_aggregate.py --input-dir {args.data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
