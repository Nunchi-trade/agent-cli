"""Tests for the Trading Panel data surfaces (PR4).

Covers the JSON contracts on `account`, `status`, `journal view`, and the new
`trading summary` command, plus the read-only / view-only guard. Network and
key-dependent paths are stubbed so these run offline.
"""
from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# view_mode helper
# ---------------------------------------------------------------------------

class TestViewMode:
    ADDR = "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"

    def test_view_address_from_explicit(self):
        from cli.view_mode import view_address
        assert view_address(self.ADDR) == self.ADDR

    def test_view_address_from_env(self, monkeypatch):
        from cli.view_mode import view_address
        monkeypatch.setenv("HL_VIEW_AS_USER", self.ADDR)
        assert view_address() == self.ADDR

    def test_explicit_overrides_env(self, monkeypatch):
        from cli.view_mode import view_address
        monkeypatch.setenv("HL_VIEW_AS_USER", "0x" + "1" * 40)
        assert view_address(self.ADDR) == self.ADDR

    def test_invalid_address_ignored(self):
        from cli.view_mode import view_address, is_view_only
        assert view_address("not-an-address") is None
        assert view_address("0x123") is None  # too short
        assert is_view_only("garbage") is False

    def test_is_view_only_false_without_env(self, monkeypatch):
        from cli.view_mode import is_view_only
        monkeypatch.delenv("HL_VIEW_AS_USER", raising=False)
        assert is_view_only() is False

    def test_require_not_view_only_refuses(self, monkeypatch):
        from cli.view_mode import require_not_view_only
        monkeypatch.setenv("HL_VIEW_AS_USER", self.ADDR)
        with pytest.raises(typer.Exit) as exc:
            require_not_view_only()
        assert exc.value.exit_code == 1

    def test_require_not_view_only_passes_when_off(self, monkeypatch):
        from cli.view_mode import require_not_view_only
        monkeypatch.delenv("HL_VIEW_AS_USER", raising=False)
        # Should not raise.
        require_not_view_only()


class TestViewModeWriteGuards:
    ADDR = "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"

    def _assert_refused(self, result):
        assert result.exit_code == 1
        assert "view-only mode is active" in result.output
        assert "would sign/mutate state" in result.output

    def test_trade_refuses_before_key_load(self, monkeypatch):
        import cli.config as cfgmod

        called = False

        def mark_key_load(self):
            nonlocal called
            called = True
            return "0x" + "1" * 64

        monkeypatch.setenv("HL_VIEW_AS_USER", self.ADDR)
        monkeypatch.setattr(cfgmod.TradingConfig, "get_private_key", mark_key_load)
        result = runner.invoke(app, ["trade", "ETH-PERP", "buy", "0.1"])
        self._assert_refused(result)
        assert called is False

    def test_run_refuses_before_config_load(self, monkeypatch):
        import cli.config as cfgmod

        original_init = cfgmod.TradingConfig.__init__
        called = False

        def mark_init(self, *args, **kwargs):
            nonlocal called
            called = True
            original_init(self, *args, **kwargs)

        monkeypatch.setenv("HL_VIEW_AS_USER", self.ADDR)
        monkeypatch.setattr(cfgmod.TradingConfig, "__init__", mark_init)
        result = runner.invoke(app, ["run", "engine_mm", "--mock", "--max-ticks", "1"])
        self._assert_refused(result)
        assert called is False

    def test_builder_approve_refuses_before_config_load(self, monkeypatch):
        import cli.config as cfgmod

        original_init = cfgmod.TradingConfig.__init__
        called = False

        def mark_init(self, *args, **kwargs):
            nonlocal called
            called = True
            original_init(self, *args, **kwargs)

        monkeypatch.setenv("HL_VIEW_AS_USER", self.ADDR)
        monkeypatch.setattr(cfgmod.TradingConfig, "__init__", mark_init)
        result = runner.invoke(app, ["builder", "approve", "--yes"])
        self._assert_refused(result)
        assert called is False


# ---------------------------------------------------------------------------
# account --json / --address (read-only path stubbed)
# ---------------------------------------------------------------------------

