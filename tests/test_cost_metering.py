import json
from decimal import Decimal

from modules.cost_metering import CostMeter, ExperimentContext, OpenRouterPricing


class StaticPricing(OpenRouterPricing):
    def unit_prices(self, model: str):
        return self.input_price, self.output_price, "test"

    def __init__(self, input_price="0.001", output_price="0.002"):
        self.input_price = Decimal(input_price)
        self.output_price = Decimal(output_price)


def test_cost_meter_writes_cost_and_route_ledgers(tmp_path):
    context = ExperimentContext(
        experiment_id="exp-1",
        run_id="run-1",
        agent_id="agent-1",
        job_type="taker",
    )
    meter = CostMeter(
        context=context,
        data_dir=str(tmp_path),
        strategy="claude_agent",
        pricing=StaticPricing(),
    )

    meter.record_llm_call(
        provider="openrouter",
        requested_model="openrouter/fusion",
        resolved_model="anthropic/claude-haiku",
        route="openrouter/fusion",
        input_tokens=10,
        output_tokens=5,
        tick_index=7,
        elapsed_ms=123.4,
        decision_call_id="claude_agent:run-1:tick-7",
    )

    cost_row = json.loads((tmp_path / "cost_ledger.jsonl").read_text().strip())
    route_row = json.loads((tmp_path / "route_ledger.jsonl").read_text().strip())

    assert cost_row["experiment_id"] == "exp-1"
    assert cost_row["job_type"] == "taker"
    assert cost_row["tick_index"] == 7
    assert cost_row["decision_call_id"] == "claude_agent:run-1:tick-7"
    assert cost_row["usd_cost"] == "0.020"
    assert route_row["requested_route"] == "openrouter/fusion"
    assert route_row["decision_call_id"] == "claude_agent:run-1:tick-7"
    assert route_row["resolved_model"] == "anthropic/claude-haiku"


def test_cost_meter_prefers_actual_openrouter_cost(tmp_path):
    context = ExperimentContext(
        experiment_id="exp-1",
        run_id="run-1",
        agent_id="agent-1",
        job_type="taker",
    )
    meter = CostMeter(
        context=context,
        data_dir=str(tmp_path),
        strategy="claude_agent",
        pricing=StaticPricing(input_price="0", output_price="0"),
    )

    meter.record_llm_call(
        provider="openrouter",
        requested_model="openrouter/fusion",
        resolved_model="anthropic/claude-haiku",
        route="openrouter/fusion",
        input_tokens=10,
        output_tokens=5,
        tick_index=7,
        elapsed_ms=123.4,
        actual_usd_cost="0.0042",
    )

    cost_row = json.loads((tmp_path / "cost_ledger.jsonl").read_text().strip())
    route_row = json.loads((tmp_path / "route_ledger.jsonl").read_text().strip())

    assert cost_row["usd_cost"] == "0.0042"
    assert cost_row["pricing_snapshot_source"] == "openrouter:usage.cost"
    assert route_row["actual_usd"] == "0.0042"


def test_cost_meter_records_openrouter_route_metadata(tmp_path):
    context = ExperimentContext(
        experiment_id="exp-1",
        run_id="run-1",
        agent_id="agent-1",
        job_type="taker",
    )
    meter = CostMeter(
        context=context,
        data_dir=str(tmp_path),
        strategy="claude_agent",
        pricing=StaticPricing(),
    )

    meter.record_llm_call(
        provider="openrouter",
        requested_model="openrouter/fusion",
        resolved_model="anthropic/claude-haiku",
        route="openrouter/fusion:preflight",
        input_tokens=10,
        output_tokens=5,
        tick_index=7,
        elapsed_ms=123.4,
        actual_usd_cost="0.0042",
        route_metadata={"generation_id": "gen-1", "router": "openrouter/fusion"},
    )

    cost_row = json.loads((tmp_path / "cost_ledger.jsonl").read_text().strip())
    route_row = json.loads((tmp_path / "route_ledger.jsonl").read_text().strip())

    assert cost_row["route_metadata"]["router"] == "openrouter/fusion"
    assert route_row["router"] == "openrouter/fusion"
    assert route_row["generation_id"] == "gen-1"


def test_cost_meter_records_cache_metrics(tmp_path):
    context = ExperimentContext(
        experiment_id="exp-1",
        run_id="run-1",
        agent_id="agent-1",
        job_type="heartbeat",
    )
    meter = CostMeter(
        context=context,
        data_dir=str(tmp_path),
        strategy="claude_agent",
        pricing=StaticPricing(),
    )

    meter.record_llm_call(
        provider="openrouter",
        requested_model="openrouter/auto",
        resolved_model="openai/gpt-4o-mini",
        route="openrouter/auto",
        input_tokens=100,
        output_tokens=10,
        tick_index=1,
        elapsed_ms=10,
        cached_tokens=80,
        cache_read_input_tokens=80,
        cache_creation_input_tokens=5,
        uncached_input_tokens=20,
        cache_hit_rate=0.8,
        cache_savings_usd="0.0001",
    )

    cost_row = json.loads((tmp_path / "cost_ledger.jsonl").read_text().strip())
    route_row = json.loads((tmp_path / "route_ledger.jsonl").read_text().strip())

    assert cost_row["cached_tokens"] == 80
    assert cost_row["cache_read_input_tokens"] == 80
    assert cost_row["cache_creation_input_tokens"] == 5
    assert cost_row["uncached_input_tokens"] == 20
    assert cost_row["cache_hit_rate"] == 0.8
    assert cost_row["cache_savings_usd"] == "0.0001"
    assert route_row["cached_tokens"] == 80


def test_experiment_context_disabled_without_experiment_id(monkeypatch):
    monkeypatch.delenv("NUNCHI_EXPERIMENT_ID", raising=False)
    context = ExperimentContext.from_env("claude_agent")
    assert context.enabled is False
    assert context.agent_id == "claude_agent"
