"""Tests for cli/api/status_reader.py — file-based state readers."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cli.api.status_reader import (
    read_status,
    read_trades,
    read_reflect,
    read_radar,
    write_config_override,
    read_journal,
)


# ---------------------------------------------------------------------------
# read_status
# ---------------------------------------------------------------------------

class TestReadStatus:
    def test_stopped_when_no_files(self, tmp_data_dir):
        result = read_status(tmp_data_dir)
        assert result["status"] == "stopped"

    def test_running_with_apex_state(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        state = {
            "tick_count": 42,
            "daily_pnl": 10.5,
            "total_pnl": 55.0,
            "total_trades": 8,
            "max_slots": 3,
            "slots": [
                {"status": "active", "slot_id": 1, "instrument": "ETH-PERP",
                 "side": "buy", "entry_size": 0.1, "entry_price": 2500,
                 "roe_pct": 1.2, "guard_phase": 2},
            ],
            "preset": "aggressive",
        }
        (apex_dir / "state.json").write_text(json.dumps(state))

        result = read_status(tmp_data_dir)
        assert result["status"] == "running"
        assert result["engine"] == "apex"
        assert result["tick_count"] == 42
        assert result["daily_pnl"] == 10.5
        assert result["total_pnl"] == 55.0
        assert len(result["positions"]) == 1
        assert result["positions"][0]["market"] == "ETH-PERP"

    def test_corrupted_state_json(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        (apex_dir / "state.json").write_text("NOT JSON")

        result = read_status(tmp_data_dir)
        assert result["status"] == "stopped"

    def test_closed_slots_limited_to_five(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        slots = [{"status": "closed", "slot_id": i} for i in range(10)]
        state = {"slots": slots}
        (apex_dir / "state.json").write_text(json.dumps(state))

        result = read_status(tmp_data_dir)
        assert len(result["closed_slots"]) == 5

    def test_network_testnet_default(self, tmp_data_dir, monkeypatch):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        (apex_dir / "state.json").write_text(json.dumps({"slots": []}))
        monkeypatch.setenv("HL_TESTNET", "true")

        result = read_status(tmp_data_dir)
        assert result["network"] == "testnet"

    def test_network_mainnet(self, tmp_data_dir, monkeypatch):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        (apex_dir / "state.json").write_text(json.dumps({"slots": []}))
        monkeypatch.setenv("HL_TESTNET", "false")

        result = read_status(tmp_data_dir)
        assert result["network"] == "mainnet"


# ---------------------------------------------------------------------------
# read_trades
# ---------------------------------------------------------------------------

class TestReadTrades:
    def test_missing_file(self, tmp_data_dir):
        result = read_trades(tmp_data_dir)
        assert result == {"trades": [], "total": 0}

    def test_reads_trades_newest_first(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        trades = [
            {"instrument": "ETH-PERP", "side": "buy", "price": 2500, "quantity": 0.1, "ts": 1},
            {"instrument": "ETH-PERP", "side": "sell", "price": 2510, "quantity": 0.1, "ts": 2},
            {"instrument": "BTC-PERP", "side": "buy", "price": 60000, "quantity": 0.01, "ts": 3},
        ]
        with open(apex_dir / "trades.jsonl", "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

        result = read_trades(tmp_data_dir, limit=2)
        assert result["total"] == 3
        assert len(result["trades"]) == 2
        # newest first
        assert result["trades"][0]["ts"] == 3

    def test_empty_file(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        (apex_dir / "trades.jsonl").write_text("")

        result = read_trades(tmp_data_dir)
        assert result == {"trades": [], "total": 0}


# ---------------------------------------------------------------------------
# read_reflect
# ---------------------------------------------------------------------------

class TestReadReflect:
    def test_missing_dir(self, tmp_data_dir):
        result = read_reflect(tmp_data_dir)
        assert result["report"] is None
        assert result["reports"] == []

    def test_reads_latest_report(self, tmp_data_dir):
        reflect_dir = Path(tmp_data_dir) / "apex" / "reflect"
        reflect_dir.mkdir(parents=True)
        (reflect_dir / "2025-01-01.md").write_text("# Old report")
        (reflect_dir / "2025-01-02.md").write_text("# New report")

        result = read_reflect(tmp_data_dir)
        assert result["report_name"] == "2025-01-02.md"
        assert "New report" in result["report"]
        assert len(result["reports"]) == 2

    def test_empty_reflect_dir(self, tmp_data_dir):
        reflect_dir = Path(tmp_data_dir) / "apex" / "reflect"
        reflect_dir.mkdir(parents=True)

        result = read_reflect(tmp_data_dir)
        assert result["report"] is None
        assert result["reports"] == []


# ---------------------------------------------------------------------------
# read_radar
# ---------------------------------------------------------------------------

class TestReadRadar:
    def test_missing_file(self, tmp_data_dir):
        result = read_radar(tmp_data_dir)
        assert result == {"scans": [], "latest": None}

    def test_reads_primary_path(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        scans = [{"score": 150}, {"score": 200}]
        (apex_dir / "radar-history.json").write_text(json.dumps(scans))

        result = read_radar(tmp_data_dir)
        assert len(result["scans"]) == 2
        assert result["latest"]["score"] == 200

    def test_reads_fallback_path(self, tmp_data_dir):
        radar_dir = Path(tmp_data_dir) / "radar"
        radar_dir.mkdir()
        scans = [{"score": 100}]
        (radar_dir / "scan-history.json").write_text(json.dumps(scans))

        result = read_radar(tmp_data_dir)
        assert len(result["scans"]) == 1
        assert result["latest"]["score"] == 100

    def test_single_object_wrapped_in_list(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        (apex_dir / "radar-history.json").write_text(json.dumps({"score": 99}))

        result = read_radar(tmp_data_dir)
        assert len(result["scans"]) == 1

    def test_corrupted_json(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        (apex_dir / "radar-history.json").write_text("BROKEN")

        result = read_radar(tmp_data_dir)
        assert result == {"scans": [], "latest": None}


# ---------------------------------------------------------------------------
# write_config_override
# ---------------------------------------------------------------------------

class TestWriteConfigOverride:
    def test_creates_file(self, tmp_data_dir):
        config = {"preset": "conservative", "max_slots": 2}
        write_config_override(tmp_data_dir, config)

        written = json.loads((Path(tmp_data_dir) / "apex" / "config-override.json").read_text())
        assert written["preset"] == "conservative"
        assert written["max_slots"] == 2

    def test_creates_parent_dirs(self, tmp_data_dir):
        nested = os.path.join(tmp_data_dir, "deep", "nested")
        write_config_override(nested, {"test": True})
        assert (Path(nested) / "apex" / "config-override.json").exists()

    def test_overwrites_existing(self, tmp_data_dir):
        write_config_override(tmp_data_dir, {"v": 1})
        write_config_override(tmp_data_dir, {"v": 2})

        written = json.loads((Path(tmp_data_dir) / "apex" / "config-override.json").read_text())
        assert written["v"] == 2


# ---------------------------------------------------------------------------
# read_journal
# ---------------------------------------------------------------------------

class TestReadJournal:
    def test_missing_file(self, tmp_data_dir):
        result = read_journal(tmp_data_dir)
        assert result == {"entries": [], "total": 0}

    def test_reads_entries_newest_first(self, tmp_data_dir):
        apex_dir = Path(tmp_data_dir) / "apex"
        apex_dir.mkdir()
        entries = [{"msg": "first"}, {"msg": "second"}, {"msg": "third"}]
        with open(apex_dir / "journal.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        result = read_journal(tmp_data_dir, limit=2)
        assert result["total"] == 3
        assert len(result["entries"]) == 2
        assert result["entries"][0]["msg"] == "third"
