"""LLM-powered trading agent — supports Claude, Gemini, OpenAI, OpenRouter, and ClawRouter.

Uses structured tool/function calling to make trading decisions each tick.
The LLM receives market data, position state, and risk context, then decides
to place orders or hold.

Usage:
    # Gemini (default — fast, free tier available)
    hl run claude_agent --mock --max-ticks 5 --tick 15
    hl run claude_agent -i ETH-PERP --tick 15

    # Claude
    hl run claude_agent -i ETH-PERP --tick 15 --model claude-haiku-4-5-20251001

    # Gemini Flash
    hl run claude_agent -i ETH-PERP --tick 15 --model gemini-2.0-flash

    # ClawRouter (x402 — pay with USDC, no API key needed)
    hl run claude_agent -i ETH-PERP --tick 15 --model blockrun/auto

    # OpenRouter (Nunchi hosted default)
    hl run claude_agent -i ETH-PERP --tick 15 --model openrouter/auto
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Any, Dict, List, Optional

from common.models import MarketSnapshot, StrategyDecision
from modules.cost_metering import CostMeter
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

log = logging.getLogger("llm_agent")

# ---------------------------------------------------------------------------
# System prompt — defines the agent's trading persona
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """\
You are an autonomous trading agent operating on Hyperliquid.

Each tick you receive a market data snapshot and your current position state.
You must decide whether to place an order or hold.

