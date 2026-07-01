#!/usr/bin/env python3
"""Run a funded BTCSWP decision+trade smoke path with joinable ledgers.

This script does not mock the LLM or execution path. It records a real
OpenRouter decision call when enabled, then shells into `hl trade` with the
same decision_call_id so cost_ledger/route_ledger rows join to trades.jsonl.
Live orders require --confirm.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.builder_fee import BuilderFeeConfig  # noqa: E402
from modules.cost_metering import CostMeter, ExperimentContext  # noqa: E402
from modules.openrouter_usage import extract_cache_metrics, usage_cost, usage_value  # noqa: E402
from parent.store import JSONLStore  # noqa: E402


def _hyperliquid_info(payload: dict, *, testnet: bool = True) -> Any:
    base = "https://api.hyperliquid-testnet.xyz" if testnet else "https://api.hyperliquid.xyz"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base}/info",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _recent_fills(address: str, *, coin: str, testnet: bool = True, limit: int = 20) -> List[dict]:
    fills = _hyperliquid_info({"type": "userFills", "user": address, "aggregateByTime": True}, testnet=testnet)
    if not isinstance(fills, list):
        return []
    matched = [fill for fill in fills if str(fill.get("coin", "")).upper() == coin.upper()]
    return matched[:limit]


def _wait_for_fill(
    *,
    address: str,
    coin: str,
    started_ms: int,
    timeout_seconds: float,
    testnet: bool,
) -> Optional[dict]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for fill in _recent_fills(address, coin=coin, testnet=testnet):
            fill_time = int(fill.get("time") or 0)
            if fill_time >= started_ms - 5_000:
                return fill
        time.sleep(1.0)
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
    input_tokens = usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = usage_value(usage, "completion_tokens", "output_tokens")
    cache_metrics = extract_cache_metrics(usage, input_tokens=input_tokens)

    meter = CostMeter.from_env("funded_btcswp_combined_run")
    if meter is not None and usage is not None:
        resolved_model = getattr(response, "model", None) or args.model
        meter.record_llm_call(
            provider="openrouter",
            requested_model=args.model,
            resolved_model=resolved_model,
            route=args.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tick_index=args.tick_index,
            elapsed_ms=elapsed_ms,
            decision_call_id=decision_call_id,
            actual_usd_cost=usage_cost(usage),
            route_metadata={"generation_id": generation_id} if generation_id else None,
            **cache_metrics,
        )
    print(
        f"Recorded LLM decision {decision_call_id} generation={generation_id or 'unknown'} "
        f"cached={cache_metrics.get('cached_tokens', 0)} cost={usage_cost(usage)}"
    )
    return generation_id


def _run_trade(
    *,
    args: argparse.Namespace,
    side: str,
    key_env_name: Optional[str],
    decision_call_id: str,
    generation_id: Optional[str],
    tif: str,
) -> subprocess.CompletedProcess:
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

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=120)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    return result


def _record_dry_run_trade(
    *,
    args: argparse.Namespace,
    context: ExperimentContext,
    decision_call_id: str,
    generation_id: Optional[str],
    side: str,
    tif: str,
) -> None:
    trade_log = JSONLStore(os.environ.get("NUNCHI_TRADE_LEDGER_PATH") or str(Path(args.data_dir) / "trades.jsonl"))
    builder_cfg = BuilderFeeConfig.from_env()
    trade_log.append({
        **context.ledger_fields(),
        "ts": int(time.time() * 1000),
        "tick": args.tick_index,
        "tick_index": args.tick_index,
        "decision_call_id": decision_call_id,
        "generation_id": generation_id,
        "oid": None,
        "cloid": None,
        "instrument": args.instrument,
        "side": side,
        "price": str(args.price),
        "quantity": str(args.size),
        "notional_usd": str(abs(args.price * args.size)),
        "timestamp_ms": int(time.time() * 1000),
        "fee": "0",
        "strategy": "funded_btcswp_combined_run",
        "route": "scripts.funded_btcswp_combined_run",
        "network": "mainnet" if args.mainnet else "testnet",
        "tif": tif,
        "dry_run": True,
        "fill_status": "dry_run_no_submission",
        **builder_cfg.metadata(),
    })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Funded BTCSWP combined cost/fill smoke run")
    parser.add_argument("--instrument", default="osrs:BTCSWP")
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument("--size", type=float, default=0.001)
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--max-notional-usd", type=float, default=25.0)
    parser.add_argument("--tick-index", type=int, default=1)
    parser.add_argument("--model", default=os.environ.get("AI_MODEL", "openai/gpt-4o-mini"))
    parser.add_argument("--confirm", action="store_true", help="Submit live orders. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run trade calls.")
    parser.add_argument("--mainnet", action="store_true", help="Use mainnet. Default is testnet.")
    parser.add_argument("--skip-llm", action="store_true", help="Do not call OpenRouter.")
    parser.add_argument("--maker-taker", action="store_true", help="Place maker ALO then taker IOC using env-key names.")
    parser.add_argument("--maker-key-env", default="HL_TESTNET_MAKER_PRIVATE_KEY")
    parser.add_argument("--taker-key-env", default="HL_TESTNET_TAKER_PRIVATE_KEY")
    parser.add_argument("--maker-address-env", default="HL_TESTNET_MAKER_ADDRESS")
    parser.add_argument("--taker-address-env", default="HL_TESTNET_TAKER_ADDRESS")
    parser.add_argument("--validate-fills", action="store_true", help="Poll Hyperliquid fills after live trades.")
    parser.add_argument("--fill-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--data-dir", default=os.environ.get("NUNCHI_COST_DATA_DIR", "data/funded_combined_run"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mainnet and not args.confirm:
        raise SystemExit("Mainnet requires --confirm.")

    os.environ.setdefault("NUNCHI_EXPERIMENT_ID", os.environ.get("NUNCHI_EXPERIMENT_ID", "funded_btcswp_combined_run"))
    os.environ.setdefault("NUNCHI_JOB_TYPE", "taker")
    os.environ.setdefault("NUNCHI_COST_DATA_DIR", args.data_dir)

    context = ExperimentContext.from_env("funded_btcswp_combined_run")
    run_id = context.run_id if context.enabled else os.environ.get("NUNCHI_RUN_ID", f"manual-{int(time.time())}")
    decision_call_id = f"funded_btcswp_combined_run:{run_id}:tick-{args.tick_index}"
    generation_id = _record_openrouter_decision(args, decision_call_id)
    started_ms = int(time.time() * 1000)

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
        if args.dry_run or not args.confirm:
            _record_dry_run_trade(
                args=args,
                context=context,
                decision_call_id=decision_call_id,
                generation_id=generation_id,
                side=maker_side,
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
        if args.dry_run or not args.confirm:
            _record_dry_run_trade(
                args=args,
                context=context,
                decision_call_id=decision_call_id,
                generation_id=generation_id,
                side=args.side,
                tif="Ioc",
            )
        validate_address = os.environ.get(args.taker_address_env, "")
    else:
        _run_trade(
            args=args,
            side=args.side,
            key_env_name=None,
            decision_call_id=decision_call_id,
            generation_id=generation_id,
            tif="Ioc",
        )
        if args.dry_run or not args.confirm:
            _record_dry_run_trade(
                args=args,
                context=context,
                decision_call_id=decision_call_id,
                generation_id=generation_id,
                side=args.side,
                tif="Ioc",
            )
        validate_address = os.environ.get("HL_ADDRESS", "")

    if args.validate_fills and args.confirm and validate_address:
        fill = _wait_for_fill(
            address=validate_address,
            coin=args.instrument.split(":")[-1] if ":" in args.instrument else args.instrument,
            started_ms=started_ms,
            timeout_seconds=args.fill_timeout_seconds,
            testnet=not args.mainnet,
        )
        if fill:
            print(
                "Validated fill:",
                json.dumps(
                    {
                        "coin": fill.get("coin"),
                        "px": fill.get("px"),
                        "sz": fill.get("sz"),
                        "side": fill.get("side"),
                        "time": fill.get("time"),
                    },
                    sort_keys=True,
                ),
            )
        else:
            print("WARN: no recent fill observed within timeout; execution may still have succeeded asynchronously")

    print(f"Validate ledgers: python scripts/validate_combined_ledger.py --input-dir {args.data_dir}")
    print(f"Aggregate report: python scripts/pricing_aggregate.py --input-dir {args.data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
