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


def test_entrypoint_refusal_measurement_is_safe():
    result = pricing.measure_entrypoint_tool(
        "funding_hedge_execute",
        {"coin": "BTC", "dry_run": True},
    )
    assert result.ok is True
    assert result.detail["containsConfirmationRefusal"] is True
