from decimal import Decimal

from modules.openrouter_usage import extract_cache_metrics, usage_cost, usage_value


class _Usage:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


def test_usage_value_reads_prompt_tokens():
    usage = _Usage({"prompt_tokens": 12, "completion_tokens": 3})
    assert usage_value(usage, "prompt_tokens", "input_tokens") == 12
    assert usage_value(usage, "completion_tokens", "output_tokens") == 3


def test_extract_cache_metrics_from_prompt_details():
    usage = _Usage(
        {
            "prompt_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 80},
            "cache_savings_usd": "0.0004",
        }
    )
    metrics = extract_cache_metrics(usage, input_tokens=100)
    assert metrics["cached_tokens"] == 80
    assert metrics["cache_hit_rate"] == 0.8
    assert metrics["cache_savings_usd"] == "0.0004"


def test_usage_cost_reads_nested_cost():
    usage = _Usage({"cost": "0.00123"})
    assert usage_cost(usage) == "0.00123"
