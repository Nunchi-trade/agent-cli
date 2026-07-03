"""Tests for the APEX/REFLECT operator loop.

Covers the PR5 surfaces:
  - `apex status --json` emits valid, machine-readable JSON.
  - `apex proof` / `reflect proof` run deterministically with no live orders.
  - `reflect schedule --once/--dry` writes a report artifact + an events.jsonl line.
  - the shared cli.events.append_event helper round-trips.
  - only the approved APEX/REFLECT codenames appear in the new surfaces.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.commands.apex import apex_app
from cli.commands.reflect import reflect_app
from cli.events import append_event, read_events, events_path
from modules.apex_state import ApexState, ApexStateStore
from modules import proof_fixtures as fx

runner = CliRunner()


# ── cli.events helper ────────────────────────────────────────────────────────

def test_append_event_writes_and_reads_back(tmp_path):
    rec = append_event(str(tmp_path), {"type": "unit_test", "value": 42})
    assert rec["type"] == "unit_test"
    assert "ts_ms" in rec and isinstance(rec["ts_ms"], int)
    assert "ts" in rec and rec["ts"].endswith("Z")

    events = read_events(str(tmp_path))
    assert len(events) == 1
    assert events[0]["value"] == 42
    assert events_path(str(tmp_path)).name == "events.jsonl"


def test_append_event_does_not_mutate_caller_dict(tmp_path):
    payload = {"type": "x"}
    append_event(str(tmp_path), payload)
    assert payload == {"type": "x"}  # no ts_ms/ts injected into caller's dict


def test_append_event_appends(tmp_path):
    append_event(str(tmp_path), {"type": "a"})
    append_event(str(tmp_path), {"type": "b"})
    events = read_events(str(tmp_path))
    assert [e["type"] for e in events] == ["a", "b"]


# ── apex status --json ───────────────────────────────────────────────────────

def test_apex_status_json_no_state(tmp_path):
    res = runner.invoke(apex_app, ["status", "--json", "--data-dir", str(tmp_path / "apex")])
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["found"] is False
    assert payload["slots"] == []
    assert payload["positions"] == []


def test_apex_status_json_with_state(tmp_path):
    data_dir = tmp_path / "apex"
    state = ApexState.from_dict(fx.apex_proof_state())
    ApexStateStore(path=str(data_dir / "state.json")).save(state)

    res = runner.invoke(apex_app, ["status", "--json", "--data-dir", str(data_dir)])
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["found"] is True
    assert payload["run_state"] == "active"
    assert payload["tick_count"] == 7
    assert payload["max_slots"] == 3
    assert payload["active_slots"] == 1
    # One active position with the expected shape
    assert len(payload["positions"]) == 1
    pos = payload["positions"][0]
    for key in ("slot_id", "instrument", "direction", "current_roe", "entry_source"):
        assert key in pos
    assert pos["instrument"] == "ETH-PERP"


def test_apex_status_text_still_works(tmp_path):
    data_dir = tmp_path / "apex"
    state = ApexState.from_dict(fx.apex_proof_state())
    ApexStateStore(path=str(data_dir / "state.json")).save(state)
    res = runner.invoke(apex_app, ["status", "--data-dir", str(data_dir)])
    assert res.exit_code == 0
    assert "Ticks: 7" in res.stdout
    # plain text must not be JSON
    with pytest.raises(json.JSONDecodeError):
        json.loads(res.stdout)


# ── apex proof ───────────────────────────────────────────────────────────────

def test_apex_proof_json_valid_and_no_live_orders(tmp_path):
    data_dir = tmp_path / "apex"
    res = runner.invoke(apex_app, ["proof", "--data-dir", str(data_dir)])
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["proof"] == "apex"
    assert payload["live_orders"] is False
    assert payload["deterministic"] is True
    # fixture drives exactly one hard-stop exit + one pulse entry
    actions = payload["actions"]
    kinds = sorted(a["action"] for a in actions)
    assert kinds == ["enter", "exit"]
    # artifact + events.jsonl were written
    assert (data_dir / "proof" / "apex-proof.json").exists()
    events = read_events(str(data_dir))
    assert any(e["type"] == "apex_proof" for e in events)


def test_apex_proof_is_deterministic(tmp_path):
    d1, d2 = tmp_path / "a1", tmp_path / "a2"
    r1 = runner.invoke(apex_app, ["proof", "--data-dir", str(d1)])
    r2 = runner.invoke(apex_app, ["proof", "--data-dir", str(d2)])
    assert r1.exit_code == r2.exit_code == 0
    # stdout JSON is byte-identical
    assert r1.stdout == r2.stdout
    # on-disk artifact is byte-identical
    a1 = (d1 / "proof" / "apex-proof.json").read_text()
    a2 = (d2 / "proof" / "apex-proof.json").read_text()
    assert a1 == a2


# ── reflect proof ────────────────────────────────────────────────────────────

def test_reflect_proof_json_valid_and_no_live_orders(tmp_path):
    data_dir = tmp_path / "reflect"
    res = runner.invoke(reflect_app, ["proof", "--data-dir", str(data_dir)])
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["proof"] == "reflect"
    assert payload["live_orders"] is False
    m = payload["metrics"]
    assert m["total_round_trips"] == 5
    assert m["orphan_trade_count"] == 1
    # the recommendation pipeline produced at least one rule hit
    assert len(payload["recommendations"]) >= 1
    assert (data_dir / "proof" / "reflect-proof.json").exists()
    events = read_events(str(data_dir))
    assert any(e["type"] == "reflect_proof" for e in events)


def test_reflect_proof_is_deterministic(tmp_path):
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    r1 = runner.invoke(reflect_app, ["proof", "--data-dir", str(d1)])
    r2 = runner.invoke(reflect_app, ["proof", "--data-dir", str(d2)])
    assert r1.exit_code == r2.exit_code == 0
    assert r1.stdout == r2.stdout
    a1 = (d1 / "proof" / "reflect-proof.json").read_text()
    a2 = (d2 / "proof" / "reflect-proof.json").read_text()
    assert a1 == a2


# ── reflect schedule (no waiting) ────────────────────────────────────────────

def test_reflect_schedule_dry_writes_report_and_event(tmp_path):
    out_dir = tmp_path / "reflect"
    res = runner.invoke(reflect_app, [
        "schedule", "--dry",
        "--output-dir", str(out_dir),
        "--data-dir", str(out_dir),
    ])
    assert res.exit_code == 0
    # exactly one report .md + one recommendations .json were written
    reports = list(out_dir.glob("*.md"))
    recs = list(out_dir.glob("*-recommendations.json"))
    assert len(reports) == 1
    assert len(recs) == 1
    assert "REFLECT Report" in reports[0].read_text()
    recs_payload = json.loads(recs[0].read_text())
    assert recs_payload["trigger"] == "schedule_dry"
    assert "recommendations" in recs_payload
    # events.jsonl got a reflect_run summary line
    events = read_events(str(out_dir))
    run_events = [e for e in events if e["type"] == "reflect_run"]
    assert len(run_events) == 1
    assert run_events[0]["round_trips"] == 5
    assert run_events[0]["trigger"] == "schedule_dry"


def test_reflect_schedule_once_no_trades_exits_cleanly(tmp_path):
    # --once without --dry and no trades.jsonl: must not hang, must report.
    res = runner.invoke(reflect_app, [
        "schedule", "--once",
        "--trades-dir", str(tmp_path / "empty"),
        "--output-dir", str(tmp_path / "reflect"),
        "--data-dir", str(tmp_path / "reflect"),
    ])
    assert res.exit_code == 0
    assert "No trades found" in res.stdout


# ── naming guard: APEX/REFLECT only ──────────────────────────────────────────

def test_deprecated_naming_absent_from_new_modules():
    """The operator-loop modules must use APEX/REFLECT naming exclusively.

    The two deprecated codenames are reconstructed from char codes so this
    test file does not itself contain the literal banned tokens (which would
    make it self-defeating).
    """
    root = Path(__file__).resolve().parent.parent
    targets = [
        root / "cli" / "events.py",
        root / "modules" / "proof_fixtures.py",
        root / "cli" / "commands" / "apex.py",
        root / "cli" / "commands" / "reflect.py",
    ]
    banned = ("".join(chr(c) for c in (119, 111, 108, 102)),   # w o l f
             "".join(chr(c) for c in (104, 111, 119, 108)))    # h o w l
    for path in targets:
        text = path.read_text().lower()
        for word in banned:
            assert word not in text, f"banned codename found in {path}"
