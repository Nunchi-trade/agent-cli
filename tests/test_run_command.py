from __future__ import annotations

from cli.commands.run import run_cmd


class _FakeStrategy:
    def __init__(self, strategy_id: str, **params):
        self.strategy_id = strategy_id
        self.params = params


class _FakeEngine:
    last_init = None
    last_run = None

    def __init__(self, **kwargs):
        _FakeEngine.last_init = kwargs

    def run(self, max_ticks: int = 0, resume: bool = True):
        _FakeEngine.last_run = {"max_ticks": max_ticks, "resume": resume}


def test_run_cmd_dry_run_uses_live_venue_data_unless_mock(monkeypatch):
    captured = {}

    def fake_build_venue_adapter(*, venue, mainnet=False, mock=False):
        captured["venue"] = venue
        captured["mainnet"] = mainnet
        captured["mock"] = mock
        return object(), "LIVE (mainnet)"

    monkeypatch.setattr("cli.commands.run.build_venue_adapter", fake_build_venue_adapter)
    monkeypatch.setattr("cli.strategy_registry.resolve_instrument", lambda instrument: instrument)
    monkeypatch.setattr("cli.strategy_registry.resolve_strategy_path", lambda strategy: f"strategies.{strategy}:Fake")
    monkeypatch.setattr("sdk.strategy_sdk.loader.load_strategy", lambda path: _FakeStrategy)
    monkeypatch.setattr("cli.engine.TradingEngine", _FakeEngine)

    run_cmd(
        strategy="avellaneda_mm",
        instrument="SOL-USD-PERP",
        venue="paradex",
        tick_interval=2.0,
        config=None,
        mainnet=True,
        dry_run=True,
        max_ticks=1,
        resume=False,
        data_dir="data/test-run",
        mock=False,
        model=None,
    )

    assert captured == {"venue": "paradex", "mainnet": True, "mock": False}
    assert _FakeEngine.last_init is not None
    assert _FakeEngine.last_init["dry_run"] is True
    assert _FakeEngine.last_run == {"max_ticks": 1, "resume": False}


def test_run_cmd_mock_still_forces_mock_adapter(monkeypatch):
    captured = {}

    def fake_build_venue_adapter(*, venue, mainnet=False, mock=False):
        captured["venue"] = venue
        captured["mainnet"] = mainnet
        captured["mock"] = mock
        return object(), "MOCK"

    monkeypatch.setattr("cli.commands.run.build_venue_adapter", fake_build_venue_adapter)
    monkeypatch.setattr("cli.strategy_registry.resolve_instrument", lambda instrument: instrument)
    monkeypatch.setattr("cli.strategy_registry.resolve_strategy_path", lambda strategy: f"strategies.{strategy}:Fake")
    monkeypatch.setattr("sdk.strategy_sdk.loader.load_strategy", lambda path: _FakeStrategy)
    monkeypatch.setattr("cli.engine.TradingEngine", _FakeEngine)

    run_cmd(
        strategy="avellaneda_mm",
        instrument="SOL-USD-PERP",
        venue="paradex",
        tick_interval=2.0,
        config=None,
        mainnet=True,
        dry_run=True,
        max_ticks=1,
        resume=False,
        data_dir="data/test-run",
        mock=True,
        model=None,
    )

    assert captured == {"venue": "paradex", "mainnet": True, "mock": True}
