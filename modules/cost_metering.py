"""Persistent LLM cost metering for hosted-agent pricing experiments."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from parent.store import JSONLStore

log = logging.getLogger("cost_metering")

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
ZERO = Decimal("0")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _env_decimal(name: str) -> Optional[Decimal]:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        log.warning("Invalid decimal in %s=%r; ignoring", name, raw)
        return None


@dataclass(frozen=True)
class ExperimentContext:
    """Stable identifiers shared across ledgers for one agent process."""

    experiment_id: str
    run_id: str
    agent_id: str
    job_type: str

    @classmethod
    def from_env(cls, strategy_id: str) -> "ExperimentContext":
        run_id = os.environ.get("NUNCHI_RUN_ID") or f"manual-{int(time.time())}"
        return cls(
            experiment_id=os.environ.get("NUNCHI_EXPERIMENT_ID", ""),
            run_id=run_id,
            agent_id=os.environ.get("NUNCHI_AGENT_ID") or strategy_id,
            job_type=os.environ.get("NUNCHI_JOB_TYPE", "unknown"),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.experiment_id)


class OpenRouterPricing:
    """Fetches and caches token prices from OpenRouter's models endpoint."""

    def __init__(self) -> None:
        self._prices: Optional[Dict[str, Tuple[Decimal, Decimal]]] = None

    def unit_prices(self, model: str) -> Tuple[Decimal, Decimal, str]:
        override_in = _env_decimal("NUNCHI_PRICE_INPUT_USD_PER_TOKEN")
        override_out = _env_decimal("NUNCHI_PRICE_OUTPUT_USD_PER_TOKEN")
        if override_in is not None and override_out is not None:
            return override_in, override_out, "env:NUNCHI_PRICE_*_USD_PER_TOKEN"

        prices = self._load_prices()
        if model in prices:
            input_price, output_price = prices[model]
            return input_price, output_price, OPENROUTER_MODELS_URL

        return ZERO, ZERO, f"{OPENROUTER_MODELS_URL}:missing:{model}"

    def _load_prices(self) -> Dict[str, Tuple[Decimal, Decimal]]:
        if self._prices is not None:
            return self._prices

        try:
            headers = {}
            api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(OPENROUTER_MODELS_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            log.warning("Could not fetch OpenRouter pricing: %s", exc)
            self._prices = {}
            return self._prices

        prices: Dict[str, Tuple[Decimal, Decimal]] = {}
        for item in payload.get("data", []):
            model_id = item.get("id")
            pricing = item.get("pricing") or {}
            if not model_id:
                continue
            try:
                prompt = Decimal(str(pricing.get("prompt", "0")))
                completion = Decimal(str(pricing.get("completion", "0")))
            except InvalidOperation:
                continue
            prices[str(model_id)] = (prompt, completion)

        self._prices = prices
        return self._prices


class CostMeter:
    """Writes cost and route ledgers for one strategy process."""

    def __init__(
        self,
        context: ExperimentContext,
        data_dir: str,
        strategy: str,
        pricing: Optional[OpenRouterPricing] = None,
    ) -> None:
        self.context = context
        self.strategy = strategy
        self.pricing = pricing or OpenRouterPricing()
        base_dir = Path(os.environ.get("NUNCHI_COST_DATA_DIR") or data_dir)
        self.cost_log = JSONLStore(
            os.environ.get("NUNCHI_COST_LEDGER_PATH") or str(base_dir / "cost_ledger.jsonl")
        )
        self.route_log = JSONLStore(
            os.environ.get("NUNCHI_ROUTE_LEDGER_PATH") or str(base_dir / "route_ledger.jsonl")
        )

    @classmethod
    def from_env(cls, strategy_id: str) -> Optional["CostMeter"]:
        context = ExperimentContext.from_env(strategy_id)
        if not context.enabled:
            return None
        data_dir = os.environ.get("DATA_DIR", "data/cli")
        return cls(context=context, data_dir=data_dir, strategy=strategy_id)

    def record_llm_call(
        self,
        *,
        provider: str,
        requested_model: str,
        resolved_model: str,
        route: str,
        input_tokens: int,
        output_tokens: int,
        tick_index: Optional[int],
        elapsed_ms: float,
        decision_call_id: Optional[str] = None,
        cache_read_input_tokens: Optional[int] = None,
        cache_creation_input_tokens: Optional[int] = None,
        cached_tokens: Optional[int] = None,
        uncached_input_tokens: Optional[int] = None,
        cache_hit_rate: Optional[float] = None,
        cache_savings_usd: Optional[Any] = None,
        actual_usd_cost: Optional[Any] = None,
        route_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        route_metadata = route_metadata or {}

        if provider == "openrouter":
            unit_in, unit_out, price_source = self.pricing.unit_prices(resolved_model)
        else:
            unit_in = _env_decimal(f"NUNCHI_{provider.upper()}_INPUT_USD_PER_TOKEN") or ZERO
            unit_out = _env_decimal(f"NUNCHI_{provider.upper()}_OUTPUT_USD_PER_TOKEN") or ZERO
            price_source = f"env:NUNCHI_{provider.upper()}_*_USD_PER_TOKEN"

        actual_cost = None
        if actual_usd_cost is not None:
            try:
                actual_cost = Decimal(str(actual_usd_cost))
            except InvalidOperation:
                actual_cost = None

        usd_cost = actual_cost
        if usd_cost is None:
            usd_cost = (Decimal(input_tokens) * unit_in) + (Decimal(output_tokens) * unit_out)
        cache_savings = None
        if cache_savings_usd is not None:
            try:
                cache_savings = Decimal(str(cache_savings_usd))
            except InvalidOperation:
                cache_savings = None
        ts_ms = _now_ms()
        row = {
            "experiment_id": self.context.experiment_id,
            "run_id": self.context.run_id,
            "ts": ts_ms,
            "agent_id": self.context.agent_id,
            "strategy": self.strategy,
            "job_type": self.context.job_type,
            "tick_index": tick_index,
            "decision_call_id": decision_call_id,
            "provider": provider,
            "requested_model": requested_model,
            "resolved_model": resolved_model,
            "route": route,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "unit_price_input_usd": str(unit_in),
            "unit_price_output_usd": str(unit_out),
            "usd_cost": str(usd_cost),
            "pricing_snapshot_source": "openrouter:usage.cost" if actual_cost is not None else price_source,
            "elapsed_ms": round(elapsed_ms, 2),
        }
        cache_fields = {
            "cache_read_input_tokens": cache_read_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cached_tokens": cached_tokens,
            "uncached_input_tokens": uncached_input_tokens,
            "cache_hit_rate": cache_hit_rate,
            "cache_savings_usd": str(cache_savings) if cache_savings is not None else None,
        }
        for key, value in cache_fields.items():
            if value is not None:
                row[key] = value
        if route_metadata:
            row["route_metadata"] = route_metadata
        self.cost_log.append(row)

        if provider == "openrouter":
            route_row = {
                "experiment_id": self.context.experiment_id,
                "run_id": self.context.run_id,
                "ts": ts_ms,
                "agent_id": self.context.agent_id,
                "job_type": self.context.job_type,
                "tick_index": tick_index,
                "decision_call_id": decision_call_id,
                "requested_route": route,
                "resolved_model": resolved_model,
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "estimated_usd": str(usd_cost),
                "actual_usd": str(usd_cost),
                "fallback_used": requested_model != resolved_model and route == "openrouter/fusion",
                "fallback_reason": "",
            }
            for key in (
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "cached_tokens",
                "uncached_input_tokens",
                "cache_hit_rate",
                "cache_savings_usd",
            ):
                if key in row:
                    route_row[key] = row[key]
            if route_metadata:
                route_row["metadata"] = route_metadata
                for key in ("generation_id", "router", "routing_strategy", "provider_name"):
                    if key in route_metadata:
                        route_row[key] = route_metadata[key]
            self.route_log.append(route_row)
