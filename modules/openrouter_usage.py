"""Shared OpenRouter/OpenAI usage parsing for cost experiments."""
from __future__ import annotations

from typing import Any, Dict, Optional


def usage_value(usage: Any, *names: str) -> int:
    if usage is None:
        return 0
    for name in names:
        value = getattr(usage, name, None)
        if value is not None:
            return int(value or 0)
    data: Dict[str, Any] = {}
    if hasattr(usage, "model_dump"):
        try:
            data = usage.model_dump() or {}
        except Exception:
            data = {}
    for name in names:
        if name in data:
            return int(data.get(name) or 0)
    return 0


def usage_cost(usage: Any) -> Optional[object]:
    if usage is None:
        return None
    cost = getattr(usage, "cost", None)
    if cost is not None:
        return cost
    extra = getattr(usage, "model_extra", None) or {}
    if isinstance(extra, dict) and extra.get("cost") is not None:
        return extra.get("cost")
    if hasattr(usage, "model_dump"):
        return usage.model_dump().get("cost")
    return None


def extract_cache_metrics(usage: Any, *, input_tokens: int) -> Dict[str, Any]:
    if usage is None:
        return {}

    usage_data: Dict[str, Any] = {}
    if hasattr(usage, "model_dump"):
        try:
            usage_data = usage.model_dump() or {}
        except Exception:
            usage_data = {}

    def get_value(name: str) -> Any:
        if hasattr(usage, name):
            return getattr(usage, name)
        return usage_data.get(name)

    prompt_details = get_value("prompt_tokens_details") or usage_data.get("prompt_tokens_details") or {}
    if hasattr(prompt_details, "model_dump"):
        prompt_details = prompt_details.model_dump()
    elif not isinstance(prompt_details, dict):
        prompt_details = {
            "cached_tokens": getattr(prompt_details, "cached_tokens", None),
        }

    cache_read = get_value("cache_read_input_tokens")
    cache_creation = get_value("cache_creation_input_tokens")
    cached_tokens = prompt_details.get("cached_tokens")
    cache_savings = get_value("cache_savings_usd") or get_value("cache_discount_usd")

    if cache_read is None and cached_tokens is None and cache_creation is None and cache_savings is None:
        return {}

    cache_read_int = int(cache_read or 0)
    cache_creation_int = int(cache_creation or 0)
    cached_int = int(cached_tokens if cached_tokens is not None else cache_read_int)
    uncached_input_tokens = max(0, int(input_tokens or 0) - cached_int)
    cache_hit_rate = (cached_int / input_tokens) if input_tokens else 0.0
    metrics: Dict[str, Any] = {
        "cache_read_input_tokens": cache_read_int,
        "cache_creation_input_tokens": cache_creation_int,
        "cached_tokens": cached_int,
        "uncached_input_tokens": uncached_input_tokens,
        "cache_hit_rate": round(cache_hit_rate, 6),
    }
    if cache_savings is not None:
        metrics["cache_savings_usd"] = cache_savings
    return metrics
