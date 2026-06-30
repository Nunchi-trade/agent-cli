#!/usr/bin/env python3
"""Run a funded BTCSWP decision+trade smoke path with joinable ledgers.

This script does not mock the LLM or execution path. It records a real
OpenRouter decision call when enabled, then shells into `hl trade` with the
same decision_call_id so cost_ledger/route_ledger rows join to trades.jsonl.
Live orders require --confirm.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.cost_metering import CostMeter, ExperimentContext  # noqa: E402


def _usage_value(usage, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if value is not None:
            return int(value or 0)
    if hasattr(usage, "model_dump"):
        data = usage.model_dump()
        for name in names:
            if name in data:
                return int(data.get(name) or 0)
    return 0


def _usage_cost(usage) -> Optional[object]:
    cost = getattr(usage, "cost", None)
    if cost is not None:
        return cost
    if hasattr(usage, "model_extra"):
        cost = usage.model_extra.get("cost")
        if cost is not None:
            return cost
    if hasattr(usage, "model_dump"):
        return usage.model_dump().get("cost")
    return None


def _record_openrouter_decision(args: argparse.Namespace, decision_call_id: str) -> Optional[str]:
    if args.skip_llm:
        return None

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY or AI_API_KEY is required unless --skip-llm is used")

    import openai

    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "https://agent.nunchi.trade"),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Nunchi Funded BTCSWP Cost Run"),
        },
    )
    prompt = (
        "You are approving a bounded Hyperliquid testnet smoke trade for cost measurement. "
        f"Instrument={args.instrument}, side={args.side}, size={args.size}, price={args.price}, "
        f"max_notional_usd={args.max_notional_usd}. Return a short JSON object with approve=true "
        "only if this remains within the stated cap."
    )
    started = time.time()
    response = client.chat.completions.create(
        model=args.model,
        messages=[
            {"role": "system", "content": "You approve or refuse tiny testnet trading smoke tests."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=96,
    )
    elapsed_ms = (time.time() - started) * 1000
    usage = response.usage
    generation_id = getattr(response, "id", None)

    meter = CostMeter.from_env("funded_btcswp_combined_run")
    if meter is not None and usage is not None:
        resolved_model = getattr(response, "model", None) or args.model
        meter.record_llm_call(
            provider="openrouter",
            requested_model=args.model,
            resolved_model=resolved_model,
            route=args.model,
            input_tokens=_usage_value(usage, "prompt_tokens", "input_tokens"),
            output_tokens=_usage_value(usage, "completion_tokens", "output_tokens"),
            tick_index=args.tick_index,
            elapsed_ms=elapsed_ms,
            decision_call_id=decision_call_id,
            actual_usd_cost=_usage_cost(usage),
            route_metadata={"generation_id": generation_id} if generation_id else None,
        )
    print(f"Recorded LLM decision {decision_call_id} generation={generation_id or 'unknown'}")
    return generation_id


def _run_trade(
    *,
    args: argparse.Namespace,
    side: str,
    key_env_name: Optional[str],
    decision_call_id: str,
    generation_id: Optional[str],
    tif: str,
) -> None:
    env = os.environ.copy()
    if key_env_name:
        private_key = env.get(key_env_name)
        if not private_key:
            raise RuntimeError(f"{key_env_name} is not set")
        env["HL_PRIVATE_KEY"] = private_key
    env.setdefault("HL_TESTNET", "true")

    cmd = [
        sys.executable,
        "-m",
        "cli.main",
        "trade",
        args.instrument,
        side,
        str(args.size),
        "--price",
        str(args.price),
        "--tif",
        tif,
        "--max-notional",
        str(args.max_notional_usd),
        "--decision-call-id",
        decision_call_id,
        "--tick-index",
        str(args.tick_index),
    ]
    if generation_id:
        cmd.extend(["--generation-id", generation_id])
    if args.mainnet:
        cmd.append("--mainnet")
    if args.confirm:
        cmd.append("--yes")
    if args.dry_run or not args.confirm:
        cmd.append("--dry-run")

    print("Running:", " ".join(cmd[:3] + ["...", *cmd[4:]]))
    result = subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=120)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Funded BTCSWP combined cost/fill smoke run")
    parser.add_argument("--instrument", default="osrs:BTCSWP")
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument("--size", type=float, default=0.001)
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--max-notional-usd", type=float, default=25.0)
    parser.add_argument("--tick-index", type=int, default=1)
    parser.add_argument("--model", default=os.environ.get("AI_MODEL", "openrouter/auto"))
    parser.add_argument("--confirm", action="store_true", help="Submit live orders. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run trade calls.")
    parser.add_argument("--mainnet", action="store_true", help="Use mainnet. Default is testnet.")
    parser.add_argument("--skip-llm", action="store_true", help="Do not call OpenRouter.")
    parser.add_argument("--maker-taker", action="store_true", help="Place maker ALO then taker IOC using env-key names.")
    parser.add_argument("--maker-key-env", default="HL_TESTNET_MAKER_PRIVATE_KEY")
    parser.add_argument("--taker-key-env", default="HL_TESTNET_TAKER_PRIVATE_KEY")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mainnet and not args.confirm:
        raise SystemExit("Mainnet requires --confirm.")
    context = ExperimentContext.from_env("funded_btcswp_combined_run")
    run_id = context.run_id if context.enabled else os.environ.get("NUNCHI_RUN_ID", f"manual-{int(time.time())}")
    decision_call_id = f"funded_btcswp_combined_run:{run_id}:tick-{args.tick_index}"
    generation_id = _record_openrouter_decision(args, decision_call_id)

    if args.maker_taker:
        maker_side = "sell" if args.side == "buy" else "buy"
        _run_trade(
            args=args,
            side=maker_side,
            key_env_name=args.maker_key_env,
            decision_call_id=decision_call_id,
            generation_id=generation_id,
            tif="Alo",
        )
        _run_trade(
            args=args,
            side=args.side,
            key_env_name=args.taker_key_env,
            decision_call_id=decision_call_id,
            generation_id=generation_id,
            tif="Ioc",
        )
    else:
        _run_trade(
            args=args,
            side=args.side,
            key_env_name=None,
            decision_call_id=decision_call_id,
            generation_id=generation_id,
            tif="Ioc",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
