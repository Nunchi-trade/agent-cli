from cli.mcp_metering import _tool_bucket, report_inference_cost, report_tool_call, upload_rows


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


def test_report_inference_cost_includes_subscription_identity(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=10):
        captured["body"] = req.data.decode("utf-8")
        return FakeResponse()

    monkeypatch.setenv("NUNCHI_METERING_URL", "https://pair.example/api/metering/usage")
    monkeypatch.setenv("NUNCHI_METERING_TOKEN", "token")
    monkeypatch.setenv("NUNCHI_USER_ID", "user_1")
    monkeypatch.setenv("NUNCHI_ACCOUNT_ID", "acct_1")
    monkeypatch.setenv("NUNCHI_SUBSCRIPTION_ID", "sub_1")
    monkeypatch.setenv("NUNCHI_PLAN_ID", "hosted-mcp-inference-starter")
    monkeypatch.setattr("cli.mcp_metering.urllib.request.urlopen", fake_urlopen)

    result = report_inference_cost(
        inference_usd="0.0042",
        input_tokens=12,
        output_tokens=2,
        cached_tokens=3,
        cache_savings_usd="0.0001",
        model="openai/gpt-4.1-mini",
        provider="openrouter",
        requested_model="openrouter/auto",
        route="openrouter/auto",
        decision_call_id="decision-1",
        price_source="openrouter:usage.cost",
    )

    assert result["ok"] is True
    body = __import__("json").loads(captured["body"])
    row = body["rows"][0]["row"]
    assert body["accountId"] == "acct_1"
    assert body["subscriptionId"] == "sub_1"
    assert row["user_id"] == "user_1"
    assert row["account_id"] == "acct_1"
    assert row["subscription_id"] == "sub_1"
    assert row["plan_id"] == "hosted-mcp-inference-starter"
    assert row["inference_usd"] == "0.0042"
    assert row["provider"] == "openrouter"
    assert row["requested_model"] == "openrouter/auto"
    assert row["resolved_model"] == "openai/gpt-4.1-mini"
    assert row["decision_call_id"] == "decision-1"
