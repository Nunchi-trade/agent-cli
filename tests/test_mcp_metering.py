from cli.mcp_metering import _tool_bucket, report_tool_call, upload_rows


def test_tool_bucket():
    assert _tool_bucket("radar_run") == "paid_compute"
    assert _tool_bucket("trade") == "safety_gated"
    assert _tool_bucket("account") == "free"


def test_report_tool_call_skips_without_env(monkeypatch):
    monkeypatch.delenv("NUNCHI_METERING_URL", raising=False)
    result = report_tool_call("account")
    assert result["skipped"] == "metering_not_configured"


def test_upload_rows_builds_generic_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("utf-8")
        return FakeResponse()

    monkeypatch.setenv("NUNCHI_METERING_URL", "https://pair.example/api/metering/usage")
    monkeypatch.setenv("NUNCHI_METERING_TOKEN", "token")
    monkeypatch.setenv("NUNCHI_ACCOUNT_ID", "acct_1")
    monkeypatch.setenv("NUNCHI_PLAN_ID", "hosted-mcp-tools-starter")
    monkeypatch.setattr("cli.mcp_metering.urllib.request.urlopen", fake_urlopen)

    result = upload_rows([{"row_id": "abc", "metric_type": "mcp_tool", "row": {"calls": 1}}])
    assert result["ok"] is True
    body = __import__("json").loads(captured["body"])
    assert body["accountId"] == "acct_1"
    assert body["rows"][0]["metric_type"] == "mcp_tool"
