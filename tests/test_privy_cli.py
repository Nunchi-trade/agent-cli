"""CLI tests for `hl privy` helpers."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.commands.privy import privy_app


runner = CliRunner()


def test_policies_command_outputs_single_template():
    result = runner.invoke(privy_app, ["policies", "--kind", "allow_approve_agent", "--network", "mainnet"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["action"] == "ALLOW"
    assert body["conditions"][0]["value"] == ["Mainnet"]


def test_signer_payload_command_outputs_wallet_update_payload():
    result = runner.invoke(privy_app, ["signer-payload", "signer-1", "policy-1"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["additional_signers"][0]["signer_id"] == "signer-1"
    assert body["additional_signers"][0]["override_policy_ids"] == ["policy-1"]


def test_scope_command_outputs_web_auth_scope():
    result = runner.invoke(
        privy_app,
        ["scope", "hl.withdraw", "--network", "42161", "--notional-usdc", "10"],
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body == {"method": "hl.withdraw", "network": 42161, "notionalUsdc": 10.0}
