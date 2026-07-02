"""Tests for backtest harness, config JSON roundtrip, and research directions."""
import json
import os
import sys
import tempfile

import pytest

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from modules.apex_config import ApexConfig
from modules.reflect_adapter import suggest_research_directions
from modules.reflect_engine import ReflectEngine, ReflectMetrics, TradeRecord
from scripts.backtest_apex import load_trades, main, replay_with_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade_dict(side="buy", price=100.0, qty=1.0, ts=1000, fee=0.5,
                     instrument="ETH-PERP", strategy="test", meta=""):
    return {
        "tick": 1, "oid": f"t-{ts}", "instrument": instrument,
        "side": side, "price": price, "quantity": qty,
        "timestamp_ms": ts, "fee": fee, "strategy": strategy,
        "meta": meta,
    }


def _write_trades_jsonl(trades_dicts, path):
    with open(path, "w") as f:
        for d in trades_dicts:
            f.write(json.dumps(d) + "\n")


def _make_paired_trades(n=6, base_price=100.0, spread=10.0, fee=0.5):
    """Create n round trips (2n trades): alternating buy low, sell high."""
    trades = []
    for i in range(n):
        ts_entry = (i * 2) * 1000
        ts_exit = (i * 2 + 1) * 1000
        trades.append(_make_trade_dict(
            side="buy", price=base_price, qty=1.0, ts=ts_entry, fee=fee,
        ))
        trades.append(_make_trade_dict(
            side="sell", price=base_price + spread, qty=1.0, ts=ts_exit, fee=fee,
        ))
    return trades


# ---------------------------------------------------------------------------
# Test: Trade loading from JSONL
# ---------------------------------------------------------------------------

class TestTradeLoading:
    def test_load_trades_from_jsonl(self, tmp_path):
        trades_file = str(tmp_path / "trades.jsonl")
        dicts = [
            _make_trade_dict(side="buy", price=100, ts=1000),
            _make_trade_dict(side="sell", price=110, ts=2000),
        ]
        _write_trades_jsonl(dicts, trades_file)

        trades = load_trades(trades_file)
        assert len(trades) == 2
        assert isinstance(trades[0], TradeRecord)
        assert trades[0].side == "buy"
        assert trades[0].price == 100.0
        assert trades[1].side == "sell"
        assert trades[1].price == 110.0

    def test_load_trades_skips_blank_lines(self, tmp_path):
        trades_file = str(tmp_path / "trades.jsonl")
        with open(trades_file, "w") as f:
            f.write(json.dumps(_make_trade_dict()) + "\n")
            f.write("\n")
            f.write("  \n")
            f.write(json.dumps(_make_trade_dict(ts=2000)) + "\n")

        trades = load_trades(trades_file)
        assert len(trades) == 2


# ---------------------------------------------------------------------------
# Test: Config filtering (radar score threshold)
# ---------------------------------------------------------------------------

class TestConfigFiltering:
    def test_radar_score_filters_entries(self):
        config = ApexConfig(radar_score_threshold=200)
        trades = [
            # radar_score=150 → below threshold → filtered out
            TradeRecord.from_dict(_make_trade_dict(
                side="buy", price=100, ts=1000,
                meta=json.dumps({"radar_score": 150}),
            )),
            # This sell has no meta (default pass) → kept
            TradeRecord.from_dict(_make_trade_dict(
                side="sell", price=110, ts=2000,
            )),
        ]
        filtered = replay_with_config(trades, config)
        # The buy is filtered, only the sell passes (as it defaults to 999)
        assert len(filtered) == 1
        assert filtered[0].side == "sell"

    def test_radar_score_passes_above_threshold(self):
        config = ApexConfig(radar_score_threshold=170)
        trades = [
            TradeRecord.from_dict(_make_trade_dict(
                side="buy", price=100, ts=1000,
                meta=json.dumps({"radar_score": 200}),
            )),
            TradeRecord.from_dict(_make_trade_dict(
                side="sell", price=110, ts=2000,
                meta=json.dumps({"exit": True}),
            )),
        ]
        filtered = replay_with_config(trades, config)
        assert len(filtered) == 2

    def test_pulse_confidence_filters(self):
        config = ApexConfig(pulse_confidence_threshold=80.0)
        trades = [
            TradeRecord.from_dict(_make_trade_dict(
                side="buy", price=100, ts=1000,
                meta=json.dumps({"radar_score": 200, "pulse_confidence": 60.0}),
            )),
            TradeRecord.from_dict(_make_trade_dict(
                side="sell", price=110, ts=2000,
            )),
        ]
        filtered = replay_with_config(trades, config)
        # Buy filtered (pulse_confidence 60 < 80)
        assert len(filtered) == 1


# ---------------------------------------------------------------------------
# Test: Quality gate rejects too few trades
# ---------------------------------------------------------------------------

