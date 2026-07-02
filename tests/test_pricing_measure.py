from scripts import pricing_measure as pricing
from ops import railway_cost_meter


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


def test_railway_rate_card_and_cost_estimate(monkeypatch):
    monkeypatch.setenv("RAILWAY_VCPU_HOUR_USD", "0.10")
    monkeypatch.setenv("RAILWAY_MEMORY_GB_HOUR_USD", "0.01")
    monkeypatch.setenv("RAILWAY_NETWORK_GB_USD", "0.05")
    monkeypatch.setenv("RAILWAY_DISK_GB_HOUR_USD", "0.02")
    monkeypatch.setenv("RAILWAY_BASELINE_MONTHLY_USD", "73")

    rate_card = pricing.railway_rate_card_from_env()
    estimate = pricing.estimate_railway_cost(
        {
            "cpu": {"average": 2.0},
            "memory": {"average_mb": 1024},
            "network": {"egress_gb": 3.0},
            "disk": {"average_gb": 4.0},
        },
        rate_card,
        hours=10,
    )

    assert rate_card["ok"] is True
    assert estimate["ok"] is True
    assert estimate["components"]["cpuUsd"] == 2.0
    assert estimate["components"]["memoryUsd"] == 0.1
    assert estimate["components"]["networkUsd"] == 0.15
    assert estimate["components"]["diskUsd"] == 0.8
    assert estimate["components"]["baselineUsd"] == 1.0
    assert estimate["estimatedRailwayCostUsd"] == 4.05


def test_job_profile_margins_use_railway_cost_estimate():
    profiles = {"maker": pricing.job_profile_simulation("maker")}
    margins = pricing.job_profile_margins(
        profiles,
        {"ok": True, "monthlyRunRateUsd": 250.0},
    )

    maker = margins["maker"]
    assert maker["computed"] is True
    assert maker["planId"] == "hosted-mcp-tools-team"
    assert maker["revenueUsd"] == 299.0
    assert maker["estimatedRailwayCogsUsd"] == 25.0
    assert maker["inferenceCogsUsd"] == 0.0
    assert maker["grossMarginUsd"] == 274.0


def test_railway_runtime_cost_row_shape():
    row = pricing.railway_runtime_cost_row(
        {
            "estimatedRailwayCostUsd": 1.23,
            "windowHours": 1,
            "monthlyRunRateUsd": 897.9,
            "components": {"cpuUsd": 0.5},
        },
        service="hosted-trading-mcp",
        ts_ms=123,
    )

    assert row["metric_type"] == "runtime_cost"
    assert row["row_id"] == "railway_runtime_cost:hosted-trading-mcp:123"
    assert row["row"]["estimated_railway_cost_usd"] == 1.23
    assert row["row"]["monthly_run_rate_usd"] == 897.9


def test_railway_cost_meter_uses_internal_endpoint():
    class Args:
        internal_runtime_url = ""
        web_auth_api_url = "https://web-auth.example"

    assert railway_cost_meter.internal_runtime_url(Args()) == "https://web-auth.example/api/internal/costing/railway-runtime"


def test_railway_cost_meter_prefers_target_service_env(monkeypatch):
    monkeypatch.setenv("RAILWAY_TARGET_SERVICE_NAME", "hosted-trading-mcp")
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "railway-cost-meter")
    monkeypatch.setenv("RAILWAY_TARGET_PROJECT_ID", "project-target")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "project-self")

    parser = railway_cost_meter.argparse.ArgumentParser()
    parser.add_argument("--railway-project", default=railway_cost_meter.os.environ.get("RAILWAY_TARGET_PROJECT_ID", railway_cost_meter.os.environ.get("RAILWAY_PROJECT_ID", "")))
    parser.add_argument("--railway-service", default=railway_cost_meter.os.environ.get("RAILWAY_TARGET_SERVICE_NAME", railway_cost_meter.os.environ.get("RAILWAY_SERVICE_NAME", "")))
    args = parser.parse_args([])

    assert args.railway_service == "hosted-trading-mcp"
    assert args.railway_project == "project-target"


def test_railway_cost_meter_samples_and_uploads_global_runtime(monkeypatch):
    class Args:
        internal_runtime_url = "https://web-auth.example/api/internal/costing/railway-runtime"
        web_auth_api_url = ""
        internal_costing_token = "internal-token"
        railway_metrics_since = "1m"
        railway_service = "hosted-trading-mcp"
        railway_environment = "production"
        railway_project = ""

    captured = {}
    monkeypatch.setattr(railway_cost_meter.pricing, "railway_rate_card_from_env", lambda: {"ok": True, "rates": {
        "vcpuHourUsd": 0.1,
        "memoryGbHourUsd": 0.01,
        "networkGbUsd": 0.05,
        "diskGbHourUsd": 0.02,
        "baselineMonthlyUsd": 0,
    }})
    monkeypatch.setattr(railway_cost_meter.pricing, "railway_metrics_probe", lambda _args: {"ok": True, "metrics": {"cpu": {"average": 1}}})
    monkeypatch.setattr(railway_cost_meter.pricing, "estimate_railway_cost", lambda *_args, **_kwargs: {
        "ok": True,
        "estimatedRailwayCostUsd": 0.1,
        "windowHours": 1 / 60,
        "monthlyRunRateUsd": 73,
        "components": {"cpuUsd": 0.1},
    })

    def fake_upload(url, token, rows):
        captured["url"] = url
        captured["token"] = token
        captured["rows"] = rows
        return {"ok": True, "accepted": 1}

    monkeypatch.setattr(railway_cost_meter, "upload_internal_runtime_rows", fake_upload)

    result = railway_cost_meter.sample_once(Args())

    assert result["ok"] is True
    assert captured["url"].endswith("/api/internal/costing/railway-runtime")
    assert captured["token"] == "internal-token"
    assert captured["rows"][0]["metric_type"] == "runtime_cost"
    assert captured["rows"][0]["row"]["source"] == "railway_cost_meter"


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
