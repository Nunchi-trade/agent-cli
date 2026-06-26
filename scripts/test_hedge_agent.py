#!/usr/bin/env python3
"""End-to-end hedge_agent smoke test for Sam.

The script exercises the same CLI path an operator uses:

    python -m cli.main run hedge_agent --mock --max-ticks 1

It seeds a saved long and short position into a temporary StateDB, runs one
mock tick for each side, and validates that the first fill is the expected IOC
hedge. Optional flags can also do a read-only mainnet account check and send
testnet USDC to a provided address.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], *, env: dict[str, str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result


def _seed_position(data_dir: Path, instrument: str, position_qty: float, entry_price: float) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from parent.position_tracker import PositionTracker
    from parent.store import StateDB

    tracker = PositionTracker()
    side = "buy" if position_qty > 0 else "sell"
    tracker.apply_fill(
        "hedge_agent",
        instrument,
        side,
        Decimal(str(abs(position_qty))),
        Decimal(str(entry_price)),
    )

    db = StateDB(path=str(data_dir / "state.db"))
    db.put("tick_count", 0)
    db.put("positions", tracker.to_dict())
    db.put("strategy_id", "hedge_agent")
    db.put("instrument", instrument)
    db.put("start_time_ms", int(time.time() * 1000))
    db.put("order_stats", {"total_placed": 0, "total_filled": 0})
    db.close()


def _write_config(
    path: Path,
    *,
    inventory_threshold: float | None,
    notional_threshold: float | None,
    urgency_factor: float,
    max_hedge_size: float,
    slippage_bps: float,
) -> None:
    params: dict[str, Any] = {
        "urgency_factor": urgency_factor,
        "max_hedge_size": max_hedge_size,
        "slippage_bps": slippage_bps,
    }
    if notional_threshold is None:
        params["inventory_threshold"] = inventory_threshold
    else:
        params["notional_threshold"] = notional_threshold

    lines = ["strategy_params:"]
    for key, value in params.items():
        lines.append(f"  {key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_trades(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    trades: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            trades.append(json.loads(line))
    return trades


def _expected_size(
    *,
    position_qty: float,
    inventory_threshold: float | None,
    urgency_factor: float,
    max_hedge_size: float,
) -> float | None:
    if inventory_threshold is None:
        return None
    excess = abs(position_qty) - inventory_threshold
    if excess <= 0:
        return 0.0
    return round(min(excess * urgency_factor, max_hedge_size), 6)


def _run_case(args: argparse.Namespace, work_root: Path, position_qty: float) -> dict[str, Any]:
    label = "long" if position_qty > 0 else "short"
    data_dir = work_root / label
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "hedge_config.yaml"

    _seed_position(data_dir, args.instrument, position_qty, args.entry_price)
    _write_config(
        config_path,
        inventory_threshold=args.inventory_threshold,
        notional_threshold=args.notional_threshold,
        urgency_factor=args.urgency_factor,
        max_hedge_size=args.max_hedge_size,
        slippage_bps=args.slippage_bps,
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HL_TESTNET"] = "true"

    result = _run(
        [
            sys.executable,
            "-m",
            "cli.main",
            "run",
            "hedge_agent",
            "--instrument",
            args.instrument,
            "--config",
            str(config_path),
            "--data-dir",
            str(data_dir),
            "--tick",
            "0",
            "--max-ticks",
            "1",
            "--mock",
        ],
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{label} hedge run failed with exit code {result.returncode}")

    trades = _read_trades(data_dir / "trades.jsonl")
    if not trades:
        raise RuntimeError(f"{label} hedge run produced no trades")

    first = trades[0]
    expected_side = "sell" if position_qty > 0 else "buy"
    if first.get("side") != expected_side:
        raise RuntimeError(f"{label} expected first hedge side {expected_side}, got {first.get('side')}")

    quantity = float(first["quantity"])
    if quantity <= 0 or quantity > args.max_hedge_size:
        raise RuntimeError(f"{label} invalid hedge quantity {quantity}")

    expected_size = _expected_size(
        position_qty=position_qty,
        inventory_threshold=args.inventory_threshold if args.notional_threshold is None else None,
        urgency_factor=args.urgency_factor,
        max_hedge_size=args.max_hedge_size,
    )
    if expected_size is not None and abs(quantity - expected_size) > 1e-9:
        raise RuntimeError(f"{label} expected hedge quantity {expected_size}, got {quantity}")

    print(f"OK {label}: first hedge {first['side']} {first['quantity']} {first['instrument']} @ {first['price']}")
    return first


def _mainnet_account_check() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HL_TESTNET"] = "false"
    result = _run([sys.executable, "-m", "cli.main", "account", "--mainnet"], env=env)
    if result.returncode != 0:
        raise RuntimeError("mainnet account check failed")
    print("OK mainnet account check completed (read-only)")


def _send_testnet_usdc(address: str, amount: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HL_TESTNET"] = "true"
    result = _run(
        [
            sys.executable,
            "-m",
            "cli.main",
            "money",
            "transfer",
            "usd",
            amount,
            address,
            "--yes",
        ],
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError("testnet USDC transfer failed")
    print(f"OK sent {amount} testnet USDC to {address}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run hedge_agent CLI smoke checks for Sam.")
    parser.add_argument("--instrument", default="ETH-PERP")
    parser.add_argument("--position-qty", type=float, default=5.0, help="Absolute seeded position for long/short cases")
    parser.add_argument("--entry-price", type=float, default=2500.0)
    parser.add_argument("--inventory-threshold", type=float, default=3.0)
    parser.add_argument("--notional-threshold", type=float, default=None)
    parser.add_argument("--urgency-factor", type=float, default=0.5)
    parser.add_argument("--max-hedge-size", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    parser.add_argument("--mainnet-account-check", action="store_true", help="Run read-only hl account --mainnet")
    parser.add_argument("--sam-address", help="Destination address for optional testnet USDC transfer")
    parser.add_argument("--send-testnet-usdc", help="Amount of testnet USDC to send to --sam-address")
    parser.add_argument("--artifacts-dir", type=Path, help="Keep artifacts in this directory instead of a temp dir")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.position_qty <= 0:
        raise SystemExit("--position-qty must be positive")
    if args.notional_threshold is not None:
        args.inventory_threshold = None
    if args.send_testnet_usdc and not args.sam_address:
        raise SystemExit("--send-testnet-usdc requires --sam-address")

    if args.artifacts_dir:
        work_root = args.artifacts_dir.resolve()
        if work_root.exists():
            shutil.rmtree(work_root)
        work_root.mkdir(parents=True)
        cleanup = False
    else:
        tmp = tempfile.TemporaryDirectory(prefix="hedge-agent-")
        work_root = Path(tmp.name)
        cleanup = True

    try:
        print(f"Artifacts: {work_root}")
        _run_case(args, work_root, abs(args.position_qty))
        _run_case(args, work_root, -abs(args.position_qty))

        if args.mainnet_account_check:
            _mainnet_account_check()

        if args.send_testnet_usdc:
            _send_testnet_usdc(args.sam_address, args.send_testnet_usdc)

        print("OK hedge_agent CLI smoke test passed")
        return 0
    finally:
        if cleanup:
            tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
