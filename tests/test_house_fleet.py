"""Tests for the HOUSE-mode fleet launcher (supervisor + standing aggregator)."""
import json
import os
import sys
import time

import pytest

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from cli.builder_fee import BuilderFeeConfig
from cli.fleet_supervisor import FleetSupervisor, resolve_python, parse_env_file
from cli.standing_aggregator import StandingAggregator
from common.fleet_models import FleetMemberSpec


# --------------------------------------------------------------------------- #
# StandingAggregator
# --------------------------------------------------------------------------- #
class TestStandingAggregator:
    def test_absent_file_returns_zeros(self, tmp_path):
        agg = StandingAggregator(trades_path=tmp_path / "nope.jsonl")
        r = agg.get_standing()
        assert r.empty is True
        assert r.total_fills == 0
        assert r.total_notional_24h == 0.0
        assert r.total_bc_accrued_24h == 0.0
        assert r.markets == []
        assert r.error and "not found" in r.error

    def test_empty_file_returns_zeros(self, tmp_path):
        p = tmp_path / "trades.jsonl"
        p.write_text("")
        agg = StandingAggregator(trades_path=p)
        r = agg.get_standing()
        assert r.empty is True
        assert r.total_fills == 0
        assert r.markets == []
        assert r.error is None  # file exists, just empty

    def test_aggregation_math(self, tmp_path):
        """notional = price*quantity; bcAccrued = notional * fee_tenths_bps/100_000."""
        now_ms = int(time.time() * 1000)
        p = tmp_path / "trades.jsonl"
        lines = [
            # GOLD: 2 fills within 24h
            {"tick": 1, "oid": "a", "instrument": "xyz:GOLD", "side": "buy",
             "price": "2000", "quantity": "3", "timestamp_ms": now_ms - 1000},
            {"tick": 2, "oid": "b", "instrument": "xyz:GOLD", "side": "sell",
             "price": "2000", "quantity": "2", "timestamp_ms": now_ms - 2000},
            # OIL: 1 fill within 24h
            {"tick": 3, "oid": "c", "instrument": "xyz:OIL", "side": "buy",
             "price": "80", "quantity": "10", "timestamp_ms": now_ms - 3000},
            # OIL: 1 fill in 7d window but older than 24h
            {"tick": 4, "oid": "d", "instrument": "xyz:OIL", "side": "buy",
             "price": "80", "quantity": "5", "timestamp_ms": now_ms - (2 * 24 * 60 * 60 * 1000)},
            # outside 7d window — must be ignored entirely
            {"tick": 5, "oid": "e", "instrument": "xyz:OIL", "side": "buy",
             "price": "80", "quantity": "100", "timestamp_ms": now_ms - (10 * 24 * 60 * 60 * 1000)},
        ]
        p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")

        # fee = 100 tenths-bps = 0.1% => fraction 0.001
        cfg = BuilderFeeConfig(builder_address="0xA", fee_rate_tenths_bps=100)
        agg = StandingAggregator(trades_path=p, fee_config=cfg)
        r = agg.get_standing()

        assert r.empty is False
        assert r.total_fills == 5  # all parseable lines counted

        by = {m.market: m for m in r.markets}

        # GOLD: 24h notional = 2000*3 + 2000*2 = 10_000
        gold = by["xyz:GOLD"]
        assert gold.notional_24h == pytest.approx(10_000.0)
        assert gold.notional_7d == pytest.approx(10_000.0)
        assert gold.fill_count_24h == 2
        assert gold.fill_count_7d == 2
        assert gold.bc_accrued_24h == pytest.approx(10_000.0 * 0.001)  # 10.0
        assert gold.bc_accrued_7d == pytest.approx(10.0)

        # OIL: 24h notional = 80*10 = 800 ; 7d notional = 800 + 80*5 = 1200
        oil = by["xyz:OIL"]
        assert oil.notional_24h == pytest.approx(800.0)
        assert oil.notional_7d == pytest.approx(1200.0)
        assert oil.fill_count_24h == 1
        assert oil.fill_count_7d == 2
        assert oil.bc_accrued_24h == pytest.approx(0.8)
        assert oil.bc_accrued_7d == pytest.approx(1.2)

        # Totals
        assert r.total_notional_24h == pytest.approx(10_800.0)
        assert r.total_notional_7d == pytest.approx(11_200.0)
        assert r.total_bc_accrued_24h == pytest.approx(10.8)
        assert r.total_bc_accrued_7d == pytest.approx(11.2)

        # Sorted by 24h notional desc => GOLD first
        assert r.markets[0].market == "xyz:GOLD"

    def test_bad_lines_skipped(self, tmp_path):
        now_ms = int(time.time() * 1000)
        p = tmp_path / "trades.jsonl"
        p.write_text(
            "not json\n"
            + json.dumps({"instrument": "X", "price": "10", "quantity": "1",
                          "timestamp_ms": now_ms}) + "\n"
            + json.dumps({"price": "1", "quantity": "1", "timestamp_ms": now_ms}) + "\n"  # no instrument
            + json.dumps({"instrument": "X", "price": "bad", "quantity": "1",
                          "timestamp_ms": now_ms}) + "\n"  # bad price
        )
        agg = StandingAggregator(trades_path=p)
        r = agg.get_standing()
        assert r.total_fills == 1  # only the one good line
        assert len(r.markets) == 1

    def test_fee_from_env_default(self, tmp_path):
        """Default fee config picks up the Nunchi 100 tenths-bps default."""
        p = tmp_path / "trades.jsonl"
        p.write_text("")
        agg = StandingAggregator(trades_path=p)
        assert agg.fee_config.fee_rate_tenths_bps == 100

    def test_ttl_cache(self, tmp_path):
        p = tmp_path / "trades.jsonl"
        p.write_text("")
        agg = StandingAggregator(trades_path=p)
        r1 = agg.get_standing()
        # add a trade after first read; cached result should be returned
        now_ms = int(time.time() * 1000)
        p.write_text(json.dumps(
            {"instrument": "X", "price": "10", "quantity": "1", "timestamp_ms": now_ms}
        ) + "\n")
        r2 = agg.get_standing()  # within 5s TTL -> stale cache
        assert r2 is r1
        r3 = agg.get_standing(bypass_cache=True)  # force re-read
        assert r3.total_fills == 1


