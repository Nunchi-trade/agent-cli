from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.main import app
from modules import treadfi_contract


runner = CliRunner()


def test_spec_status_is_blocked_until_contract_exists():
    status = treadfi_contract.spec_status()

    assert status["status"] == "blocked"
    assert "transport" in status["missing_contract_fields"]
    assert status["known_local_context"]["mcp_command"] == "hl mcp serve"


def test_market_params_use_local_btcswp_registry():
    params = treadfi_contract.market_params()

    assert params["known_locally"] is True
    assert params["instrument"] == "BTCSWP-USDYP"
    assert params["coin"] == "yex:BTCSWP"
    assert params["live_params_available"] is False


def test_treadfi_spec_status_cli_outputs_json():
    result = runner.invoke(app, ["treadfi", "spec-status"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"


def test_treadfi_market_params_cli_accepts_instrument():
    result = runner.invoke(app, ["treadfi", "market-params", "--instrument", "BTCSWP"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["requested"] == "BTCSWP"
    assert payload["known_locally"] is True
