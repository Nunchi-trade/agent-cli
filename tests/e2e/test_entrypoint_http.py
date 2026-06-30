"""Hosted entrypoint HTTP E2E tests."""
from __future__ import annotations

import json
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import scripts.entrypoint as entrypoint


pytestmark = pytest.mark.e2e


@pytest.fixture
def entrypoint_server(monkeypatch, tmp_path):
    monkeypatch.setenv("RUN_MODE", "strategy")
    monkeypatch.setenv("STRATEGY", "ai_agent")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("AI_MODEL", "openrouter/fusion")
    monkeypatch.setenv("HL_TESTNET", "true")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    original_token = entrypoint.AUTH_TOKEN
    original_child = entrypoint.CHILD_PROC
    entrypoint.AUTH_TOKEN = "test-secret"
    entrypoint.CHILD_PROC = None

    server = HTTPServer(("127.0.0.1", 0), entrypoint.HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}", tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        entrypoint.AUTH_TOKEN = original_token
        entrypoint.CHILD_PROC = original_child


def _request_json(base_url: str, path: str, *, method: str = "GET", body: dict | None = None, token: str | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_health_status_metrics_and_pricing_endpoints(entrypoint_server):
    base_url, data_dir = entrypoint_server
    (data_dir / "apex").mkdir()
    (data_dir / "apex" / "metrics.json").write_text('{"tick_count": 1}', encoding="utf-8")
    (data_dir / "cost_ledger.jsonl").write_text('{"usd_cost": "0.01"}\n', encoding="utf-8")

    status, health = _request_json(base_url, "/health")
    assert status == 200
    assert health["status"] == "ok"
    assert health["mode"] == "strategy"
    assert health["strategy"] == "ai_agent"

    status, api_status = _request_json(base_url, "/api/status")
    assert status == 200
    assert api_status["status"] == "stopped"

    status, strategies = _request_json(base_url, "/api/strategies")
    assert status == 200
    assert "avellaneda_mm" in strategies["strategies"]
    assert "BTCSWP-USDYP" in strategies["markets"]

    status, metrics = _request_json(base_url, "/metrics")
    assert status == 200
    assert metrics["tick_count"] == 1

    status, pricing = _request_json(base_url, "/api/pricing?limit=5")
    assert status == 200
    assert pricing["ai_model"] == "openrouter/fusion"
    assert pricing["ledger_exists"]["cost"] is True
    assert pricing["ledgers"]["cost"][0]["usd_cost"] == "0.01"


def test_control_endpoints_require_auth_and_write_config(entrypoint_server):
    base_url, data_dir = entrypoint_server

    status, unauthorized = _request_json(
        base_url,
        "/api/configure",
        method="POST",
        body={"preset": "aggressive"},
    )
    assert status == 401
    assert unauthorized["error"] == "unauthorized"

    status, configured = _request_json(
        base_url,
        "/api/configure",
        method="POST",
        body={"preset": "aggressive"},
        token="test-secret",
    )
    assert status == 200
    assert configured["status"] == "ok"
    assert json.loads((data_dir / "apex" / "config-override.json").read_text())["preset"] == "aggressive"

    status, paused = _request_json(base_url, "/api/pause", method="POST", token="test-secret")
    assert status == 200
    assert paused["status"] == "paused"

    status, resumed = _request_json(base_url, "/api/resume", method="POST", token="test-secret")
    assert status == 200
    assert resumed["status"] == "resumed"
