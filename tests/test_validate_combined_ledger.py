import json
from pathlib import Path

from scripts.validate_combined_ledger import validate


def test_validate_combined_ledger_passes_linked_rows(tmp_path: Path):
    data_dir = tmp_path / "run"
    data_dir.mkdir()
    decision_call_id = "exp:run-1:tick-1"
    (data_dir / "cost_ledger.jsonl").write_text(
        json.dumps(
            {
                "decision_call_id": decision_call_id,
                "usd_cost": "0.0001",
                "input_tokens": 10,
                "cached_tokens": 8,
            }
        )
        + "\n"
    )
    (data_dir / "trades.jsonl").write_text(
        json.dumps(
            {
                "decision_call_id": decision_call_id,
                "notional_usd": "57.87",
                "fee": "0.01",
            }
        )
        + "\n"
    )
    assert validate(data_dir) == 0


def test_validate_combined_ledger_fails_orphan_trades(tmp_path: Path):
    data_dir = tmp_path / "run"
    data_dir.mkdir()
    (data_dir / "cost_ledger.jsonl").write_text(json.dumps({"decision_call_id": "a", "usd_cost": "0.1"}) + "\n")
    (data_dir / "trades.jsonl").write_text(json.dumps({"decision_call_id": "b", "notional_usd": "1"}) + "\n")
    assert validate(data_dir) == 1