class TestAccountJson:
    ADDR = "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"
    FAKE_STATE = {
        "account_value": 1234.5,
        "total_margin": 10.0,
        "withdrawable": 1224.5,
        "address": ADDR,
        "positions": [],
        "spot_balances": [],
    }

    def test_account_address_json_no_key(self, monkeypatch):
        # Stub the public read so no network/key is needed.
        monkeypatch.setattr(
            "cli.hl_adapter.read_only_account_state",
            lambda address, testnet=True: dict(self.FAKE_STATE, address=address),
        )
        result = runner.invoke(app, ["account", "--address", self.ADDR, "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["address"] == self.ADDR
        assert data["view_only"] is True
        assert data["account_value"] == 1234.5
        assert data["network"] == "testnet"

    def test_account_view_as_user_env(self, monkeypatch):
        monkeypatch.setenv("HL_VIEW_AS_USER", self.ADDR)
        monkeypatch.setattr(
            "cli.hl_adapter.read_only_account_state",
            lambda address, testnet=True: dict(self.FAKE_STATE, address=address),
        )
        result = runner.invoke(app, ["account", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["view_only"] is True
        assert data["address"] == self.ADDR

    def test_account_json_error_when_empty(self, monkeypatch):
        monkeypatch.setattr(
            "cli.hl_adapter.read_only_account_state",
            lambda address, testnet=True: {},
        )
        result = runner.invoke(app, ["account", "--address", self.ADDR, "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data


# ---------------------------------------------------------------------------
# journal view --json
# ---------------------------------------------------------------------------

class TestJournalJson:
    def test_empty_dir(self, tmp_data_dir):
        result = runner.invoke(app, ["journal", "view", "--data-dir", tmp_data_dir, "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data == {"entries": [], "total": 0, "date": None}

    def test_reads_entries(self, tmp_data_dir):
        from modules.journal_engine import JournalEntry
        from modules.journal_guard import JournalGuard

        guard = JournalGuard(data_dir=tmp_data_dir)
        guard.log_entry(JournalEntry(
            entry_id="ETH-PERP-1", instrument="ETH-PERP", direction="long",
            pnl=12.5, roe_pct=3.2, signal_quality="good", close_reason="guard_close",
            close_ts=1_700_000_000_000,
        ))
        result = runner.invoke(app, ["journal", "view", "--data-dir", tmp_data_dir, "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["total"] == 1
        assert data["entries"][0]["entry_id"] == "ETH-PERP-1"
        assert data["entries"][0]["pnl"] == 12.5


# ---------------------------------------------------------------------------
# status --json
# ---------------------------------------------------------------------------

class TestStatusJson:
    def test_empty_dir_valid_json(self, tmp_data_dir):
        result = runner.invoke(app, ["status", "--data-dir", tmp_data_dir, "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["found"] is False
        assert data["position_qty"] == 0.0
        assert data["total_pnl"] == 0.0
        assert data["view_only"] is False

    def test_address_attaches_account(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr(
            "cli.hl_adapter.read_only_account_state",
            lambda address, testnet=True: {"address": address, "account_value": 50.0},
        )
        addr = "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"
        result = runner.invoke(app, ["status", "--data-dir", tmp_data_dir, "--address", addr, "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["view_only"] is True
        assert data["account"]["account_value"] == 50.0
        assert data["account_address"] == addr


# ---------------------------------------------------------------------------
# trading summary
# ---------------------------------------------------------------------------

class TestTradingSummary:
    def _write_trades(self, tmp_data_dir):
        path = f"{tmp_data_dir}/trades.jsonl"
        rows = [
            {"tick": 1, "oid": "a1", "instrument": "ETH-PERP", "side": "buy",
             "price": 2500.0, "quantity": 1.0, "timestamp_ms": 1, "fee": 1.0,
             "strategy": "avellaneda_mm", "meta": "entry"},
            {"tick": 5, "oid": "a2", "instrument": "ETH-PERP", "side": "sell",
             "price": 2530.0, "quantity": 1.0, "timestamp_ms": 2, "fee": 1.0,
             "strategy": "avellaneda_mm", "meta": "guard_close"},
        ]
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_summary_with_synthetic_trades(self, tmp_data_dir):
        self._write_trades(tmp_data_dir)
        result = runner.invoke(app, [
            "trading", "summary", "--workspace", "ws1", "--agent", "ag1",
            "--data-dir", tmp_data_dir, "--no-account",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["workspace"] == "ws1"
        assert data["agent"] == "ag1"
        assert data["fills_total"] == 2
        # newest-first
        assert data["fills"][0]["oid"] == "a2"
        # realized = gross(30) - fees(2) = 28
        assert data["pnl"]["realized_pnl"] == 28.0
        assert data["pnl"]["gross_pnl"] == 30.0
        assert data["pnl"]["round_trips"] == 1
        assert data["pnl"]["win_rate"] == 100.0
        # registry is always present
        assert "avellaneda_mm" in data["registry"]["strategies"]
        assert data["view_only"] is False

    def test_summary_empty_dir_zeros(self, tmp_data_dir):
        result = runner.invoke(app, ["trading", "summary", "--data-dir", tmp_data_dir, "--no-account"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["fills"] == []
        assert data["fills_total"] == 0
        assert data["pnl"]["realized_pnl"] == 0.0
        assert data["pnl"]["round_trips"] == 0
        assert data["journal"] == {"entries": [], "total": 0}
        assert data["strategy"] == {}

    def test_summary_nonexistent_dir_no_crash(self):
        result = runner.invoke(app, [
            "trading", "summary", "--data-dir", "/tmp/nope-does-not-exist-xyz/sub", "--no-account",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["fills_total"] == 0
        assert data["pnl"]["realized_pnl"] == 0.0

    def test_summary_view_only_address(self, tmp_data_dir, monkeypatch):
        monkeypatch.setattr(
            "cli.hl_adapter.read_only_account_state",
            lambda address, testnet=True: {"address": address, "account_value": 99.0},
        )
        addr = "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"
        result = runner.invoke(app, [
            "trading", "summary", "--data-dir", tmp_data_dir, "--address", addr,
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["view_only"] is True
        assert data["address"] == addr
        assert data["account"]["account_value"] == 99.0
