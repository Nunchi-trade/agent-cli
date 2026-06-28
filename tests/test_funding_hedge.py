"""Tests for BTCSWP funding hedge proposal surfaces."""
from __future__ import annotations

import json
import sys
import types

from typer.testing import CliRunner

from cli.main import app
from modules.funding_hedge import (
    annualize_funding_rate_8h,
    backtest_funding_hedge_csv,
    funding_hedge_info,
    propose_funding_hedge,
)


runner = CliRunner()


class FakeFastMCP:
    def __init__(self, *args, **kwargs):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def install_fake_mcp(monkeypatch) -> None:
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    server_module = types.ModuleType("mcp.server")
    server_module.fastmcp = fastmcp_module
    mcp_module = types.ModuleType("mcp")
    mcp_module.server = server_module
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)


def test_propose_btcswp_funding_hedge_percent_apr():
    proposal = propose_funding_hedge(
        asset="BTC",
        perp_side="long",
        perp_notional_usd=150_000,
        funding_apr=42,
    )

    assert proposal.hedge_market == "BTCSWP-USDYP"
    assert proposal.hedge_side == "long"
    assert proposal.hedge_notional_usd == 10_000
    assert proposal.effective_hedged_notional_usd == 150_000
    assert proposal.funding_apr == 0.42
    assert proposal.unhedged_funding_cashflow_usd_per_year == -63_000
    assert proposal.target_hedge_cashflow_usd_per_year == 63_000


def test_propose_annualizes_8h_funding_rate():
    apr = annualize_funding_rate_8h(0.0003)
    proposal = propose_funding_hedge(
        asset="BTC",
        perp_side="short",
        perp_notional_usd=90_000,
        funding_rate_8h=0.0003,
    )

    assert proposal.funding_apr == apr
    assert proposal.hedge_notional_usd == 6_000
    assert proposal.unhedged_funding_cashflow_usd_per_year == 29_565


def test_funding_hedge_info_describes_deployed_profile():
    info = funding_hedge_info()

    assert info["deployed_profiles"][0]["asset"] == "BTC"  # type: ignore[index]
    assert info["deployed_profiles"][0]["hedge_market"] == "BTCSWP-USDYP"  # type: ignore[index]
    assert "funding_hedge_info" in info["mcp_tools"]
    assert "funding_rate_8h" in info["csv_required_columns"]
    assert "hedge_agent is an inventory/delta reducer" in info["hedge_agent_distinction"]


def test_hedge_propose_cli_json():
    result = runner.invoke(
        app,
        ["hedge", "propose", "--perp-notional", "150000", "--side", "long", "--funding-apr", "42", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["hedge_market"] == "BTCSWP-USDYP"
    assert payload["hedge_notional_usd"] == 10_000
    assert payload["disclaimer"].startswith("Sizing proposal only.")


def test_hedge_info_cli_json():
    result = runner.invoke(app, ["hedge", "info", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["deployed_profiles"][0]["hedge_market"] == "BTCSWP-USDYP"
    assert "funding_hedge_backtest" in payload["mcp_tools"]


def test_mcp_funding_hedge_propose(monkeypatch):
    install_fake_mcp(monkeypatch)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()
    payload = json.loads(
        server.tools["funding_hedge_propose"](
            asset="BTC",
            perp_side="long",
            perp_notional_usd=150_000,
            funding_apr=42,
        )
    )

    assert payload["hedge_market"] == "BTCSWP-USDYP"
    assert payload["hedge_side"] == "long"
    assert payload["hedge_notional_usd"] == 10_000
    assert payload["coverage_pct"] == 100


def test_mcp_funding_hedge_info(monkeypatch):
    install_fake_mcp(monkeypatch)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()
    payload = json.loads(server.tools["funding_hedge_info"]())

    assert payload["deployed_profiles"][0]["asset"] == "BTC"
    assert "funding_hedge_propose" in payload["mcp_tools"]


def test_mcp_funding_hedge_rejects_roadmap_assets(monkeypatch):
    install_fake_mcp(monkeypatch)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()
    payload = json.loads(server.tools["funding_hedge_propose"](asset="ETH", funding_apr=10))

    assert "only BTC funding hedges are deployed today" in payload["error"]


def test_backtest_csv_idealized_offset(tmp_path):
    csv_path = tmp_path / "funding.csv"
    csv_path.write_text("timestamp,funding_rate_8h\n1,0.0003\n2,-0.0001\n", "utf-8")

    backtest = backtest_funding_hedge_csv(
        csv_path=csv_path,
        asset="BTC",
        perp_side="long",
        perp_notional_usd=150_000,
    )

    assert backtest.periods == 2
    assert backtest.hedge_notional_usd == 10_000
    assert backtest.unhedged_cashflow_usd == -30
    assert backtest.hedge_cashflow_usd == 30
    assert backtest.net_cashflow_usd == 0


def test_backtest_csv_realized_hedge_residual(tmp_path):
    csv_path = tmp_path / "funding.csv"
    csv_path.write_text("date,funding_rate_8h,btcswp_rate_8h\n2026-01-01,0.0003,0.00025\n", "utf-8")

    backtest = backtest_funding_hedge_csv(
        csv_path=csv_path,
        asset="BTC",
        perp_side="long",
        perp_notional_usd=150_000,
    )

    assert backtest.unhedged_cashflow_usd == -45
    assert backtest.hedge_cashflow_usd == 37.5
    assert backtest.net_cashflow_usd == -7.5
    assert backtest.max_period_net_cost_usd == 7.5


def test_hedge_backtest_cli_json(tmp_path):
    csv_path = tmp_path / "funding.csv"
    csv_path.write_text("funding_rate\n0.0003\n-0.0001\n", "utf-8")

    result = runner.invoke(
        app,
        [
            "hedge",
            "backtest",
            "--csv",
            str(csv_path),
            "--perp-notional",
            "150000",
            "--side",
            "long",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["periods"] == 2
    assert payload["hedge_market"] == "BTCSWP-USDYP"
    assert payload["net_cashflow_usd"] == 0


def test_mcp_funding_hedge_backtest(monkeypatch, tmp_path):
    csv_path = tmp_path / "funding.csv"
    csv_path.write_text("funding_rate_8h\n0.0003\n", "utf-8")
    install_fake_mcp(monkeypatch)

    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()
    payload = json.loads(
        server.tools["funding_hedge_backtest"](
            csv_path=str(csv_path),
            asset="BTC",
            perp_side="long",
            perp_notional_usd=150_000,
        )
    )

    assert payload["periods"] == 1
    assert payload["unhedged_cashflow_usd"] == -45
    assert payload["hedge_cashflow_usd"] == 45
