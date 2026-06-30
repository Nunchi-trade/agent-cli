"""CLI tests for web-auth pair and money commands."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.commands.money import money_app
from cli.commands.pair import pair_app


runner = CliRunner()


def test_pair_list_no_pairing(monkeypatch):
    monkeypatch.setattr("cli.web_auth.get_stored_pairing", lambda: None)

    result = runner.invoke(pair_app, ["list"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["ok"] is False


def test_pair_select_missing_pairing(monkeypatch):
    class Missing(Exception):
        pass

    monkeypatch.setattr("cli.commands.pair._ensure_path", lambda: None)
    monkeypatch.setattr("cli.web_auth.PairingMissingError", Missing)
    monkeypatch.setattr("cli.web_auth.select_pairing_address", lambda wallet: (_ for _ in ()).throw(Missing("missing")))

    result = runner.invoke(pair_app, ["select", "0"])

    assert result.exit_code == 1
    assert "missing" in result.output


def test_pair_open_builds_agent_wallet_url(monkeypatch):
    calls = []
    monkeypatch.setattr("cli.commands.pair._ensure_path", lambda: None)
    monkeypatch.setattr(
        "cli.web_auth.open_wallet_ui",
        lambda **kwargs: calls.append(kwargs) or "https://web-auth.example/?view=agent-wallets",
    )

    result = runner.invoke(
        pair_app,
        [
            "open",
            "--account-id",
            "acct",
            "--agent-id",
            "agent-cli-cost-e2e-maker",
            "--agent-name",
            "Maker",
            "--include-pair-token",
            "--no-browser",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "no_browser": True,
            "account_id": "acct",
            "agent_id": "agent-cli-cost-e2e-maker",
            "agent_name": "Maker",
            "include_pair_token": True,
        }
    ]
    assert "web-auth" in result.output


def test_pair_bind_role_opens_and_persists_maker(monkeypatch):
    calls = []
    class Pairing:
        account_id = "acct"

    monkeypatch.setattr("cli.commands.pair._ensure_path", lambda: None)
    monkeypatch.setattr("cli.web_auth.require_pairing", lambda: Pairing())
    monkeypatch.setattr(
        "cli.web_auth.open_wallet_ui",
        lambda **kwargs: calls.append(("open", kwargs)) or "https://web-auth.example/?view=agent-wallets",
    )
    monkeypatch.setattr(
        "cli.web_auth.wait_for_agent_wallet_binding",
        lambda **kwargs: calls.append(("wait", kwargs)) or {
            "walletAddress": "0x1111111111111111111111111111111111111111"
        },
    )

    result = runner.invoke(pair_app, ["bind-role", "maker", "--timeout", "1", "--no-browser"])

    assert result.exit_code == 0
    assert calls[0][0] == "open"
    assert calls[0][1]["account_id"] == "acct"
    assert calls[0][1]["agent_id"] == "agent-cli-cost-e2e-maker"
    assert calls[0][1]["include_pair_token"] is True
    assert calls[1][0] == "wait"
    assert calls[1][1]["role"] == "maker"
    assert "Bound maker" in result.output


def test_pair_pending_lists_scoped_requests(monkeypatch):
    monkeypatch.setattr("cli.commands.pair._ensure_path", lambda: None)
    monkeypatch.setattr(
        "cli.web_auth.fetch_pending_scoped_requests",
        lambda: [
            {
                "request_id": "req-1",
                "summary": "tiny testnet order",
                "requested_signer": "0x1111111111111111111111111111111111111111",
                "programmatic_eligible": True,
            }
        ],
    )

    result = runner.invoke(pair_app, ["pending"])

    assert result.exit_code == 0
    assert "req-1" in result.output
    assert "eligible" in result.output


def test_pair_approve_calls_backend_with_yes(monkeypatch):
    calls = []
    monkeypatch.setattr("cli.commands.pair._ensure_path", lambda: None)
    monkeypatch.setattr(
        "cli.web_auth.approve_scoped_request",
        lambda request_id, approval="approve": calls.append((request_id, approval)) or {
            "ok": True,
            "approval": {"signer": "0x1111111111111111111111111111111111111111"},
        },
    )

    result = runner.invoke(pair_app, ["approve", "req-1", "--yes"])

    assert result.exit_code == 0
    assert calls == [("req-1", "approve")]
    assert "Approved scoped request req-1" in result.output


def test_money_withdraw_requires_yes_in_non_interactive(monkeypatch):
    class Request:
        summary = "Withdraw 5 USDC"

    monkeypatch.setattr("cli.commands.money._ensure_path", lambda: None)
    monkeypatch.setattr("cli.hl_actions.build_withdraw", lambda amount, destination, mainnet: Request())
    monkeypatch.setattr("cli.commands.money._submit", lambda request, mainnet: None)

    result = runner.invoke(
        money_app,
        ["withdraw", "5", "0x1111111111111111111111111111111111111111"],
    )

    assert result.exit_code == 1
    assert "Refusing to move funds without --yes" in result.output


def test_money_deposit_requires_yes_before_pairing(monkeypatch):
    def fail_build(*args, **kwargs):
        raise AssertionError("deposit should refuse before building transaction")

    monkeypatch.setattr("cli.commands.money._ensure_path", lambda: None)
    monkeypatch.setattr("cli.hl_actions.build_deposit_transaction", fail_build)

    result = runner.invoke(money_app, ["deposit", "5"])

    assert result.exit_code == 1
    assert "Refusing to move funds without --yes" in result.output


def test_money_bridge_is_deferred():
    result = runner.invoke(money_app, ["bridge"])

    assert result.exit_code == 2
    assert "deferred" in result.output