# --------------------------------------------------------------------------- #
# FleetSupervisor
# --------------------------------------------------------------------------- #
class TestFleetSupervisorHelpers:
    def test_resolve_python(self):
        py = resolve_python()
        assert py  # non-empty string

    def test_resolve_python_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_CLI_PYTHON", "/custom/python")
        assert resolve_python() == "/custom/python"

    def test_parse_env_file(self, tmp_path):
        f = tmp_path / "p.env"
        f.write_text(
            "# comment\n"
            "\n"
            'FOO=bar\n'
            'QUOTED="hello world"\n'
            "SINGLE='x'\n"
            "noeq line\n"
        )
        env = parse_env_file(f)
        assert env == {"FOO": "bar", "QUOTED": "hello world", "SINGLE": "x"}

    def test_build_args_run(self):
        sup = FleetSupervisor()
        spec = FleetMemberSpec(name="g", strategy="engine_mm", market="xyz:GOLD",
                               extra_args=["--mock", "--max-ticks", "30"])
        assert sup._build_args(spec) == [
            "run", "engine_mm", "-i", "xyz:GOLD", "--mock", "--max-ticks", "30"
        ]

    def test_build_args_no_market(self):
        sup = FleetSupervisor()
        spec = FleetMemberSpec(name="m", strategy="engine_mm")
        assert sup._build_args(spec) == ["run", "engine_mm"]

    def test_build_args_load_sentinel(self):
        """__load__ members emit `strategy load <name>` (sibling-PR subcommand)."""
        sup = FleetSupervisor()
        spec = FleetMemberSpec(name="l", strategy="__load__", market="myartifact",
                               extra_args=["--foo"])
        assert sup._build_args(spec) == ["strategy", "load", "myartifact", "--foo"]


class TestFleetSupervisorLifecycle:
    def test_spawn_mock_member_exits_cleanly(self):
        """Spawn a real self-terminating member and assert state transitions.

        Uses `run engine_mm --mock --max-ticks 2` with a tiny tick so it runs
        offline (no HL connection) and exits on its own.
        """
        sup = FleetSupervisor()
        spec = FleetMemberSpec(
            name="mock-gold",
            strategy="engine_mm",
            market="ETH-PERP",
            extra_args=["--mock", "--max-ticks", "2", "--tick", "0.05", "--fresh"],
        )
        state = sup.spawn(spec)

        assert state.status == "active"
        assert isinstance(state.pid, int) and state.pid > 0
        assert sup.get(state.id) is state

        # Wait for the process to self-terminate (max ~20s).
        deadline = time.time() + 20
        while time.time() < deadline and state.status == "active":
            time.sleep(0.2)

        assert state.status in ("exited", "errored"), (
            f"member did not exit; status={state.status} "
            f"recent={state.recent_logs[-5:]} errors={state.error_logs[-5:]}"
        )
        assert state.exited_at is not None
        assert state.exit_code is not None
        # It runs and exits cleanly in mock mode.
        assert state.status == "exited", (
            f"expected clean exit, got errored. errors={state.error_logs[-10:]}"
        )

    def test_kill_marks_killed(self):
        """A long-running member can be killed and is marked 'killed'."""
        sup = FleetSupervisor()
        spec = FleetMemberSpec(
            name="long",
            strategy="engine_mm",
            market="ETH-PERP",
            # max-ticks 0 => runs forever; large tick so it idles between ticks.
            extra_args=["--mock", "--max-ticks", "0", "--tick", "30", "--fresh"],
        )
        state = sup.spawn(spec)
        assert state.status == "active"
        # Give it a moment to actually start.
        time.sleep(1.0)

        assert sup.kill(state.id) is True
        assert state.status == "killed"
        assert state.exited_at is not None

        # kill_all on an already-killed fleet returns 0 (nothing live left).
        time.sleep(0.5)
        assert sup.kill_all() == 0

    def test_missing_preset_soft_fails(self):
        """A missing preset is recorded in error_logs but does not block spawn."""
        sup = FleetSupervisor()
        spec = FleetMemberSpec(
            name="p",
            strategy="engine_mm",
            market="ETH-PERP",
            preset="does-not-exist-xyz",
            extra_args=["--mock", "--max-ticks", "1", "--tick", "0.05", "--fresh"],
        )
        state = sup.spawn(spec)
        assert any("preset" in e and "not found" in e for e in state.error_logs)
        # Still spawned.
        assert state.pid
        sup.kill(state.id)
