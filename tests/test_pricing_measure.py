from scripts import pricing_measure as pricing


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def test_builder_revenue_uses_tenths_bps():
    assert pricing.builder_revenue_usd(100_000, 100) == 100.0
    assert pricing.builder_revenue_usd(1_000_000, 25) == 250.0


def test_runtime_c_seat_requires_explicit_input():
    missing = pricing.runtime_c_seat(None)
    assert missing["computed"] is False
    assert "blocker" in missing

    computed = pricing.runtime_c_seat(250)
    assert computed["computed"] is True
    assert computed["byPlan"]["starter"]["cSeatUsd"] == 50
    assert computed["byPlan"]["growth"]["cSeatUsd"] == 25
    assert computed["byPlan"]["team"]["cSeatUsd"] == 5


def test_tool_classification_counts_match_task7_buckets():
    counts = pricing.tool_bucket_counts()
    assert counts["free_read"] == 15
    assert counts["paid_compute"] == 5
    assert counts["safety_gated"] == 7
    assert counts["total"] == 27
    assert counts["costedWithoutWalletAuto"] == 26


def test_inference_anchor_budget_capacity_uses_prompt_anchors():
    capacity = pricing.inference_anchor_budget_capacity({"starter": 10.0})
    assert capacity["openai/gpt-4.1-mini"]["heartbeatsByPlan"]["starter"] == 50_000
    assert round(capacity["openrouter/auto"]["heartbeatsByPlan"]["starter"], 2) == 2702.7
    assert capacity["fusion"]["providedRatioVsMini"] == 146


def test_openrouter_probe_uses_supported_token_minimum(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["payload"] = request.data
        return _FakeResponse(b'{"id":"chatcmpl_test","usage":{"cost":0.000001},"provider":"OpenAI"}')

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(pricing.urllib.request, "urlopen", fake_urlopen)

    result = pricing.openrouter_probe("openai/gpt-4.1-mini")

    assert result["ok"] is True
    assert pricing.json.loads(captured["payload"])["max_tokens"] >= 16


def test_job_profile_simulation_recommends_tier_and_overage():
    maker = pricing.job_profile_simulation("maker")

    assert maker["recommendedPlanId"] == "hosted-mcp-tools-team"
    assert maker["monthlyUsage"]["mcpCalls"] == 45_150
    assert maker["monthlyUsage"]["creditsUsed"] == 459.0
    assert maker["planLimits"]["credits"] == 500
    assert maker["creditRates"]["mcpCallCredits"] == 0.01
    assert maker["overageAgainstRecommendedPlan"]["mcpCalls"] == 0


def test_report_defaults_to_all_job_profiles():
    class Args:
        runtime_monthly_usd = None
        openrouter_live = False
        openrouter_model = "openai/gpt-4.1-mini"
        job_profile = []
        railway_metrics = False
        railway_project = ""
        railway_service = ""
        railway_environment = "production"
        railway_metrics_since = "1h"

    report = pricing.build_report(Args())

    assert set(report["jobProfileSimulations"]) == {"hedge", "maker", "taker"}
    assert report["railwayMetricsProbe"]["skipped"] == "Run with --railway-metrics to collect Railway resource metrics."


def test_railway_metrics_probe_reports_missing_cli(monkeypatch):
    class Args:
        railway_metrics_since = "1h"
        railway_service = ""
        railway_environment = "production"
        railway_project = ""

    monkeypatch.setattr(pricing.shutil, "which", lambda _name: None)

    probe = pricing.railway_metrics_probe(Args())

    assert probe["ok"] is False
    assert "Railway CLI" in probe["blocker"]


def test_entrypoint_refusal_measurement_is_safe():
    result = pricing.measure_entrypoint_tool(
        "funding_hedge_execute",
        {"coin": "BTC", "dry_run": True},
    )
    assert result.ok is True
    assert result.detail["containsConfirmationRefusal"] is True
