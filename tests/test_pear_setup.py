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
        "PEAR_BTCSWP_ASSET",
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
        "attribution": "server_side",
    }
    statuses = {check["name"]: check["status"] for check in payload["checks"]}
    assert statuses["pear_api_key"] == "action_needed"
    assert statuses["dedicated_wallet_ack"] == "action_needed"
    assert payload["btcswp_asset"] == "BTCSWP"


def test_pear_setup_status_ready_from_env(monkeypatch):
    monkeypatch.setenv("PEAR_ADDRESS", "0x" + "1" * 40)
    monkeypatch.setenv("PEAR_API_KEY", "pear-api-key")
    monkeypatch.setenv("PEAR_DEDICATED_WALLET_ACK", "true")
    monkeypatch.setenv("PEAR_BTCSWP_ASSET", "nunchi:BTCSWP")

    result = runner.invoke(app, ["pear", "setup", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ready"] is True
    assert payload["auth_mode"] == "api_key"
    assert payload["btcswp_asset"] == "nunchi:BTCSWP"
    assert payload["pear_builder"]["attribution"] == "server_side"


def test_pear_setup_status_probe_checks_account_builder_and_agent(monkeypatch):
    import cli.commands.pear as pear_cmd

    user_address = "0x" + "1" * 40
    agent_address = "0x" + "2" * 40

    class FakePear:
        def get_account_state(self):
            return {"agentWalletAddress": "0xabc", "totalClosedTrades": 1}

        def get_agent_wallet(self):
            return {"agentWalletAddress": agent_address}

    def fake_hl_info(payload):
        assert payload["user"] == user_address
        if payload["type"] == "approvedBuilders":
            return [PEAR_BUILDER_ADDRESS.lower()]
        if payload["type"] == "extraAgents":
            return [{"address": agent_address.upper()}]
        raise AssertionError(f"unexpected payload: {payload}")

    monkeypatch.setenv("PEAR_ADDRESS", user_address)
    monkeypatch.setenv("PEAR_API_KEY", "pear-api-key")
    monkeypatch.setenv("PEAR_DEDICATED_WALLET_ACK", "true")
    monkeypatch.setattr("cli.commands.pair._open_pear", lambda: FakePear())
    monkeypatch.setattr(pear_cmd, "_hl_info", fake_hl_info)

    result = runner.invoke(app, ["pear", "setup", "status", "--json", "--probe"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ready"] is True
    probes = {probe["name"]: probe for probe in payload["probes"]}
    assert probes["pear_account_probe"]["status"] == "pass"
    assert probes["pear_builder_approval"]["status"] == "pass"
    assert probes["pear_agent_wallet_approval"]["status"] == "pass"
    assert probes["pear_agent_wallet_approval"]["agent_wallet"] == agent_address


def test_pear_setup_status_probe_flags_missing_approvals(monkeypatch):
    import cli.commands.pear as pear_cmd

    user_address = "0x" + "1" * 40
    agent_address = "0x" + "2" * 40

    class FakePear:
        def get_account_state(self):
            return {"agentWalletAddress": "0xabc"}

        def get_agent_wallet(self):
            return {"agentWalletAddress": agent_address}

    def fake_hl_info(payload):
        assert payload["user"] == user_address
        if payload["type"] == "approvedBuilders":
            return []
        if payload["type"] == "extraAgents":
            return [{"address": "0x" + "3" * 40}]
        raise AssertionError(f"unexpected payload: {payload}")

    monkeypatch.setenv("PEAR_ADDRESS", user_address)
    monkeypatch.setenv("PEAR_API_KEY", "pear-api-key")
    monkeypatch.setenv("PEAR_DEDICATED_WALLET_ACK", "true")
    monkeypatch.setattr("cli.commands.pair._open_pear", lambda: FakePear())
    monkeypatch.setattr(pear_cmd, "_hl_info", fake_hl_info)

    result = runner.invoke(app, ["pear", "setup", "status", "--json", "--probe"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ready"] is False
    probes = {probe["name"]: probe for probe in payload["probes"]}
    assert probes["pear_builder_approval"]["status"] == "action_needed"
    assert probes["pear_agent_wallet_approval"]["status"] == "action_needed"
