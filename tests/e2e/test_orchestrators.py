"""E2E smoke tests for agent orchestration command groups."""
from __future__ import annotations

import json

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def test_apex_mock_run_status_and_reconcile(run_cli, e2e_data_dir):
    apex_dir = e2e_data_dir / "apex"

    run = run_cli(
        [
            "apex",
            "run",
            "--mock",
            "--max-ticks",
            "1",
            "--tick",
            "0",
            "--data-dir",
            str(apex_dir),
        ],
        timeout=120,
    )
    assert "Mode: MOCK" in run.stdout
    assert "APEX SESSION SUMMARY" in run.stdout
    assert (apex_dir / "state.json").exists()

    status = run_cli(["apex", "status", "--data-dir", str(apex_dir)])
    assert "Ticks:" in status.stdout

    reconcile = run_cli(["apex", "reconcile", "--mock", "--data-dir", str(apex_dir)])
    assert "All clear" in reconcile.stdout or "Found" in reconcile.stdout


def test_radar_once_then_status(run_cli, e2e_data_dir):
    radar_dir = e2e_data_dir / "radar"

    once = run_cli(["radar", "once", "--mock", "--data-dir", str(radar_dir)], timeout=90)
    assert "Mode: MOCK" in once.stdout
    assert "SCAN #1" in once.stdout

    status = run_cli(["radar", "status", "--data-dir", str(radar_dir)])
    assert "Last scan:" in status.stdout
    assert "Qualified:" in status.stdout


def test_pulse_once_then_status(run_cli, e2e_data_dir):
    pulse_dir = e2e_data_dir / "pulse"

    once = run_cli(["pulse", "once", "--mock", "--data-dir", str(pulse_dir)], timeout=90)
    assert "Mode: MOCK" in once.stdout
    assert "PULSE #1" in once.stdout

    status = run_cli(["pulse", "status", "--data-dir", str(pulse_dir)])
    assert "Last scan:" in status.stdout


def test_guard_readonly_surfaces(run_cli, e2e_data_dir):
    status = run_cli(["guard", "status", "--data-dir", str(e2e_data_dir / "guard")])
    assert "No active guards." in status.stdout

    presets = run_cli(["guard", "presets"])
    assert "MODERATE" in presets.stdout or "TIGHT" in presets.stdout


def test_hedge_proposal_and_backtest_json(run_cli, tmp_path):
    proposal = run_cli(
        [
            "hedge",
            "propose",
            "--perp-notional",
            "150000",
            "--side",
            "long",
            "--funding-apr",
            "42",
            "--json",
        ]
    )
    payload = json.loads(proposal.stdout)
    assert payload["hedge_market"] == "BTCSWP-USDYP"
    assert payload["hedge_notional_usd"] == 10_000

    csv_path = tmp_path / "funding.csv"
    csv_path.write_text("funding_rate_8h\n0.0003\n-0.0001\n", encoding="utf-8")
    backtest = run_cli(
        [
            "hedge",
            "backtest",
            "--csv",
            str(csv_path),
            "--perp-notional",
            "150000",
            "--side",
            "long",
            "--json",
        ]
    )
    backtest_payload = json.loads(backtest.stdout)
    assert backtest_payload["periods"] == 2
    assert backtest_payload["hedge_market"] == "BTCSWP-USDYP"


def test_hedge_agent_standalone_script(run_script):
    result = run_script("scripts/test_hedge_agent.py", timeout=120)

    assert "OK hedge_agent CLI smoke test passed" in result.stdout
