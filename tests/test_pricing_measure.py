from scripts import pricing_measure as pricing


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


def test_entrypoint_refusal_measurement_is_safe():
    result = pricing.measure_entrypoint_tool(
        "funding_hedge_execute",
        {"coin": "BTC", "dry_run": True},
    )
    assert result.ok is True
    assert result.detail["containsConfirmationRefusal"] is True
