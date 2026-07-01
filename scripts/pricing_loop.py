#!/usr/bin/env python3
"""Run hosted-agent pricing qualification scenarios.

Examples:
    python scripts/pricing_loop.py run --scenario pilot_taker --mock --duration-seconds 300
    python scripts/pricing_loop.py run --scenario pilot_monitoring --agents 3 --duration-seconds 86400
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional


SCENARIOS: Dict[str, Dict[str, object]] = {
    "pilot_taker": {
        "agents": 3,
        "strategy": "ai_agent",
        "job_type": "taker",
        "model": "openai/gpt-4o-mini",
    },
    "pilot_taker_control": {
        "agents": 1,
        "strategy": "aggressive_taker",
        "job_type": "taker_control",
        "model": None,
    },
    "pilot_monitoring": {
        "agents": 3,
        "strategy": "strategies.llm_monitoring:LLMMonitoringStrategy",
        "job_type": "monitoring",
        "model": "inclusionai/ling-2.6-flash",
    },
    "pilot_hedge_heartbeat": {
        "agents": 1,
        "strategy": "strategies.llm_monitoring:LLMMonitoringStrategy",
        "job_type": "hedge_heartbeat",
        "model": "openai/gpt-4.1-mini",
    },
    "full_taker": {
        "agents": 10,
        "strategy": "ai_agent",
        "job_type": "taker",
        "model": "openai/gpt-4o-mini",
    },
    "full_monitoring": {
        "agents": 10,
        "strategy": "strategies.llm_monitoring:LLMMonitoringStrategy",
        "job_type": "monitoring",
        "model": "inclusionai/ling-2.6-flash",
    },
}


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _terminate(proc: subprocess.Popen, grace_seconds: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _build_child_cmd(args: argparse.Namespace, strategy: str, model: Optional[str], data_dir: Path) -> List[str]:
    cmd = [
        sys.executable,
        "-m",
        "cli.main",
        "run",
        strategy,
        "--instrument",
        args.instrument,
        "--tick",
        str(args.tick),
        "--data-dir",
        str(data_dir),
        "--fresh",
    ]
    if args.max_ticks:
        cmd.extend(["--max-ticks", str(args.max_ticks)])
    if args.mock:
        cmd.append("--mock")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.mainnet:
        cmd.append("--mainnet")
    if model:
        cmd.extend(["--model", model])
    return cmd


def run(args: argparse.Namespace) -> int:
    scenario = dict(SCENARIOS[args.scenario])
    agent_count = args.agents or int(scenario["agents"])
    strategy = str(args.strategy or scenario["strategy"])
    job_type = str(args.job_type or scenario["job_type"])
    model = args.model if args.model is not None else scenario.get("model")
    model = str(model) if model else None

    experiment_id = args.experiment_id or f"{time.strftime('%Y%m%d')}-{args.scenario}-{uuid.uuid4().hex[:8]}"
    run_id = args.run_id or uuid.uuid4().hex[:12]
    run_dir = Path(args.data_dir) / experiment_id / run_id
    supervisor_log = run_dir / "supervisor_events.jsonl"
    incident_log = run_dir / "incident_ledger.jsonl"

    if args.mainnet or os.environ.get("HL_TESTNET", "true").lower() == "false":
        print("Refusing to run pricing qualification loop outside testnet.", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parent.parent
    children: List[subprocess.Popen] = []

    print(f"Starting scenario={args.scenario} experiment_id={experiment_id} run_id={run_id}")
    print(f"Run directory: {run_dir}")

    for idx in range(agent_count):
        agent_id = f"{job_type}-{idx + 1:02d}"
        agent_dir = run_dir / agent_id
        env = os.environ.copy()
        env.update({
            "PYTHONPATH": str(project_root),
            "HL_TESTNET": "true",
            "DATA_DIR": str(agent_dir),
            "NUNCHI_EXPERIMENT_ID": experiment_id,
            "NUNCHI_RUN_ID": run_id,
            "NUNCHI_AGENT_ID": agent_id,
            "NUNCHI_JOB_TYPE": job_type,
            "NUNCHI_COST_DATA_DIR": str(agent_dir),
            "NUNCHI_RUNTIME_LEDGER_PATH": str(agent_dir / "agent_runtime_ledger.jsonl"),
            "NUNCHI_INCIDENT_LEDGER_PATH": str(agent_dir / "incident_ledger.jsonl"),
        })
        cmd = _build_child_cmd(args, strategy, model, agent_dir)
        stdout_path = agent_dir / "stdout.log"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout = stdout_path.open("a")
        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            env=env,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            text=True,
        )
        children.append(proc)
        _append_jsonl(supervisor_log, {
            "ts": int(time.time() * 1000),
            "event_type": "child_started",
            "experiment_id": experiment_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "job_type": job_type,
            "strategy": strategy,
            "model": model,
            "pid": proc.pid,
            "data_dir": str(agent_dir),
            "cmd": cmd,
        })

    deadline = time.time() + args.duration_seconds if args.duration_seconds else None
    exit_code = 0

    try:
        while True:
            all_done = True
            for idx, proc in enumerate(children):
                agent_id = f"{job_type}-{idx + 1:02d}"
                status = proc.poll()
                if status is None:
                    all_done = False
                    continue
                if status != 0:
                    exit_code = status
                    _append_jsonl(incident_log, {
                        "ts": int(time.time() * 1000),
                        "experiment_id": experiment_id,
                        "run_id": run_id,
                        "agent_id": agent_id,
                        "job_type": job_type,
                        "failure_type": "child_exit_nonzero",
                        "severity": "critical",
                        "description": f"Child process exited with code {status}",
                        "impact_on_cost_data": "run ended before duration completed",
                        "recoverable": True,
                        "rerun_required": True,
                        "fix_owner": "",
                        "status": "open",
                    })
                    if args.stop_on_failure:
                        raise KeyboardInterrupt
            if all_done:
                break
            if deadline and time.time() >= deadline:
                break
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("Stopping children...")
    finally:
        for proc in children:
            _terminate(proc)
        _append_jsonl(supervisor_log, {
            "ts": int(time.time() * 1000),
            "event_type": "run_finished",
            "experiment_id": experiment_id,
            "run_id": run_id,
            "exit_code": exit_code,
        })

    print(f"Pricing loop run complete: {run_dir}")
    print(f"Aggregate with: python scripts/pricing_aggregate.py --input-dir {run_dir}")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Hosted-agent pricing qualification loop")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Run a pricing qualification scenario")
    run_parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    run_parser.add_argument("--agents", type=int)
    run_parser.add_argument("--strategy")
    run_parser.add_argument("--job-type")
    run_parser.add_argument("--model")
    run_parser.add_argument("--instrument", default="ETH-PERP")
    run_parser.add_argument("--tick", type=float, default=10.0)
    run_parser.add_argument("--max-ticks", type=int, default=0)
    run_parser.add_argument("--duration-seconds", type=int, default=0)
    run_parser.add_argument("--poll-seconds", type=float, default=5.0)
    run_parser.add_argument("--data-dir", default="data/pricing")
    run_parser.add_argument("--experiment-id")
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--mock", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--mainnet", action="store_true")
    run_parser.add_argument("--stop-on-failure", action="store_true", default=True)
    run_parser.set_defaults(func=run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
