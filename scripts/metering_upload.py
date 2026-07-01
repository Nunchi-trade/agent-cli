#!/usr/bin/env python3
"""Upload hosted-agent metering rows to web-auth.

The hosted runtime keeps local JSONL ledgers as the source of truth, then this
uploader batches unsent rows to the subscription metering API. It is safe to
restart: sent row IDs are persisted locally and web-auth also dedupes rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

LEDGER_FILES = {
    "cost": "cost_ledger.jsonl",
    "route": "route_ledger.jsonl",
    "runtime": "agent_runtime_ledger.jsonl",
    "incident": "incident_ledger.jsonl",
    "trade": "trades.jsonl",
}


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _row_id(ledger: str, row: dict[str, Any]) -> str:
    stable = {
        "ledger": ledger,
        "experiment_id": row.get("experiment_id"),
        "run_id": row.get("run_id"),
        "agent_id": row.get("agent_id"),
        "tick_index": row.get("tick_index") or row.get("tick"),
        "decision_call_id": row.get("decision_call_id"),
        "generation_id": row.get("generation_id") or (row.get("route_metadata") or {}).get("generation_id"),
        "oid": row.get("oid"),
        "ts": row.get("ts") or row.get("timestamp_ms"),
        "event_type": row.get("event_type"),
        "provider": row.get("provider"),
        "usd_cost": row.get("usd_cost"),
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(str(item) for item in data.get("sent_row_ids", []))


def _save_state(path: Path, sent: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sent_row_ids": sorted(sent)[-50_000:], "updated_at_ms": int(time.time() * 1000)}, indent=2)
        + "\n",
        "utf-8",
    )


def collect_rows(data_dir: Path, sent: set[str], limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ledger, filename in LEDGER_FILES.items():
        for row in _read_jsonl(data_dir / filename):
            row_id = _row_id(ledger, row)
            if row_id in sent:
                continue
            rows.append({"row_id": row_id, "ledger": ledger, "row": row})
            if len(rows) >= limit:
                return rows
    return rows


def upload_batch(url: str, token: str, account_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    user_id = os.environ.get("NUNCHI_USER_ID", "")
    req = urllib.request.Request(
        url,
        data=json.dumps({"user_id": user_id, "account_id": account_id, "rows": rows}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"metering upload failed ({exc.code}): {body[:500]}") from exc


def _handle_quota_status(data_dir: Path, result: dict[str, Any]) -> None:
    quota_status = result.get("quotaStatus")
    if not isinstance(quota_status, dict):
        return
    status_path = Path(os.environ.get("NUNCHI_METERING_QUOTA_STATUS_PATH") or data_dir / ".metering_quota_status.json")
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(quota_status, indent=2, sort_keys=True) + "\n", "utf-8")
    action = str(quota_status.get("action") or "observe")
    if action in {"stop", "pause"} and os.environ.get("NUNCHI_METERING_ENFORCE_RUNTIME") == "1":
        os.kill(os.getppid(), signal.SIGTERM)


def run_once(args: argparse.Namespace) -> int:
    url = args.url or os.environ.get("NUNCHI_METERING_URL", "")
    token = args.token or os.environ.get("NUNCHI_METERING_TOKEN", "")
    account_id = args.account_id or os.environ.get("NUNCHI_ACCOUNT_ID", "")
    if not url or not token or not account_id:
        print("Metering disabled: NUNCHI_METERING_URL, NUNCHI_METERING_TOKEN, and NUNCHI_ACCOUNT_ID are required.")
        return 0

    data_dir = Path(args.data_dir or os.environ.get("DATA_DIR", "/data"))
    state_path = Path(args.state_path or os.environ.get("NUNCHI_METERING_STATE_PATH") or data_dir / ".metering_upload_state.json")
    sent = _load_state(state_path)
    rows = collect_rows(data_dir, sent, args.batch_size)
    if not rows:
        print("No new metering rows.")
        return 0

    result = upload_batch(url, token, account_id, rows)
    _handle_quota_status(data_dir, result)
    accepted = result.get("accepted_row_ids") or [row["row_id"] for row in rows]
    sent.update(str(row_id) for row_id in accepted)
    _save_state(state_path, sent)
    print(f"Uploaded {len(accepted)} metering rows to web-auth.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload hosted-agent metering rows")
    parser.add_argument("--data-dir")
    parser.add_argument("--url")
    parser.add_argument("--token")
    parser.add_argument("--account-id")
    parser.add_argument("--state-path")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=float(os.environ.get("NUNCHI_METERING_UPLOAD_INTERVAL_S", "60")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.loop:
        return run_once(args)
    while True:
        try:
            run_once(args)
        except Exception as exc:
            print(f"Metering upload error: {exc}")
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
