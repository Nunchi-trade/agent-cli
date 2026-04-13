from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.radar_market_source import ParadexPublicRadarAdapter, build_radar_market_source


class _FakeApiClient:
    def fetch_markets(self):
        return {
            "results": [
                {"symbol": "BTC-USD-PERP", "asset_kind": "PERP"},
                {"symbol": "ETH-USD-PERP", "asset_kind": "PERP"},
                {"symbol": "BTC-USD-14APR26-70000-C", "asset_kind": "OPTION"},
            ]
        }

    def fetch_markets_summary(self, params=None):
        market = (params or {}).get("market")
        summaries = {
            "BTC-USD-PERP": {
                "volume_24h": "1000000",
                "funding_rate": "0.0001",
                "open_interest": "123",
                "mark_price": "70000",
            },
            "ETH-USD-PERP": {
                "volume_24h": "500000",
                "funding_rate": "-0.0002",
                "open_interest": "456",
                "mark_price": "2000",
            },
        }
        return {"results": [summaries[market]]}

    def fetch_klines(self, symbol, resolution, start_at, end_at):
        assert start_at < end_at
        if resolution == "15":
            return {"results": [[1, 10, 12, 9, 11, 100], [2, 11, 13, 10, 12, 110]]}
        return {
            "results": [
                [1, 10, 12, 9, 11, 100],
                [2, 11, 13, 10, 12, 110],
                [3, 12, 14, 11, 13, 120],
                [4, 13, 15, 12, 14, 130],
            ]
        }


class _FakeParadex:
    def __init__(self, env, auto_auth=False):
        self.env = env
        self.auto_auth = auto_auth
        self.api_client = _FakeApiClient()


class _FakeDirectMockProxy:
    pass


@pytest.fixture
def fake_paradex_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "paradex_py", SimpleNamespace(Paradex=_FakeParadex))


def test_paradex_public_radar_adapter_shapes_markets_and_candles(fake_paradex_module):
    adapter = ParadexPublicRadarAdapter(mainnet=True)

    all_markets = adapter.get_all_markets()
    assert all_markets[0]["universe"] == [{"name": "BTC"}, {"name": "ETH"}]
    assert all_markets[1][0]["dayNtlVlm"] == 1_000_000.0
    assert all_markets[1][1]["funding"] == -0.0002

    candles_1h = adapter.get_candles("BTC", "1h", 3_600_000)
    assert len(candles_1h) == 4
    assert candles_1h[0]["c"] == 11

    candles_4h = adapter.get_candles("BTC", "4h", 14_400_000)
    assert len(candles_4h) == 1
    assert candles_4h[0]["o"] == 10
    assert candles_4h[0]["c"] == 14
    assert candles_4h[0]["h"] == 15.0
    assert candles_4h[0]["l"] == 9.0
    assert candles_4h[0]["v"] == 460.0


def test_build_radar_market_source_paradex_needs_no_private_credentials(fake_paradex_module):
    source, mode = build_radar_market_source(venue="paradex", mainnet=True, mock=False)
    assert source.__class__.__name__ == "ParadexPublicRadarAdapter"
    assert mode == "LIVE (mainnet)"


def test_build_radar_market_source_mock_returns_mock_proxy(monkeypatch):
    monkeypatch.setitem(sys.modules, "cli.hl_adapter", SimpleNamespace(DirectMockProxy=_FakeDirectMockProxy))
    source, mode = build_radar_market_source(venue="hl", mock=True)
    assert source.__class__.__name__ == "_FakeDirectMockProxy"
    assert mode == "MOCK"


def test_radar_cli_once_accepts_paradex_venue():
    repo = Path("/home/hermes/agent-cli")
    cmd = [
        str(repo / ".venv" / "bin" / "python"),
        "-m",
        "cli.main",
        "radar",
        "once",
        "--venue",
        "paradex",
        "--mainnet",
        "--preset",
        "aggressive",
        "--json",
    ]
    result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Venue: paradex" in result.stdout
    assert "\"opportunities\"" in result.stdout

    json_start = result.stdout.find("{\n")
    payload = json.loads(result.stdout[json_start:])
    assert "btc_macro" in payload
    assert isinstance(payload["opportunities"], list)


def test_pulse_cli_once_accepts_paradex_venue():
    repo = Path("/home/hermes/agent-cli")
    cmd = [
        str(repo / ".venv" / "bin" / "python"),
        "-m",
        "cli.main",
        "pulse",
        "once",
        "--venue",
        "paradex",
        "--mainnet",
        "--preset",
        "sensitive",
        "--json",
    ]
    result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Venue: paradex" in result.stdout
    assert "\"signals\"" in result.stdout

    json_start = result.stdout.find("{\n")
    payload = json.loads(result.stdout[json_start:])
    assert "stats" in payload
    assert isinstance(payload["signals"], list)
