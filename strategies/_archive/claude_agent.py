"""LLM-powered trading agent — supports Claude, Gemini, OpenAI, and ClawRouter.

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
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Any, Dict, List, Optional

from common.models import MarketSnapshot, StrategyDecision
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
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith("claude"):
        return "claude"
    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3") or model.startswith("o4"):
        return "openai"
    # Default to gemini
    return "gemini"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class ClaudeStrategy(BaseStrategy):
    """LLM-powered trading strategy — supports Claude and Gemini backends."""

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
        self._blockrun_client = None

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
            self._total_input_tokens += usage.prompt_token_count or 0
            self._total_output_tokens += usage.candidates_token_count or 0
            log.info(
                "Gemini: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
                elapsed_ms,
                usage.prompt_token_count or 0,
                usage.candidates_token_count or 0,
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
            self._total_input_tokens += usage.prompt_tokens or 0
            self._total_output_tokens += usage.completion_tokens or 0
            log.info(
                "OpenAI: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
                elapsed_ms, usage.prompt_tokens or 0, usage.completion_tokens or 0,
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
            self._total_input_tokens += usage.prompt_tokens or 0
            self._total_output_tokens += usage.completion_tokens or 0
            log.info(
                "ClawRouter: %dms, %d/%d tokens (total: %d calls, %d/%d tokens)",
                elapsed_ms, usage.prompt_tokens or 0, usage.completion_tokens or 0,
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
        user_msg = self._build_user_message(snapshot, context)

        try:
            provider = _detect_provider(self.model)
            if provider == "blockrun":
                decisions = self._call_blockrun(user_msg, snapshot)
            elif provider == "gemini":
                decisions = self._call_gemini(user_msg, snapshot)
            elif provider == "claude":
                decisions = self._call_claude(user_msg, snapshot)
            elif provider == "openai":
                decisions = self._call_openai(user_msg, snapshot)
            else:
                decisions = self._call_gemini(user_msg, snapshot)

            for d in decisions:
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
