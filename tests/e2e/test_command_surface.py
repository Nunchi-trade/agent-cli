"""Command-surface E2E checks for safe CLI paths and safety gates."""
from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.e2e


HELP_COMMANDS = [
    [],
    ["run", "--help"],
    ["trade", "--help"],
    ["account", "--help"],
    ["guard", "--help"],
    ["radar", "--help"],
    ["pulse", "--help"],
    ["apex", "--help"],
    ["builder", "--help"],
    ["pair", "--help"],
    ["money", "--help"],
    ["reflect", "--help"],
    ["wallet", "--help"],
    ["setup", "--help"],
    ["mcp", "--help"],
    ["skills", "--help"],
    ["journal", "--help"],
    ["keys", "--help"],
    ["telegram", "start", "--help"],
    ["hedge", "--help"],
]


@pytest.mark.parametrize("args", HELP_COMMANDS)
def test_command_help_surfaces_are_available(run_cli, args):
    result = run_cli([*args, "--help"] if not args else args)

    assert "Usage:" in result.stdout


def test_wallet_keys_pair_and_journal_readonly_paths(run_cli, isolated_env):
    wallet = run_cli(["wallet", "auto", "--json"])
    wallet_payload = json.loads(wallet.stdout)
    assert wallet_payload["keystore"].startswith(isolated_env["HOME"])

    wallet_list = run_cli(["wallet", "list"])
    assert wallet_payload["address"].lower() in wallet_list.stdout.lower()

    keys = run_cli(["keys", "list"])
    assert "Address" in keys.stdout

    pair_list = run_cli(["pair", "list"])
    assert json.loads(pair_list.stdout)["ok"] is False

    pair_status = run_cli(["pair", "status"])
    assert "Pairing: NONE" in pair_status.stdout
    assert "Run `hl pair connect`" in pair_status.stdout

    journal_view = run_cli(["journal", "view"])
    assert "No journal entries found." in journal_view.stdout


def test_money_commands_refuse_or_defer_without_confirm(run_cli):
    destination = "0x1111111111111111111111111111111111111111"

    withdraw = run_cli(["money", "withdraw", "5", destination], check=False)
    assert withdraw.returncode == 1
    assert "Refusing to move funds without --yes" in withdraw.combined_output

    transfer = run_cli(["money", "transfer", "usd", "5", destination], check=False)
    assert transfer.returncode == 1
    assert "Refusing to move funds without --yes" in transfer.combined_output

    deposit = run_cli(["money", "deposit", "5"], check=False)
    assert deposit.returncode == 1
    assert "Refusing to move funds without --yes" in deposit.combined_output

    bridge = run_cli(["money", "bridge"], check=False)
    assert bridge.returncode == 2
    assert "deferred" in bridge.combined_output


def test_reflect_skills_mcp_builder_and_telegram_safe_paths(run_cli, tmp_path):
    reflect_dir = tmp_path / "reflect"
    history = run_cli(["reflect", "history", "--output-dir", str(reflect_dir)])
    assert "No REFLECT reports found." in history.stdout

    skills = run_cli(["skills", "list"])
    assert "skill(s) found" in skills.stdout

    mcp_help = run_cli(["mcp", "serve", "--help"])
    assert "transport" in mcp_help.stdout

    builder_status = run_cli(["builder", "status"], check=False)
    assert builder_status.returncode in {0, 1}
    assert "Builder" in builder_status.combined_output or "private key" in builder_status.combined_output

    telegram = run_cli(["telegram", "start"], check=False)
    assert telegram.returncode == 1
    assert "TELEGRAM_BOT_TOKEN not set" in telegram.combined_output
