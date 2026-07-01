from __future__ import annotations

import json

from scripts import metering_upload


def test_collect_rows_adds_stable_ids_and_skips_sent(tmp_path):
    row = {
        "experiment_id": "exp",
        "run_id": "run",
        "agent_id": "agent",
        "tick_index": 1,
        "usd_cost": "0.01",
        "provider": "openrouter",
    }
    (tmp_path / "cost_ledger.jsonl").write_text(json.dumps(row) + "\n")

    rows = metering_upload.collect_rows(tmp_path, sent=set(), limit=10)
    assert len(rows) == 1
    assert rows[0]["ledger"] == "cost"
    assert rows[0]["row"] == row
    assert len(rows[0]["row_id"]) == 64

    skipped = metering_upload.collect_rows(tmp_path, sent={rows[0]["row_id"]}, limit=10)
    assert skipped == []


def test_state_round_trip(tmp_path):
    state_path = tmp_path / "state.json"
    metering_upload._save_state(state_path, {"b", "a"})

    assert metering_upload._load_state(state_path) == {"a", "b"}


def test_handle_quota_status_writes_status_file(tmp_path, monkeypatch):
    monkeypatch.delenv("NUNCHI_METERING_ENFORCE_RUNTIME", raising=False)
    result = {"quotaStatus": {"status": "soft_cap", "action": "observe"}}

    metering_upload._handle_quota_status(tmp_path, result)

    status = json.loads((tmp_path / ".metering_quota_status.json").read_text())
    assert status["status"] == "soft_cap"
    assert status["action"] == "observe"