class TestQualityGate:
    def test_reject_too_few_trades(self, tmp_path, capsys):
        # Only 2 trades → 1 round trip → REJECT
        config_path = str(tmp_path / "config.json")
        trades_path = str(tmp_path / "trades.jsonl")

        ApexConfig().to_json(config_path)
        _write_trades_jsonl([
            _make_trade_dict(side="buy", price=100, ts=1000),
            _make_trade_dict(side="sell", price=110, ts=2000),
        ], trades_path)

        rc = main(["--config", config_path, "--trades", trades_path])
        assert rc == 1
        captured = capsys.readouterr()
        assert "REJECT: too few trades" in captured.out

    def test_passes_with_enough_trades(self, tmp_path, capsys):
        config_path = str(tmp_path / "config.json")
        trades_path = str(tmp_path / "trades.jsonl")

        ApexConfig().to_json(config_path)
        _write_trades_jsonl(_make_paired_trades(n=6), trades_path)

        rc = main(["--config", config_path, "--trades", trades_path])
        assert rc == 0
        captured = capsys.readouterr()
        assert "REJECT" not in captured.out
        assert "net_pnl:" in captured.out


# ---------------------------------------------------------------------------
# Test: Metric output format parsing
# ---------------------------------------------------------------------------

class TestMetricOutput:
    def test_output_format_parseable(self, tmp_path, capsys):
        config_path = str(tmp_path / "config.json")
        trades_path = str(tmp_path / "trades.jsonl")

        ApexConfig().to_json(config_path)
        _write_trades_jsonl(_make_paired_trades(n=6), trades_path)

        rc = main(["--config", config_path, "--trades", trades_path])
        assert rc == 0

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        parsed = {}
        for line in lines:
            key, val = line.split(": ", 1)
            parsed[key] = val

        assert "net_pnl" in parsed
        assert "win_rate" in parsed
        assert "fdr" in parsed
        assert "trades" in parsed
        assert "profit_factor" in parsed

        # Values should be parseable as floats
        assert float(parsed["net_pnl"]) > 0
        assert float(parsed["win_rate"]) > 0
        assert int(parsed["trades"]) >= 5


# ---------------------------------------------------------------------------
# Test: Config JSON roundtrip
# ---------------------------------------------------------------------------

class TestConfigJsonRoundtrip:
    def test_roundtrip(self, tmp_path):
        config = ApexConfig(
            radar_score_threshold=200,
            pulse_confidence_threshold=85.0,
            daily_loss_limit=300.0,
            max_same_direction=1,
        )
        path = str(tmp_path / "test_config.json")
        config.to_json(path)

        loaded = ApexConfig.from_json(path)
        assert loaded.radar_score_threshold == 200
        assert loaded.pulse_confidence_threshold == 85.0
        assert loaded.daily_loss_limit == 300.0
        assert loaded.max_same_direction == 1

    def test_roundtrip_preserves_defaults(self, tmp_path):
        config = ApexConfig()
        path = str(tmp_path / "default_config.json")
        config.to_json(path)

        loaded = ApexConfig.from_json(path)
        assert loaded.total_budget == config.total_budget
        assert loaded.leverage == config.leverage
        assert loaded.guard_preset == config.guard_preset


# ---------------------------------------------------------------------------
# Test: suggest_research_directions
# ---------------------------------------------------------------------------

class TestSuggestResearchDirections:
    def test_high_fdr_suggests_radar(self):
        m = ReflectMetrics(
            total_round_trips=10, fdr=35.0, win_rate=55.0,
            net_pnl=100.0, long_pnl=50.0, short_pnl=50.0,
        )
        dirs = suggest_research_directions(m)
        assert any("radar_score_threshold" in d for d in dirs)
        assert any("[170, 250]" in d for d in dirs)

    def test_low_win_rate_suggests_pulse(self):
        m = ReflectMetrics(
            total_round_trips=10, fdr=10.0, win_rate=30.0,
            net_pnl=50.0, long_pnl=25.0, short_pnl=25.0,
        )
        dirs = suggest_research_directions(m)
        assert any("pulse_confidence_threshold" in d for d in dirs)

    def test_direction_imbalance_suggests_max_same_direction(self):
        m = ReflectMetrics(
            total_round_trips=10, fdr=10.0, win_rate=55.0,
            net_pnl=50.0, long_pnl=-30.0, short_pnl=80.0,
            long_count=5, short_count=5,
        )
        dirs = suggest_research_directions(m)
        assert any("max_same_direction" in d for d in dirs)

    def test_healthy_strategy_suggests_relaxing(self):
        m = ReflectMetrics(
            total_round_trips=10, fdr=8.0, win_rate=60.0,
            net_pnl=200.0, long_pnl=100.0, short_pnl=100.0,
            long_count=5, short_count=5,
        )
        dirs = suggest_research_directions(m)
        assert any("healthy" in d.lower() or "lowering" in d.lower() for d in dirs)

    def test_insufficient_data(self):
        m = ReflectMetrics(total_round_trips=2)
        dirs = suggest_research_directions(m)
        assert len(dirs) == 1
        assert "more trades" in dirs[0].lower()
