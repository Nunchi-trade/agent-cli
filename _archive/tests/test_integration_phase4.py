"""Phase 4a integration tests — Autoresearch REFLECT validation.

Tests the full chain: trades JSONL -> backtest_apex.py -> ReflectEngine metrics
-> reflect_adapter suggestions -> ApexConfig JSON roundtrip.

Read-only validation: no production code is modified.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is importable
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.apex_config import ApexConfig
from modules.reflect_adapter import suggest_research_directions
from modules.reflect_engine import ReflectEngine, ReflectMetrics, TradeRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade(
    tick: int = 0,
    instrument: str = "ETH",
    side: str = "buy",
    price: float = 2000.0,
    quantity: float = 1.0,
    timestamp_ms: int = 0,
    fee: float = 0.5,
    strategy: str = "apex",
    meta: str = "",
) -> dict:
    """Return a trade dict suitable for JSONL serialization."""
    return {
        "tick": tick,
        "oid": f"order-{tick}-{side}",
        "instrument": instrument,
        "side": side,
        "price": price,
        "quantity": quantity,
        "timestamp_ms": timestamp_ms,
        "fee": fee,
        "strategy": strategy,
        "meta": meta,
    }


def _write_trades_jsonl(trades: list[dict], path: str) -> None:
    with open(path, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


def _profitable_trades() -> list[dict]:
    """Generate 12 trades forming 6 profitable long round trips."""
    trades = []
    base_ts = 1_700_000_000_000
    for i in range(6):
        ts_entry = base_ts + i * 3_600_000
        ts_exit = ts_entry + 1_800_000  # 30 min hold
        entry_price = 2000.0 + i * 10
        exit_price = entry_price + 20.0  # $20 profit per RT

        meta_entry = json.dumps({"radar_score": 200, "pulse_confidence": 80.0})
        meta_exit = json.dumps({"exit": True})

        trades.append(_make_trade(
            tick=i * 2, side="buy", price=entry_price, quantity=1.0,
            timestamp_ms=ts_entry, fee=0.5, meta=meta_entry,
        ))
        trades.append(_make_trade(
            tick=i * 2 + 1, side="sell", price=exit_price, quantity=1.0,
            timestamp_ms=ts_exit, fee=0.5, meta=meta_exit,
        ))
    return trades


def _losing_trades() -> list[dict]:
    """Generate 12 trades forming 6 losing long round trips."""
    trades = []
    base_ts = 1_700_000_000_000
    for i in range(6):
        ts_entry = base_ts + i * 3_600_000
        ts_exit = ts_entry + 1_800_000
        entry_price = 2000.0 + i * 10
        exit_price = entry_price - 15.0  # -$15 per RT

        meta_entry = json.dumps({"radar_score": 200, "pulse_confidence": 80.0})
        meta_exit = json.dumps({"exit": True})

        trades.append(_make_trade(
            tick=i * 2, side="buy", price=entry_price, quantity=1.0,
            timestamp_ms=ts_entry, fee=0.5, meta=meta_entry,
        ))
        trades.append(_make_trade(
            tick=i * 2 + 1, side="sell", price=exit_price, quantity=1.0,
            timestamp_ms=ts_exit, fee=0.5, meta=meta_exit,
        ))
    return trades


# ===========================================================================
# 1. backtest_apex.py — importability and core functions
# ===========================================================================

class TestBacktestApexImport:
    """Verify backtest_apex.py is importable and has expected functions."""

    def test_module_imports(self):
        mod = importlib.import_module("scripts.backtest_apex")
        assert hasattr(mod, "load_trades")
        assert hasattr(mod, "replay_with_config")
        assert hasattr(mod, "main")

    def test_load_trades_parses_jsonl(self, tmp_path):
        from scripts.backtest_apex import load_trades

        trades_data = _profitable_trades()
        path = str(tmp_path / "trades.jsonl")
        _write_trades_jsonl(trades_data, path)

        loaded = load_trades(path)
        assert len(loaded) == len(trades_data)
        assert all(isinstance(t, TradeRecord) for t in loaded)
        assert loaded[0].side == "buy"
        assert loaded[0].price == 2000.0
        assert loaded[1].side == "sell"

    def test_load_trades_skips_blank_lines(self, tmp_path):
        from scripts.backtest_apex import load_trades

        path = str(tmp_path / "trades.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(_make_trade(side="buy")) + "\n")
            f.write("\n")  # blank line
            f.write("  \n")  # whitespace-only line
            f.write(json.dumps(_make_trade(side="sell")) + "\n")

        loaded = load_trades(path)
        assert len(loaded) == 2

    def test_replay_filters_low_radar_score(self, tmp_path):
        """Trades with radar_score below threshold should be excluded."""
        from scripts.backtest_apex import load_trades, replay_with_config

        config = ApexConfig(radar_score_threshold=200, pulse_confidence_threshold=50.0)
        base_ts = 1_700_000_000_000

        trades_data = [
            # Entry with radar_score 150 — below threshold of 200 → filtered out
            _make_trade(tick=0, side="buy", price=2000, quantity=1.0,
                        timestamp_ms=base_ts, meta=json.dumps({"radar_score": 150, "pulse_confidence": 80})),
            _make_trade(tick=1, side="sell", price=2020, quantity=1.0,
                        timestamp_ms=base_ts + 100_000, meta=json.dumps({"exit": True})),
            # Entry with radar_score 250 — above threshold → passes
            _make_trade(tick=2, side="buy", price=2010, quantity=1.0,
                        timestamp_ms=base_ts + 200_000, meta=json.dumps({"radar_score": 250, "pulse_confidence": 80})),
            _make_trade(tick=3, side="sell", price=2030, quantity=1.0,
                        timestamp_ms=base_ts + 300_000, meta=json.dumps({"exit": True})),
        ]
        path = str(tmp_path / "trades.jsonl")
        _write_trades_jsonl(trades_data, path)

        loaded = load_trades(path)
        filtered = replay_with_config(loaded, config)

        # First buy (radar=150) should be excluded. Second buy (radar=250) passes.
        # The first sell doesn't close anything (no open positions), so it's treated as
        # a new entry with default radar_score 999 → passes. So we expect 3 trades.
        # Actually let's just verify the low-radar entry was excluded.
        low_radar_entries = [t for t in filtered
                            if t.side == "buy" and t.price == 2000]
        assert len(low_radar_entries) == 0, "Low radar_score trade should be filtered"

        high_radar_entries = [t for t in filtered
                             if t.side == "buy" and t.price == 2010]
        assert len(high_radar_entries) == 1, "High radar_score trade should pass"

    def test_replay_filters_low_pulse_confidence(self, tmp_path):
        """Trades with pulse_confidence below threshold should be excluded."""
        from scripts.backtest_apex import load_trades, replay_with_config

        config = ApexConfig(radar_score_threshold=100, pulse_confidence_threshold=80.0)
        base_ts = 1_700_000_000_000

        trades_data = [
            _make_trade(tick=0, side="buy", price=2000, quantity=1.0,
                        timestamp_ms=base_ts,
                        meta=json.dumps({"radar_score": 200, "pulse_confidence": 60})),
            _make_trade(tick=1, side="sell", price=2020, quantity=1.0,
                        timestamp_ms=base_ts + 100_000,
                        meta=json.dumps({"exit": True})),
        ]
        path = str(tmp_path / "trades.jsonl")
        _write_trades_jsonl(trades_data, path)

        loaded = load_trades(path)
        filtered = replay_with_config(loaded, config)

        low_pulse = [t for t in filtered if t.side == "buy" and t.price == 2000]
        assert len(low_pulse) == 0, "Low pulse_confidence trade should be filtered"


# ===========================================================================
# 2. reflect_adapter.suggest_research_directions()
# ===========================================================================

class TestReflectAdapterDirections:

    def test_high_fdr_suggests_radar_threshold(self):
        metrics = ReflectMetrics(
            total_round_trips=10,
            fdr=35.0,
            win_rate=55.0,
            net_pnl=100.0,
            gross_pnl=200.0,
            total_fees=50.0,
        )
        directions = suggest_research_directions(metrics)
        assert any("radar_score_threshold" in d for d in directions), \
            f"High FDR should suggest radar_score_threshold. Got: {directions}"

    def test_low_win_rate_suggests_pulse_confidence(self):
        metrics = ReflectMetrics(
            total_round_trips=10,
            win_rate=35.0,
            fdr=10.0,
            net_pnl=50.0,
            gross_pnl=100.0,
            total_fees=5.0,
        )
        directions = suggest_research_directions(metrics)
        assert any("pulse_confidence" in d.lower() for d in directions), \
            f"Low win rate should suggest pulse_confidence. Got: {directions}"

    def test_both_issues_present(self):
        metrics = ReflectMetrics(
            total_round_trips=10,
            fdr=35.0,
            win_rate=35.0,
            net_pnl=50.0,
            gross_pnl=100.0,
            total_fees=30.0,
        )
        directions = suggest_research_directions(metrics)
        has_radar = any("radar_score_threshold" in d for d in directions)
        has_pulse = any("pulse_confidence" in d.lower() for d in directions)
        assert has_radar and has_pulse, \
            f"Both FDR and win_rate issues should produce both suggestions. Got: {directions}"

    def test_healthy_metrics_suggest_relaxing(self):
        metrics = ReflectMetrics(
            total_round_trips=10,
            win_rate=60.0,
            fdr=10.0,
            net_pnl=500.0,
            gross_pnl=600.0,
            total_fees=20.0,
        )
        directions = suggest_research_directions(metrics)
        text = " ".join(directions).lower()
        assert "healthy" in text or "lower" in text or "relax" in text, \
            f"Healthy metrics should suggest relaxing. Got: {directions}"

    def test_insufficient_data(self):
        metrics = ReflectMetrics(total_round_trips=2)
        directions = suggest_research_directions(metrics)
        assert len(directions) == 1
        assert "more trades" in directions[0].lower()


# ===========================================================================
# 3. ApexConfig JSON roundtrip
# ===========================================================================

class TestApexConfigRoundtrip:

    def test_json_roundtrip_non_defaults(self, tmp_path):
        original = ApexConfig(
            total_budget=25_000.0,
            max_slots=5,
            leverage=15.0,
            radar_score_threshold=220,
            pulse_confidence_threshold=85.0,
            daily_loss_limit=750.0,
            max_same_direction=1,
            min_hold_ms=3_000_000,
            slot_cooldown_ms=600_000,
            cooldown_duration_ms=2_400_000,
            entry_order_type="Gtc",
        )
        path = str(tmp_path / "apex_config.json")
        original.to_json(path)
        restored = ApexConfig.from_json(path)

        assert restored.total_budget == original.total_budget
        assert restored.max_slots == original.max_slots
        assert restored.leverage == original.leverage
        assert restored.radar_score_threshold == original.radar_score_threshold
        assert restored.pulse_confidence_threshold == original.pulse_confidence_threshold
        assert restored.daily_loss_limit == original.daily_loss_limit
        assert restored.max_same_direction == original.max_same_direction

    def test_phase3_fields_survive_roundtrip(self, tmp_path):
        """Phase 3 fields must survive JSON serialization."""
        original = ApexConfig(
            min_hold_ms=5_000_000,
            slot_cooldown_ms=900_000,
            cooldown_duration_ms=3_600_000,
            entry_order_type="Alo",
        )
        path = str(tmp_path / "config.json")
        original.to_json(path)
        restored = ApexConfig.from_json(path)

        assert restored.min_hold_ms == 5_000_000
        assert restored.slot_cooldown_ms == 900_000
        assert restored.cooldown_duration_ms == 3_600_000
        assert restored.entry_order_type == "Alo"

    def test_roundtrip_all_fields(self, tmp_path):
        """Every field in to_dict() should survive the roundtrip."""
        original = ApexConfig()
        path = str(tmp_path / "config.json")
        original.to_json(path)
        restored = ApexConfig.from_json(path)

        orig_dict = original.to_dict()
        rest_dict = restored.to_dict()

        for key in orig_dict:
            assert key in rest_dict, f"Missing field after roundtrip: {key}"
            assert orig_dict[key] == rest_dict[key], \
                f"Field {key} mismatch: {orig_dict[key]} != {rest_dict[key]}"


# ===========================================================================
# 4. autoresearch_program.md structure
# ===========================================================================

class TestAutoresearchProgramDoc:

    @pytest.fixture(scope="class")
    def program_text(self):
        path = os.path.join(_ROOT, "configs", "autoresearch_program.md")
        assert os.path.exists(path), f"autoresearch_program.md not found at {path}"
        with open(path) as f:
            return f.read()

    def test_has_mutable_file_reference(self, program_text):
        assert "apex_config.json" in program_text, \
            "Program should reference a mutable config file"

    def test_has_run_command(self, program_text):
        assert "backtest_apex.py" in program_text, \
            "Program should contain a run command referencing backtest_apex.py"
        assert "--config" in program_text, \
            "Run command should include --config flag"

    def test_has_metric_specification(self, program_text):
        assert "net_pnl" in program_text, "Program should specify net_pnl as target metric"
        assert "win_rate" in program_text, "Program should mention win_rate metric"
        assert "fdr" in program_text, "Program should mention fdr metric"

    def test_has_parameter_bounds(self, program_text):
        # Check that bounds table has the expected parameters
        assert "radar_score_threshold" in program_text
        assert "pulse_confidence_threshold" in program_text
        assert "daily_loss_limit" in program_text
        # Check that numeric bounds are present
        assert "120" in program_text, "radar_score_threshold min bound (120) missing"
        assert "280" in program_text, "radar_score_threshold max bound (280) missing"


# ===========================================================================
# 5. End-to-end subprocess run of backtest_apex.py
# ===========================================================================

class TestBacktestE2E:

    def test_e2e_profitable_run(self, tmp_path):
        """Run backtest_apex.py as subprocess, verify metric output."""
        trades = _profitable_trades()
        trades_path = str(tmp_path / "trades.jsonl")
        _write_trades_jsonl(trades, trades_path)

        config = ApexConfig(radar_score_threshold=100, pulse_confidence_threshold=50.0)
        config_path = str(tmp_path / "config.json")
        config.to_json(config_path)

        result = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "scripts", "backtest_apex.py"),
             "--config", config_path, "--trades", trades_path],
            capture_output=True, text=True, timeout=30,
        )

        assert result.returncode == 0, \
            f"Backtest should succeed. stderr: {result.stderr}"

        stdout = result.stdout
        # Parse key: value lines
        metrics_found = {}
        for line in stdout.strip().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                metrics_found[key.strip()] = val.strip()

        expected_keys = {"net_pnl", "win_rate", "fdr", "trades", "profit_factor"}
        missing = expected_keys - set(metrics_found.keys())
        assert not missing, f"Missing metric keys in output: {missing}"

        # Sanity check values
        assert float(metrics_found["net_pnl"]) > 0, "Profitable trades should yield positive net_pnl"
        assert float(metrics_found["win_rate"]) > 0, "Win rate should be positive"
        assert int(metrics_found["trades"]) >= 5, "Should have at least 5 round trips"

    def test_e2e_quality_gate_reject(self, tmp_path):
        """Config that filters everything should produce REJECT output."""
        # Create trades with low radar scores
        base_ts = 1_700_000_000_000
        trades = []
        for i in range(4):
            trades.append(_make_trade(
                tick=i * 2, side="buy", price=2000, quantity=1.0,
                timestamp_ms=base_ts + i * 3_600_000,
                meta=json.dumps({"radar_score": 100, "pulse_confidence": 50}),
            ))
            trades.append(_make_trade(
                tick=i * 2 + 1, side="sell", price=2010, quantity=1.0,
                timestamp_ms=base_ts + i * 3_600_000 + 60_000,
                meta=json.dumps({"exit": True}),
            ))
        trades_path = str(tmp_path / "trades.jsonl")
        _write_trades_jsonl(trades, trades_path)

        # Set thresholds impossibly high → all entries filtered
        config = ApexConfig(radar_score_threshold=280, pulse_confidence_threshold=95.0)
        config_path = str(tmp_path / "config.json")
        config.to_json(config_path)

        result = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "scripts", "backtest_apex.py"),
             "--config", config_path, "--trades", trades_path],
            capture_output=True, text=True, timeout=30,
        )

        assert result.returncode == 1, \
            f"Should REJECT when all trades are filtered. stdout: {result.stdout}"
        assert "REJECT" in result.stdout, \
            f"Output should contain REJECT. Got: {result.stdout}"

    def test_e2e_output_is_parseable(self, tmp_path):
        """Every non-REJECT line in output must be 'key: value' parseable."""
        trades = _profitable_trades()
        trades_path = str(tmp_path / "trades.jsonl")
        _write_trades_jsonl(trades, trades_path)

        config = ApexConfig(radar_score_threshold=100, pulse_confidence_threshold=50.0)
        config_path = str(tmp_path / "config.json")
        config.to_json(config_path)

        result = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "scripts", "backtest_apex.py"),
             "--config", config_path, "--trades", trades_path],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0

        for line in result.stdout.strip().splitlines():
            parts = line.split(":", 1)
            assert len(parts) == 2, f"Line not in 'key: value' format: {line!r}"
            key, val = parts[0].strip(), parts[1].strip()
            assert key, f"Empty key in line: {line!r}"
            # Value should be numeric
            try:
                float(val)
            except ValueError:
                pytest.fail(f"Value not numeric in line: {line!r}")
