#!/usr/bin/env python3
"""Run bounded MCP/inference pricing experiments and emit aggregate reports."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], *, env: dict | None = None) -> int:
    print("+", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT), env=env)
    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run mode-specific MCP/inference pricing experiment suite")
    parser.add_argument(
        "--suite",
        choices=["cache", "monitoring", "hedge_heartbeat", "combined_dry_run", "all"],
        default="all",
    )
    parser.add_argument("--experiment-id", default=f"pricing-suite-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}")
    parser.add_argument("--data-root", default="data/pricing_suite")
    parser.add_argument("--skip-live", action="store_true", help="Skip OpenRouter live calls")
    parser.add_argument("--dry-run-only", action="store_true", help="Run only non-funded/non-secret dry-run measurements")
    parser.add_argument("--combined-price", type=float, default=24000.0)
    return parser.parse_args()


def _write_manifest(root: Path, args: argparse.Namespace, blocked: list[str]) -> None:
    manifest = {
        "experiment_id": args.experiment_id,
        "suite": args.suite,
        "skip_live": bool(args.skip_live),
        "dry_run_only": bool(args.dry_run_only),
        "generated_at_ms": int(time.time() * 1000),
        "modes": {
            "mode_1_hosted_mcp_tools": "dry-run/runtime allocation only unless Railway runner metrics are supplied",
            "mode_2_hosted_mcp_tools_inference": "OpenRouter measurements skipped when --skip-live or --dry-run-only is set",
            "mode_3_clone_local": "builder-fee economics reported from dry-run/ledger metadata; fill validation needs funded wallets",
        },
        "blocked_live_measurements": blocked,
    }
    (root / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")


def main() -> int:
    args = parse_args()
    if args.dry_run_only:
        args.skip_live = True
        if args.suite == "all":
            args.suite = "combined_dry_run"
    root = Path(args.data_root) / args.experiment_id
    root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("HL_TESTNET", "true")
    exit_code = 0
    blocked = []
    if args.skip_live:
        blocked.append("OpenRouter anchor measurements skipped because --skip-live/--dry-run-only was set.")
    if not (os.environ.get("HL_TESTNET_MAKER_PRIVATE_KEY") and os.environ.get("HL_TESTNET_TAKER_PRIVATE_KEY")):
        blocked.append("Fill-level maker/taker validation blocked: funded HL_TESTNET_MAKER_PRIVATE_KEY and HL_TESTNET_TAKER_PRIVATE_KEY are not configured.")
    _write_manifest(root, args, blocked)

    if args.suite in {"cache", "all"} and not args.skip_live:
        cache_dir = root / "cache"
        code = _run(
            [
                sys.executable,
                "scripts/cache_savings_experiment.py",
                "--rounds",
                "6",
                "--experiment-id",
                f"{args.experiment_id}-cache",
                "--data-dir",
                str(cache_dir),
            ],
            env={**env, "NUNCHI_EXPERIMENT_ID": f"{args.experiment_id}-cache", "NUNCHI_COST_DATA_DIR": str(cache_dir)},
        )
        exit_code = exit_code or code
        _run([sys.executable, "scripts/pricing_aggregate.py", "--input-dir", str(cache_dir)])

    if args.suite in {"monitoring", "all"}:
        monitoring_dir = root / "monitoring"
        monitoring_experiment = f"{args.experiment_id}-monitoring"
        code = _run(
            [
                sys.executable,
                "scripts/pricing_loop.py",
                "run",
                "--scenario",
                "pilot_monitoring",
                "--agents",
                "1",
                "--duration-seconds",
                "60",
                "--tick",
                "10",
                "--data-dir",
                str(monitoring_dir),
                "--experiment-id",
                monitoring_experiment,
            ],
            env=env,
        )
        exit_code = exit_code or code
        for run_dir in monitoring_dir.rglob("cost_ledger.jsonl"):
            _run([sys.executable, "scripts/pricing_aggregate.py", "--input-dir", str(run_dir.parent.parent)])

    if args.suite in {"hedge_heartbeat", "all"}:
        hedge_dir = root / "hedge_heartbeat"
        hedge_experiment = f"{args.experiment_id}-hedge"
        code = _run(
            [
                sys.executable,
                "scripts/pricing_loop.py",
                "run",
                "--scenario",
                "pilot_hedge_heartbeat",
                "--agents",
                "1",
                "--duration-seconds",
                "60",
                "--tick",
                "10",
                "--data-dir",
                str(hedge_dir),
                "--experiment-id",
                hedge_experiment,
            ],
            env=env,
        )
        exit_code = exit_code or code
        for run_dir in hedge_dir.rglob("cost_ledger.jsonl"):
            _run([sys.executable, "scripts/pricing_aggregate.py", "--input-dir", str(run_dir.parent.parent)])

    if args.suite in {"combined_dry_run", "all"}:
        combined_dir = root / "combined"
        combined_dir.mkdir(parents=True, exist_ok=True)
        combined_env = {
            **env,
            "NUNCHI_EXPERIMENT_ID": f"{args.experiment_id}-combined",
            "NUNCHI_COST_DATA_DIR": str(combined_dir),
        }
        combined_cmd = [
            sys.executable,
            "scripts/funded_btcswp_combined_run.py",
            "--price",
            str(args.combined_price),
            "--dry-run",
            "--data-dir",
            str(combined_dir),
        ]
        if args.skip_live:
            combined_cmd.append("--skip-llm")
        code = _run(combined_cmd, env=combined_env)
        exit_code = exit_code or code
        if not args.skip_live:
            _run([sys.executable, "scripts/validate_combined_ledger.py", "--input-dir", str(combined_dir)])
        _run([sys.executable, "scripts/pricing_aggregate.py", "--input-dir", str(combined_dir)])

    print(f"Experiment suite complete under {root}")
    if blocked:
        print("Blocked live measurements:")
        for item in blocked:
            print(f"- {item}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
