from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.main import app
from cli.pear_config import PEAR_BUILDER_ADDRESS, PEAR_BUILDER_FEE_TENTHS_BPS


runner = CliRunner()


def test_pear_setup_status_reports_missing_actions(monkeypatch):
    for key in (
        "PEAR_ADDRESS",
        "PEAR_WALLET_ADDRESS",
        "PEAR_API_KEY",
        "HL_PRIVATE_KEY",
        "PEAR_DEDICATED_WALLET_ACK",
        "PEAR_API_WALLET_APPROVED",
        "PEAR_BUILDER_APPROVED",
    ):
        monkeypatch.delenv(key, raising=False)

    result = runner.invoke(app, ["pear", "setup", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ready"] is False
    assert payload["auth_mode"] == "missing"
    assert payload["pear_builder"] == {
        "address": PEAR_BUILDER_ADDRESS,
        "fee_tenths_bps": PEAR_BUILDER_FEE_TENTHS_BPS,
        "fee_bps": 6.0,
    }
    statuses = {check["name"]: check["status"] for check in payload["checks"]}
    assert statuses["pear_api_key"] == "action_needed"
    assert statuses["dedicated_wallet_ack"] == "action_needed"


def test_pear_setup_status_ready_from_env(monkeypatch):
    monkeypatch.setenv("PEAR_ADDRESS", "0x" + "1" * 40)
    monkeypatch.setenv("PEAR_API_KEY", "pear-api-key")
    monkeypatch.setenv("PEAR_DEDICATED_WALLET_ACK", "true")
    monkeypatch.setenv("PEAR_API_WALLET_APPROVED", "true")
    monkeypatch.setenv("PEAR_BUILDER_APPROVED", "true")

    result = runner.invoke(app, ["pear", "setup", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ready"] is True
    assert payload["auth_mode"] == "api_key"


def test_pear_setup_status_probe_uses_open_pear(monkeypatch):
    class FakePear:
        def get_account_state(self):
            return {"agentWalletAddress": "0xabc", "totalClosedTrades": 1}

    monkeypatch.setenv("PEAR_ADDRESS", "0x" + "1" * 40)
    monkeypatch.setenv("PEAR_API_KEY", "pear-api-key")
    monkeypatch.setenv("PEAR_DEDICATED_WALLET_ACK", "true")
    monkeypatch.setenv("PEAR_API_WALLET_APPROVED", "true")
    monkeypatch.setenv("PEAR_BUILDER_APPROVED", "true")
    monkeypatch.setattr("cli.commands.pair._open_pear", lambda: FakePear())

    result = runner.invoke(app, ["pear", "setup", "status", "--json", "--probe"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ready"] is True
    assert payload["account_probe"]["status"] == "pass"
    assert payload["account_probe"]["account_keys"] == ["agentWalletAddress", "totalClosedTrades"]
