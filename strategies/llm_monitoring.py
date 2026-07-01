"""LLM portfolio-monitoring strategy for hosted-agent pricing experiments.

This strategy intentionally never emits orders. It reuses AIStrategy's
provider/tool plumbing so monitoring heartbeats are metered like trading jobs.
"""
from __future__ import annotations

from typing import Dict, List

from common.models import MarketSnapshot, StrategyDecision
from strategies.ai_agent import AIStrategy


MONITORING_SYSTEM_PROMPT = """\
You are a portfolio monitoring agent for a hosted Hyperliquid agent stack.

Each tick you receive market data, position state, and risk context. Evaluate
whether the operator should be alerted about drift, risk, unusual market
conditions, or follow-up investigation. You do not trade.

You MUST use the hold tool. Keep reasoning brief and operational.
"""


class LLMMonitoringStrategy(AIStrategy):
    """LLM monitoring heartbeat that records inference cost but places no orders."""

    def __init__(
        self,
        strategy_id: str = "llm_monitoring",
        model: str = "openrouter/fusion",
        base_size: float = 0.0,
        max_position: float = 0.0,
        **kwargs,
    ):
        super().__init__(
            strategy_id=strategy_id,
            model=model,
            base_size=base_size,
            max_position=max_position,
            system_prompt=kwargs.pop("system_prompt", MONITORING_SYSTEM_PROMPT),
            **kwargs,
        )

    def _parse_tool_call(
        self,
        name: str,
        args: Dict,
        snapshot: MarketSnapshot,
    ) -> List[StrategyDecision]:
        reasoning = args.get("reasoning", "") if args else ""
        if reasoning:
            # Parent class logs the LLM decision details; returning [] enforces
            # monitoring-only behavior even if a model chooses place_order.
            import logging

            logging.getLogger("llm_agent").info("Monitoring decision: %s", reasoning)
        return []
