from typer.testing import CliRunner

from cli.main import app


runner = CliRunner()


def test_mcp_buy_more_prints_web_auth_topup_url(monkeypatch):
    monkeypatch.setenv("VITE_PAIR_API_URL", "https://web-auth.example")
    monkeypatch.setenv("NUNCHI_ACCOUNT_ID", "acct_123")

    result = runner.invoke(app, ["mcp", "buy-more", "--credits", "200", "--no-open"])

    assert result.exit_code == 0
    assert "Buy more MCP credits in web-auth:" in result.output
    assert "https://web-auth.example/" in result.output
    assert "view=agent-subscriptions" in result.output
    assert "topUp=credits" in result.output
    assert "credits=200" in result.output
    assert "accountId=acct_123" in result.output
