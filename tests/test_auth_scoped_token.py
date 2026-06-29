from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.main import app


runner = CliRunner()


def test_auth_import_status_export_and_revoke(monkeypatch, tmp_path):
    token_path = tmp_path / "scoped-token.json"
    monkeypatch.setenv("NUNCHI_SCOPED_TOKEN_PATH", str(token_path))

    result = runner.invoke(
        app,
        [
            "auth",
            "import",
            "--token",
            "scoped-token-123",
            "--address",
            "0x" + "8" * 40,
            "--permission-tier",
            "testnet_trading",
            "--network",
            "testnet",
            "--max-order-size",
            "0.5",
            "--max-hedge-notional",
            "12000",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["stored"] is True
    assert payload["token"] != "scoped-token-123"
    assert token_path.exists()

    status = runner.invoke(app, ["auth", "status", "--json"])
    assert status.exit_code == 0
    status_payload = json.loads(status.stdout)
    assert status_payload["configured"] is True
    assert status_payload["address"] == "0x" + "8" * 40
    assert status_payload["max_order_size"] == 0.5
    assert status_payload["max_hedge_notional"] == 12000.0

    exported = runner.invoke(app, ["auth", "export-env"])
    assert exported.exit_code == 0
    assert "export NUNCHI_WEB_AUTH_PAIR_TOKEN='scoped-token-123'" in exported.stdout
    assert "export NUNCHI_WEB_AUTH_ADDRESS='0x" + "8" * 40 in exported.stdout
    assert "export NUNCHI_MAX_HEDGE_NOTIONAL='12000.0'" in exported.stdout

    revoked = runner.invoke(app, ["auth", "revoke"])
    assert revoked.exit_code == 0
    assert not token_path.exists()

    empty = runner.invoke(app, ["auth", "status", "--json"])
    assert json.loads(empty.stdout)["configured"] is False
