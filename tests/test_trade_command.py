from __future__ import annotations

import typer
from typer.testing import CliRunner

from cli.commands.trade import trade_cmd


class FakeConfig:
    max_notional_usd = 100.0

    def get_private_key(self) -> str:
        return "0x" + "1" * 64


class FakeRawHL:
    def __init__(self, private_key: str, testnet: bool):
        self.private_key = private_key
        self.testnet = testnet


class FakeDirectHL:
    placed = []

    def __init__(self, raw_hl):
        self.raw_hl = raw_hl

    def place_order(self, **kwargs):
        self.placed.append(kwargs)
        return None


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command("trade")(trade_cmd)
    return app


def _patch_trade_deps(monkeypatch):
    import cli.commands.trade as trade_module
    import cli.config as config_module
    import cli.hl_adapter as hl_adapter_module
    import cli.strategy_registry as strategy_registry_module
    import parent.hl_proxy as hl_proxy_module

    FakeDirectHL.placed = []
    monkeypatch.setattr(config_module, "TradingConfig", FakeConfig)
    monkeypatch.setattr(hl_adapter_module, "DirectHLProxy", FakeDirectHL)
    monkeypatch.setattr(hl_proxy_module, "HLProxy", FakeRawHL)
    monkeypatch.setattr(strategy_registry_module, "resolve_instrument", lambda instrument: instrument)
    monkeypatch.setattr(trade_module.sys.stdin, "isatty", lambda: False)


def test_trade_dry_run_does_not_submit(monkeypatch):
    _patch_trade_deps(monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        _app(),
        ["ETH-PERP", "buy", "0.01", "--price", "2500", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "Dry run: order not submitted." in result.output
    assert FakeDirectHL.placed == []


def test_trade_refuses_noninteractive_without_yes(monkeypatch):
    _patch_trade_deps(monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        _app(),
        ["ETH-PERP", "buy", "0.01", "--price", "2500"],
    )

    assert result.exit_code == 2
    assert "Refusing to trade non-interactively without --yes." in result.output
    assert FakeDirectHL.placed == []


def test_trade_rejects_notional_above_cap(monkeypatch):
    _patch_trade_deps(monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        _app(),
        [
            "ETH-PERP",
            "buy",
            "1",
            "--price",
            "2500",
            "--yes",
            "--max-notional",
            "100",
        ],
    )

    assert result.exit_code == 1
    assert "exceeds max notional" in result.output
    assert FakeDirectHL.placed == []
