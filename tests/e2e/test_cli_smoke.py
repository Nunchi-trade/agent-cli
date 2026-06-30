"""Top-level process-boundary smoke tests for the `hl` CLI."""
from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.e2e


def test_help_lists_current_command_surface(run_cli):
    result = run_cli(["--help"])

    assert "Autonomous Hyperliquid trader" in result.stdout
    assert "strategies" in result.stdout
    assert "hedge" in result.stdout


def test_strategy_catalog_lists_registry_and_yex_markets(run_cli):
    result = run_cli(["strategies"])

    assert "avellaneda_mm" in result.stdout
    assert "hedge_agent" in result.stdout
    assert "BTCSWP-USDYP" in result.stdout


def test_setup_check_reports_non_fatal_auth_guidance(run_cli):
    result = run_cli(["setup", "check"])

    assert "Environment Check" in result.stdout
    assert "No private key" in result.stdout
    assert "No paired wallet found" in result.stdout


def test_wallet_auto_json_uses_isolated_home(run_cli, isolated_env):
    result = run_cli(["wallet", "auto", "--json"])
    payload = json.loads(result.stdout)

    assert payload["address"].startswith("0x")
    assert payload["password"]
    assert payload["keystore"].startswith(isolated_env["HOME"])
    assert payload["env_file"].startswith(isolated_env["HOME"])


def test_mock_strategy_run_persists_state_and_status(run_cli, e2e_data_dir):
    cli_dir = e2e_data_dir / "cli"

    run = run_cli(
        [
            "run",
            "avellaneda_mm",
            "--mock",
            "--max-ticks",
            "1",
            "--tick",
            "0",
            "--data-dir",
            str(cli_dir),
        ]
    )
    assert "Mode: MOCK" in run.stdout
    assert (cli_dir / "state.db").exists()

    status = run_cli(["status", "--data-dir", str(cli_dir)])
    assert "avellaneda_mm" in status.stdout
    assert "ETH-PERP" in status.stdout