Rules:
- You manage a single instrument position
- You receive: price data (mid, bid, ask, spread, funding), your position \
(qty, entry price, unrealized PnL, realized PnL), risk state, and recent history
- You MUST use exactly one tool call: either place_order or hold
- Consider: price trend, spread width, funding rate, your inventory, drawdown
- Be conservative: use small sizes, tight risk management
- If reduce_only is true, you may ONLY reduce your current position \
(sell if long, buy if short)
- If safe_mode is true, you MUST hold — no orders allowed
- Keep reasoning brief (1-2 sentences)
"""

# ---------------------------------------------------------------------------
# Tool definitions — Anthropic format (converted to Gemini format at runtime)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "place_order",
        "description": "Place a limit order on the exchange. "
        "The order will be IOC (immediate-or-cancel).",
        "input_schema": {
            "type": "object",
            "properties": {
                "side": {
                    "type": "string",
                    "enum": ["buy", "sell"],
                    "description": "Order side",
                },
                "size": {
                    "type": "number",
                    "description": "Order size in base asset units",
                },
                "price": {
                    "type": "number",
                    "description": "Limit price in USD",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief reasoning for this trade",
                },
            },
            "required": ["side", "size", "price", "reasoning"],
        },
    },
    {
        "name": "hold",
        "description": "Do nothing this tick — place no orders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reasoning": {
                    "type": "string",
                    "description": "Why you are holding this tick",
                },
            },
            "required": ["reasoning"],
        },
    },
]


def _detect_provider(model: str) -> str:
    """Detect LLM provider from model name."""
    if model.startswith("blockrun"):
        return "blockrun"
    if model.startswith("openrouter/") or os.environ.get("AI_PROVIDER", "").lower() == "openrouter":
        return "openrouter"
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith("claude"):
        return "claude"
    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3") or model.startswith("o4"):
        return "openai"
    # Default to gemini
    return "gemini"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class ClaudeStrategy(BaseStrategy):
    """LLM-powered trading strategy with multiple hosted/local inference backends."""

    def __init__(
        self,
        strategy_id: str = "claude_agent",
        model: str = "gemini-2.0-flash",
        base_size: float = 0.5,
        max_position: float = 5.0,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_tokens: int = 256,
        price_history_len: int = 20,
        fill_history_len: int = 10,
    ):
        super().__init__(strategy_id=strategy_id)
        self.model = model
        self.base_size = base_size
        self.max_position = max_position
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens

        # Rolling history buffers
        self._price_history: deque = deque(maxlen=price_history_len)
        self._fill_history: deque = deque(maxlen=fill_history_len)

        # Token usage tracking
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._api_calls = 0

        # Lazy-init clients
        self._anthropic_client = None
        self._gemini_client = None
        self._openai_client = None
        self._openrouter_client = None
        self._blockrun_client = None

        # Optional hosted-agent pricing meter. Enabled only when
        # NUNCHI_EXPERIMENT_ID is present in the environment.
        self._cost_meter = CostMeter.from_env(strategy_id)
        self._current_tick_index: Optional[int] = None
        self._current_decision_call_id: Optional[str] = None
        self.last_decision_call_id: Optional[str] = None
        self._last_llm_decision_tick: Optional[int] = None

    # ------------------------------------------------------------------
    # Client initialization
    # ------------------------------------------------------------------

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError(
                    "anthropic package required. Install: pip3 install anthropic"
                )
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable required")
            self._anthropic_client = anthropic.Anthropic(api_key=api_key)
        return self._anthropic_client

    def _get_gemini_client(self):
        if self._gemini_client is None:
            try:
                from google import genai
            except ImportError:
                raise ImportError(
                    "google-genai package required. Install: pip3 install google-genai"
                )
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError(
                    "GEMINI_API_KEY or GOOGLE_API_KEY environment variable required"
                )
            self._gemini_client = genai.Client(api_key=api_key)
        return self._gemini_client

    def _get_openai_client(self):
        if self._openai_client is None:
            try:
                import openai
            except ImportError:
                raise ImportError(
                    "openai package required. Install: pip3 install openai"
                )
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable required")
            self._openai_client = openai.OpenAI(api_key=api_key)
        return self._openai_client

    def _get_openrouter_client(self):
        if self._openrouter_client is None:
            try:
                import openai
            except ImportError:
                raise ImportError(
                    "openai package required for OpenRouter. Install: pip3 install openai"
                )
            api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY or AI_API_KEY environment variable required")
            self._openrouter_client = openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "https://agent.nunchi.trade"),
                    "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Nunchi Hosted Agent"),
                },
            )
        return self._openrouter_client

    def _resolve_openrouter_model(self) -> str:
        """Allow a confirmed Fusion override while preserving the requested route."""
        if self.model == "openrouter/fusion":
            return (
                os.environ.get("OPENROUTER_FUSION_MODEL")
                or os.environ.get("NUNCHI_OPENROUTER_FUSION_MODEL")
                or self.model
            )
        return self.model

    def _openrouter_fusion_plugins(
        self,
        *,
        default_preset: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Build optional OpenRouter Fusion plugin config from env."""
        if self.model != "openrouter/fusion":
            return None

        preset = (
            os.environ.get("OPENROUTER_FUSION_PRESET")
            or os.environ.get("NUNCHI_OPENROUTER_FUSION_PRESET")
            or default_preset
        )
        analysis_models_raw = (
            os.environ.get("OPENROUTER_FUSION_ANALYSIS_MODELS")
            or os.environ.get("NUNCHI_OPENROUTER_FUSION_ANALYSIS_MODELS")
        )
        judge_model = (
            os.environ.get("OPENROUTER_FUSION_JUDGE_MODEL")
            or os.environ.get("NUNCHI_OPENROUTER_FUSION_JUDGE_MODEL")
        )

        plugin: Dict[str, Any] = {"id": "fusion"}
        if preset:
            plugin["preset"] = preset
        if analysis_models_raw:
            plugin["analysis_models"] = [
                item.strip() for item in analysis_models_raw.split(",") if item.strip()
            ]
        if judge_model:
            plugin["model"] = judge_model

        for env_name, field in (
            ("OPENROUTER_FUSION_MAX_TOOL_CALLS", "max_tool_calls"),
            ("OPENROUTER_FUSION_MAX_COMPLETION_TOKENS", "max_completion_tokens"),
        ):
            raw = os.environ.get(env_name)
            if raw:
                try:
                    plugin[field] = int(raw)
                except ValueError:
                    log.warning("Ignoring invalid %s=%r", env_name, raw)

        if len(plugin) == 1:
            return None
        return [plugin]

    def _force_openrouter_fusion(self) -> bool:
        return _env_bool("OPENROUTER_FORCE_FUSION") or _env_bool("NUNCHI_OPENROUTER_FORCE_FUSION")

    def _llm_decision_interval_ticks(self) -> int:
        raw = (
            os.environ.get("NUNCHI_LLM_DECISION_INTERVAL_TICKS")
            or os.environ.get("LLM_DECISION_INTERVAL_TICKS")
            or "1"
        )
        try:
            return max(1, int(raw))
        except ValueError:
            log.warning("Ignoring invalid LLM decision interval: %r", raw)
            return 1

    def _should_run_llm_decision(self, context: Optional[StrategyContext]) -> bool:
        interval = self._llm_decision_interval_ticks()
        if interval <= 1 or context is None:
            return True

        tick = context.round_number
        if self._last_llm_decision_tick is None:
            return True
        return tick - self._last_llm_decision_tick >= interval

    def _fetch_openrouter_generation_metadata(self, generation_id: Optional[str]) -> Dict[str, Any]:
        """Fetch post-generation routing metadata when OpenRouter exposes it."""
        if not generation_id or not _env_bool("OPENROUTER_FETCH_GENERATION_METADATA", True):
            return {}

        try:
            import json as _json
            import urllib.parse
            import urllib.request

            api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY")
            if not api_key:
                return {"generation_id": generation_id}

            query = urllib.parse.urlencode({"id": generation_id})
            req = urllib.request.Request(
                f"https://openrouter.ai/api/v1/generation?{query}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
            data = payload.get("data") or {}
        except Exception as exc:
            log.debug("Could not fetch OpenRouter generation metadata: %s", exc)
            return {"generation_id": generation_id}

        metadata: Dict[str, Any] = {"generation_id": generation_id}
        for source_key, dest_key in (
            ("router", "router"),
            ("strategy", "routing_strategy"),
            ("provider_name", "provider_name"),
            ("model", "metadata_model"),
            ("total_cost", "metadata_total_cost"),
        ):
            if source_key in data:
                metadata[dest_key] = data.get(source_key)
        for key in ("attempts", "pipeline"):
            if key in data:
                metadata[key] = data.get(key)
        return metadata

    def _record_usage(
        self,
        *,
        provider: str,
        requested_model: str,
        resolved_model: str,
        route: str,
        input_tokens: int,
        output_tokens: int,
        elapsed_ms: float,
        usage: Any = None,
    ) -> None:
        if self._cost_meter is None:
            return
        cache_metrics = self._extract_cache_metrics(usage, input_tokens=input_tokens)
        self._cost_meter.record_llm_call(
            provider=provider,
            requested_model=requested_model,
            resolved_model=resolved_model,
            route=route,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tick_index=self._current_tick_index,
            elapsed_ms=elapsed_ms,
            decision_call_id=self._current_decision_call_id,
            **cache_metrics,
        )

    def _record_openrouter_usage(
        self,
        *,
        requested_model: str,
        resolved_model: str,
        input_tokens: int,
        output_tokens: int,
        elapsed_ms: float,
        usage: Any,
        route: Optional[str] = None,
        route_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        actual_cost = getattr(usage, "cost", None)
        if actual_cost is None and hasattr(usage, "model_extra"):
            actual_cost = usage.model_extra.get("cost")
        if actual_cost is None and hasattr(usage, "model_dump"):
            actual_cost = usage.model_dump().get("cost")
        if self._cost_meter is None:
            return
        cache_metrics = self._extract_cache_metrics(usage, input_tokens=input_tokens)
        self._cost_meter.record_llm_call(
            provider="openrouter",
            requested_model=requested_model,
            resolved_model=resolved_model,
            route=route or requested_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tick_index=self._current_tick_index,
            elapsed_ms=elapsed_ms,
            decision_call_id=self._current_decision_call_id,
            **cache_metrics,
            actual_usd_cost=actual_cost,
            route_metadata=route_metadata,
        )

    def _extract_cache_metrics(self, usage: Any, *, input_tokens: int) -> Dict[str, Any]:
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

    # ------------------------------------------------------------------
    # Build prompt
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext],
    ) -> str:
        parts = []

        parts.append(f"=== MARKET DATA (Tick {context.round_number if context else '?'}) ===")
        parts.append(f"Instrument: {snapshot.instrument}")
        parts.append(f"Mid: {snapshot.mid_price:.4f}")
        parts.append(f"Bid: {snapshot.bid:.4f}  Ask: {snapshot.ask:.4f}")
        parts.append(f"Spread: {snapshot.spread_bps:.1f} bps")
        parts.append(f"Funding rate: {snapshot.funding_rate:.6f}")
        parts.append(f"Open interest: {snapshot.open_interest:.0f}")
        parts.append(f"24h volume: {snapshot.volume_24h:.0f}")
        parts.append("")

        if context:
            parts.append("=== YOUR POSITION ===")
            parts.append(f"Qty: {context.position_qty:+.4f}")
            parts.append(f"Notional: ${context.position_notional:.2f}")
            parts.append(f"Unrealized PnL: ${context.unrealized_pnl:+.2f}")
            parts.append(f"Realized PnL: ${context.realized_pnl:+.2f}")
            parts.append("")

            parts.append("=== RISK STATE ===")
            dd_pct = context.meta.get("drawdown_pct", 0.0) * 100
            parts.append(f"Reduce only: {context.reduce_only}")
            parts.append(f"Safe mode: {context.safe_mode}")
            parts.append(f"Drawdown: {dd_pct:.2f}%")
            parts.append("")

        if self._price_history:
            parts.append("=== RECENT PRICES (newest first) ===")
            for mid, ts in reversed(self._price_history):
                parts.append(f"  {mid:.4f}")
            parts.append("")

        if self._fill_history:
            parts.append("=== RECENT FILLS ===")
            for fill in reversed(list(self._fill_history)):
                parts.append(
                    f"  {fill['side'].upper()} {fill['size']:.4f} "
                    f"@ {fill['price']:.4f}"
                )
            parts.append("")

        parts.append("=== CONSTRAINTS ===")
        parts.append(f"Max order size: {self.base_size}")
        parts.append(f"Max position: {self.max_position}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Claude backend
    # ------------------------------------------------------------------

    def _call_claude(self, user_msg: str, snapshot: MarketSnapshot) -> List[StrategyDecision]:
        client = self._get_anthropic_client()
        t0 = time.time()

        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            tools=TOOLS,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_msg}],
        )

        elapsed_ms = (time.time() - t0) * 1000
        self._api_calls += 1
        self._total_input_tokens += response.usage.input_tokens
        self._total_output_tokens += response.usage.output_tokens
        self._record_usage(
            provider="claude",
            requested_model=self.model,
            resolved_model=self.model,
            route=self.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            elapsed_ms=elapsed_ms,
            usage=response.usage,
        )

        log.info(
            "Claude: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
            elapsed_ms, response.usage.input_tokens, response.usage.output_tokens,
            self._api_calls, self._total_input_tokens, self._total_output_tokens,
        )

        decisions = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            decisions.extend(self._parse_tool_call(block.name, block.input, snapshot))
        return decisions

    # ------------------------------------------------------------------
    # Gemini backend
    # ------------------------------------------------------------------

    def _build_gemini_tools(self):
        """Convert our tool definitions to Gemini function declarations."""
        from google.genai import types

        declarations = []
        for tool in TOOLS:
            schema = tool["input_schema"]
            # Build properties dict for Gemini
            props = {}
            for prop_name, prop_def in schema["properties"].items():
                p = {"type": prop_def["type"].upper(), "description": prop_def.get("description", "")}
                if "enum" in prop_def:
                    p["enum"] = prop_def["enum"]
                props[prop_name] = p

            declarations.append(types.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        k: types.Schema(**{kk.lower(): vv for kk, vv in v.items()})
                        for k, v in props.items()
                    },
                    required=schema.get("required", []),
                ),
            ))
        return types.Tool(function_declarations=declarations)

    def _call_gemini(self, user_msg: str, snapshot: MarketSnapshot) -> List[StrategyDecision]:
        from google.genai import types

        client = self._get_gemini_client()
        t0 = time.time()

        gemini_tools = self._build_gemini_tools()

        response = client.models.generate_content(
            model=self.model,
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=[gemini_tools],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY",
                    ),
                ),
                max_output_tokens=self.max_tokens,
            ),
        )

        elapsed_ms = (time.time() - t0) * 1000
        self._api_calls += 1

        # Track tokens
        usage = response.usage_metadata
        if usage:
            input_tokens = usage.prompt_token_count or 0
            output_tokens = usage.candidates_token_count or 0
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._record_usage(
                provider="gemini",
                requested_model=self.model,
                resolved_model=self.model,
                route=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_ms=elapsed_ms,
                usage=usage,
            )
            log.info(
                "Gemini: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
                elapsed_ms,
                input_tokens,
                output_tokens,
                self._api_calls,
                self._total_input_tokens,
                self._total_output_tokens,
            )
        else:
            log.info("Gemini: %dms (total: %d calls)", elapsed_ms, self._api_calls)

        # Parse function calls from response
        decisions = []
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    fc = part.function_call
                    args = dict(fc.args) if fc.args else {}
                    decisions.extend(self._parse_tool_call(fc.name, args, snapshot))

        return decisions

    # ------------------------------------------------------------------
    # OpenAI backend
    # ------------------------------------------------------------------

    def _build_openai_tools(self) -> List[Dict]:
        """Convert our tool defs to OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in TOOLS
        ]

    def _call_openai(self, user_msg: str, snapshot: MarketSnapshot) -> List[StrategyDecision]:
        import json as _json

        client = self._get_openai_client()
        t0 = time.time()

        response = client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_msg},
            ],
            tools=self._build_openai_tools(),
            tool_choice="required",
        )

        elapsed_ms = (time.time() - t0) * 1000
        self._api_calls += 1
        usage = response.usage
        if usage:
            input_tokens = usage.prompt_tokens or 0
            output_tokens = usage.completion_tokens or 0
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._record_usage(
                provider="openai",
                requested_model=self.model,
                resolved_model=getattr(response, "model", self.model) or self.model,
                route=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_ms=elapsed_ms,
                usage=usage,
            )
            log.info(
                "OpenAI: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
                elapsed_ms, input_tokens, output_tokens,
                self._api_calls, self._total_input_tokens, self._total_output_tokens,
            )

        decisions = []
        msg = response.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
                decisions.extend(self._parse_tool_call(tc.function.name, args, snapshot))
        return decisions

    def _call_openrouter(self, user_msg: str, snapshot: MarketSnapshot) -> List[StrategyDecision]:
        import json as _json

        client = self._get_openrouter_client()
        requested_model = self.model
        resolved_model = self._resolve_openrouter_model()

        if requested_model == "openrouter/fusion" and self._force_openrouter_fusion():
            fusion_analysis = self._call_openrouter_fusion_preflight(
                client=client,
                requested_model=requested_model,
                resolved_model=resolved_model,
                user_msg=user_msg,
            )
            if fusion_analysis:
                user_msg = (
                    f"{user_msg}\n\n"
                    "=== FORCED OPENROUTER FUSION ANALYSIS ===\n"
                    f"{fusion_analysis[:4000]}"
                )

        t0 = time.time()

        extra_body: Dict[str, Any] = {}
        plugins = self._openrouter_fusion_plugins()
        if plugins:
            extra_body["plugins"] = plugins

        request_kwargs: Dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "tools": self._build_openai_tools(),
            "tool_choice": "required",
        }
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        response = client.chat.completions.create(
            **request_kwargs
        )

        elapsed_ms = (time.time() - t0) * 1000
        self._api_calls += 1
        usage = response.usage
        if usage:
            input_tokens = usage.prompt_tokens or 0
            output_tokens = usage.completion_tokens or 0
            response_model = getattr(response, "model", None) or resolved_model
            metadata = self._fetch_openrouter_generation_metadata(getattr(response, "id", None))
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._record_openrouter_usage(
                requested_model=requested_model,
                resolved_model=response_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_ms=elapsed_ms,
                usage=usage,
                route_metadata=metadata,
            )
            log.info(
                "OpenRouter: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
                elapsed_ms, input_tokens, output_tokens,
                self._api_calls, self._total_input_tokens, self._total_output_tokens,
            )

        decisions = []
        msg = response.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
                decisions.extend(self._parse_tool_call(tc.function.name, args, snapshot))
        return decisions

    def _call_openrouter_fusion_preflight(
        self,
        *,
        client: Any,
        requested_model: str,
        resolved_model: str,
        user_msg: str,
    ) -> str:
        """Force OpenRouter Fusion before the trading tool decision."""
        max_tokens = int(os.environ.get("OPENROUTER_FUSION_PREFLIGHT_MAX_TOKENS", "384"))
        plugins = self._openrouter_fusion_plugins(default_preset="general-budget") or [
            {"id": "fusion", "preset": "general-budget"}
        ]
        t0 = time.time()

        response = client.chat.completions.create(
            model=resolved_model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Fusion routing preflight for a Hyperliquid trading agent. "
                        "Use the OpenRouter Fusion tool to compare market interpretations, "
                        "then summarize only the consensus, disagreements, and any action bias. "
                        "Do not place orders."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
            tool_choice="required",
            extra_body={"plugins": plugins},
        )

        elapsed_ms = (time.time() - t0) * 1000
        self._api_calls += 1
        usage = response.usage
        if usage:
            input_tokens = usage.prompt_tokens or 0
            output_tokens = usage.completion_tokens or 0
            response_model = getattr(response, "model", None) or resolved_model
            metadata = self._fetch_openrouter_generation_metadata(getattr(response, "id", None))
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._record_openrouter_usage(
                requested_model=requested_model,
                resolved_model=response_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_ms=elapsed_ms,
                usage=usage,
                route="openrouter/fusion:preflight",
                route_metadata=metadata,
            )
            log.info(
                "OpenRouter Fusion preflight: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
                elapsed_ms, input_tokens, output_tokens,
                self._api_calls, self._total_input_tokens, self._total_output_tokens,
            )

        msg = response.choices[0].message
        return msg.content or ""

    # ------------------------------------------------------------------
    # ClawRouter / BlockRun backend (x402 — pay with USDC, no API key)
    # ------------------------------------------------------------------

    def _get_blockrun_client(self):
        """Create OpenAI-compatible client pointing at ClawRouter local proxy.

        ClawRouter (github.com/BlockRunAI/ClawRouter) runs on localhost:8402
        and exposes an OpenAI-compatible API. Auth is handled by x402 wallet
        signatures — no API key needed. The dummy key "x402" satisfies the
        OpenAI client's required api_key param.
        """
        if self._blockrun_client is None:
            try:
                import openai
            except ImportError:
                raise ImportError(
                    "openai package required for ClawRouter. Install: pip3 install openai"
                )
            from cli.x402_config import X402Config
            cfg = X402Config.from_env()
            base_url = f"{cfg.proxy_url}/v1"
            # x402 uses wallet-based auth, not API keys.
            # "x402" is a dummy key to satisfy the OpenAI client constructor.
            self._blockrun_client = openai.OpenAI(api_key="x402", base_url=base_url)
            log.info("ClawRouter client initialized: %s (chain=%s)", base_url, cfg.payment_chain)
        return self._blockrun_client

    def _call_blockrun(self, user_msg: str, snapshot: MarketSnapshot) -> List[StrategyDecision]:
        """Route through ClawRouter — OpenAI-compatible, x402 payment."""
        import json as _json

        client = self._get_blockrun_client()
        t0 = time.time()

        # ClawRouter accepts OpenAI format; model can be "blockrun/auto" for
        # smart routing or a specific model like "blockrun/claude-sonnet"
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_msg},
            ],
            tools=self._build_openai_tools(),
            tool_choice="required",
        )

        elapsed_ms = (time.time() - t0) * 1000
        self._api_calls += 1
        usage = response.usage
        if usage:
            input_tokens = usage.prompt_tokens or 0
            output_tokens = usage.completion_tokens or 0
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            self._record_usage(
                provider="blockrun",
                requested_model=self.model,
                resolved_model=getattr(response, "model", self.model) or self.model,
                route=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_ms=elapsed_ms,
                usage=usage,
            )
            log.info(
                "ClawRouter: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
                elapsed_ms, input_tokens, output_tokens,
                self._api_calls, self._total_input_tokens, self._total_output_tokens,
            )

        decisions = []
        msg = response.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
                decisions.extend(self._parse_tool_call(tc.function.name, args, snapshot))
        return decisions

    # ------------------------------------------------------------------
    # Shared tool call parsing
    # ------------------------------------------------------------------

    def _parse_tool_call(
        self, name: str, args: Dict, snapshot: MarketSnapshot
    ) -> List[StrategyDecision]:
        if name == "place_order":
            side = args.get("side", "")
            size = float(args.get("size", 0))
            price = float(args.get("price", 0))
            reasoning = args.get("reasoning", "")

            if side not in ("buy", "sell") or size <= 0 or price <= 0:
                log.warning("Invalid order from LLM: side=%s size=%s price=%s",
                            side, size, price)
                return []

            size = min(size, self.base_size)

            log.info("LLM decision: %s %.4f @ %.2f — %s",
                     side.upper(), size, price, reasoning)

            return [StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side=side,
                size=size,
                limit_price=round(price, 2),
                order_type="Ioc",
                meta={
                    "signal": "llm_agent",
                    "reasoning": reasoning,
                    "model": self.model,
                },
            )]

        elif name == "hold":
            reasoning = args.get("reasoning", "")
            log.info("LLM decision: HOLD — %s", reasoning)
            return []

        return []

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def on_tick(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext] = None,
    ) -> List[StrategyDecision]:
        if snapshot.mid_price <= 0:
            return []

        if context and context.safe_mode:
            log.info("Safe mode active, holding")
            return []

        self._price_history.append((snapshot.mid_price, snapshot.timestamp_ms))
        if not self._should_run_llm_decision(context):
            tick = context.round_number if context else "?"
            log.info(
                "LLM decision cadence: skipping tick %s (interval=%d ticks)",
                tick,
                self._llm_decision_interval_ticks(),
            )
            return []

        user_msg = self._build_user_message(snapshot, context)
        self._current_tick_index = context.round_number if context else None
        run_id = (
            self._cost_meter.context.run_id
            if self._cost_meter is not None
            else f"manual-{int(time.time())}"
        )
        tick_label = self._current_tick_index if self._current_tick_index is not None else "unknown"
        self._current_decision_call_id = f"{self.strategy_id}:{run_id}:tick-{tick_label}"
        self.last_decision_call_id = self._current_decision_call_id

        try:
            if context:
                self._last_llm_decision_tick = context.round_number
            provider = _detect_provider(self.model)
            if provider == "blockrun":
                decisions = self._call_blockrun(user_msg, snapshot)
            elif provider == "openrouter":
                decisions = self._call_openrouter(user_msg, snapshot)
            elif provider == "gemini":
                decisions = self._call_gemini(user_msg, snapshot)
            elif provider == "claude":
                decisions = self._call_claude(user_msg, snapshot)
            elif provider == "openai":
                decisions = self._call_openai(user_msg, snapshot)
            else:
                decisions = self._call_gemini(user_msg, snapshot)

            for d in decisions:
                if self._current_decision_call_id:
                    d.meta = {**(d.meta or {}), "decision_call_id": self._current_decision_call_id}
                if d.action == "place_order":
                    self._fill_history.append({
                        "side": d.side,
                        "size": d.size,
                        "price": d.limit_price,
                    })

            return decisions

        except Exception as e:
            log.error("LLM API call failed: %s", e)
            return []
        finally:
            self._current_tick_index = None
            self._current_decision_call_id = None
