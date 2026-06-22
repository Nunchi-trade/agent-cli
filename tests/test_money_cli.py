"""CLI tests for `hl money` dry-run and guard behavior."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.commands.money import money_app


runner = CliRunner()


def test_withdraw_dry_run_prints_typed_data(monkeypatch):
    monkeypatch.setattr("cli.commands.money._ensure_path", lambda: None)
    monkeypatch.setattr("cli.hl_money._timestamp_ms", lambda: 123)

    result = runner.invoke(
        money_app,
        ["withdraw", "10", "0x1111111111111111111111111111111111111111", "--dry-run"],
    )

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["typed_data"]["primaryType"] == "HyperliquidTransaction:Withdraw"


def test_deposit_requires_yes_when_not_dry_run(monkeypatch):
    monkeypatch.setattr("cli.commands.money._ensure_path", lambda: None)
    monkeypatch.setattr(
        "cli.hl_money.build_deposit_transaction",
        lambda amount, mainnet: (
            {"from": "0x1111111111111111111111111111111111111111", "to": "0x2222222222222222222222222222222222222222"},
            "Deposit 5 USDC",
        ),
    )

    result = runner.invoke(money_app, ["deposit", "5"])

    assert result.exit_code == 1
    assert "Refusing to move funds without --yes" in result.output


def test_bridge_dry_run_prints_transaction(monkeypatch):
    monkeypatch.setattr("cli.commands.money._ensure_path", lambda: None)
    quote = {"transactionRequest": {"from": "0x1111111111111111111111111111111111111111", "to": "0x2222222222222222222222222222222222222222", "value": "0x0", "chainId": 8453}}
    monkeypatch.setattr("cli.hl_money.fetch_lifi_bridge_quote", lambda **kwargs: quote)
    monkeypatch.setattr("cli.hl_money.lifi_quote_to_transaction", lambda quote, from_address=None: quote["transactionRequest"])

    result = runner.invoke(money_app, ["bridge", "--from-chain", "8453", "--amount", "5", "--dry-run"])

    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["transaction"]["chainId"] == 8453
