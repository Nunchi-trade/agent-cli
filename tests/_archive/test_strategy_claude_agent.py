"""Tests for ClaudeStrategy (LLM agent) — tests helper functions only, no API calls."""
import os
import sys
import time

import pytest

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import StrategyContext


def _snap(mid=2500.0, bid=2499.5, ask=2500.5, spread_bps=4.0,
          funding_rate=0.0001, volume_24h=1e6, open_interest=1e5):
    return MarketSnapshot(
        instrument="ETH-PERP", mid_price=mid, bid=bid, ask=ask,
        spread_bps=spread_bps, funding_rate=funding_rate,
        volume_24h=volume_24h, open_interest=open_interest,
        timestamp_ms=int(time.time() * 1000),
    )


def _ctx(pos_qty=0.0, upnl=0.0, rpnl=0.0, reduce_only=False, safe_mode=False, round_num=1):
    return StrategyContext(
        position_qty=pos_qty, unrealized_pnl=upnl, realized_pnl=rpnl,
        reduce_only=reduce_only, safe_mode=safe_mode, round_number=round_num,
    )


class TestDetectProvider:
    def test_claude_model(self):
        from strategies.claude_agent import _detect_provider
        assert _detect_provider("claude-haiku-4-5-20251001") == "claude"
        assert _detect_provider("claude-3-sonnet") == "claude"

    def test_gemini_model(self):
        from strategies.claude_agent import _detect_provider
        assert _detect_provider("gemini-2.0-flash") == "gemini"
        assert _detect_provider("gemini-pro") == "gemini"

    def test_openai_model(self):
        from strategies.claude_agent import _detect_provider
        assert _detect_provider("gpt-4o") == "openai"
        assert _detect_provider("o1-mini") == "openai"
        assert _detect_provider("o3-mini") == "openai"
        assert _detect_provider("o4-mini") == "openai"

    def test_blockrun_model(self):
        from strategies.claude_agent import _detect_provider
        assert _detect_provider("blockrun/auto") == "blockrun"
        assert _detect_provider("blockrun/claude-sonnet") == "blockrun"

    def test_unknown_defaults_to_gemini(self):
        from strategies.claude_agent import _detect_provider
        assert _detect_provider("some-random-model") == "gemini"
        assert _detect_provider("") == "gemini"


class TestParseToolCall:
    def _make_strat(self):
        from strategies.claude_agent import ClaudeStrategy
        return ClaudeStrategy(base_size=0.5, max_position=5.0)

    def test_valid_place_order(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "place_order",
            {"side": "buy", "size": 0.3, "price": 2500.0, "reasoning": "test"},
            _snap(),
        )
        assert len(result) == 1
        assert result[0].action == "place_order"
        assert result[0].side == "buy"
        assert result[0].size == 0.3
        assert result[0].limit_price == 2500.0
        assert result[0].meta["signal"] == "llm_agent"
        assert result[0].meta["reasoning"] == "test"

    def test_size_capped_at_base_size(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "place_order",
            {"side": "sell", "size": 10.0, "price": 2500.0, "reasoning": "big"},
            _snap(),
        )
        assert len(result) == 1
        assert result[0].size == 0.5  # capped at base_size

    def test_invalid_side_returns_empty(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "place_order",
            {"side": "hold", "size": 0.3, "price": 2500.0, "reasoning": "bad"},
            _snap(),
        )
        assert result == []

    def test_zero_size_returns_empty(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "place_order",
            {"side": "buy", "size": 0, "price": 2500.0, "reasoning": "zero"},
            _snap(),
        )
        assert result == []

    def test_zero_price_returns_empty(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "place_order",
            {"side": "buy", "size": 0.3, "price": 0, "reasoning": "no price"},
            _snap(),
        )
        assert result == []

    def test_negative_size_returns_empty(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "place_order",
            {"side": "buy", "size": -1.0, "price": 2500.0, "reasoning": "neg"},
            _snap(),
        )
        assert result == []

    def test_hold_returns_empty(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "hold",
            {"reasoning": "waiting for better entry"},
            _snap(),
        )
        assert result == []

    def test_unknown_tool_returns_empty(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "unknown_tool",
            {"foo": "bar"},
            _snap(),
        )
        assert result == []

    def test_order_type_is_ioc(self):
        strat = self._make_strat()
        result = strat._parse_tool_call(
            "place_order",
            {"side": "buy", "size": 0.3, "price": 2500.0, "reasoning": "test"},
            _snap(),
        )
        assert result[0].order_type == "Ioc"

    def test_instrument_from_snapshot(self):
        strat = self._make_strat()
        snap = _snap()
        snap.instrument = "BTC-PERP"
        result = strat._parse_tool_call(
            "place_order",
            {"side": "buy", "size": 0.3, "price": 50000.0, "reasoning": "btc"},
            snap,
        )
        assert result[0].instrument == "BTC-PERP"


class TestBuildUserMessage:
    def _make_strat(self):
        from strategies.claude_agent import ClaudeStrategy
        return ClaudeStrategy(base_size=0.5, max_position=5.0)

    def test_contains_market_data(self):
        strat = self._make_strat()
        msg = strat._build_user_message(_snap(mid=2500.0), _ctx(round_num=3))
        assert "MARKET DATA" in msg
        assert "2500.0" in msg
        assert "ETH-PERP" in msg

    def test_contains_position_data(self):
        strat = self._make_strat()
        msg = strat._build_user_message(_snap(), _ctx(pos_qty=1.5, upnl=10.0))
        assert "YOUR POSITION" in msg
        assert "1.5" in msg

    def test_contains_risk_state(self):
        strat = self._make_strat()
        msg = strat._build_user_message(_snap(), _ctx(safe_mode=True, reduce_only=True))
        assert "RISK STATE" in msg
        assert "Safe mode: True" in msg
        assert "Reduce only: True" in msg

    def test_contains_constraints(self):
        strat = self._make_strat()
        msg = strat._build_user_message(_snap(), _ctx())
        assert "CONSTRAINTS" in msg
        assert "Max order size: 0.5" in msg
        assert "Max position: 5.0" in msg

    def test_price_history_included(self):
        strat = self._make_strat()
        strat._price_history.append((2500.0, 1000))
        strat._price_history.append((2501.0, 2000))
        msg = strat._build_user_message(_snap(), _ctx())
        assert "RECENT PRICES" in msg

    def test_fill_history_included(self):
        strat = self._make_strat()
        strat._fill_history.append({"side": "buy", "size": 0.3, "price": 2499.0})
        msg = strat._build_user_message(_snap(), _ctx())
        assert "RECENT FILLS" in msg
        assert "BUY" in msg

    def test_no_context_uses_question_mark(self):
        strat = self._make_strat()
        msg = strat._build_user_message(_snap(), None)
        assert "Tick ?" in msg


class TestBuildOpenAITools:
    def test_format(self):
        from strategies.claude_agent import ClaudeStrategy
        strat = ClaudeStrategy()
        tools = strat._build_openai_tools()
        assert len(tools) == 2
        for t in tools:
            assert t["type"] == "function"
            assert "function" in t
            assert "name" in t["function"]
            assert "parameters" in t["function"]

    def test_place_order_tool(self):
        from strategies.claude_agent import ClaudeStrategy
        strat = ClaudeStrategy()
        tools = strat._build_openai_tools()
        place_order = [t for t in tools if t["function"]["name"] == "place_order"][0]
        params = place_order["function"]["parameters"]
        assert "side" in params["properties"]
        assert "size" in params["properties"]
        assert "price" in params["properties"]

    def test_hold_tool(self):
        from strategies.claude_agent import ClaudeStrategy
        strat = ClaudeStrategy()
        tools = strat._build_openai_tools()
        hold = [t for t in tools if t["function"]["name"] == "hold"][0]
        params = hold["function"]["parameters"]
        assert "reasoning" in params["properties"]


class TestClaudeStrategyOnTick:
    def test_zero_mid_returns_empty(self):
        from strategies.claude_agent import ClaudeStrategy
        strat = ClaudeStrategy()
        orders = strat.on_tick(_snap(mid=0.0, bid=0.0, ask=0.0), _ctx())
        assert orders == []

    def test_safe_mode_returns_empty(self):
        from strategies.claude_agent import ClaudeStrategy
        strat = ClaudeStrategy()
        orders = strat.on_tick(_snap(), _ctx(safe_mode=True))
        assert orders == []

    def test_on_tick_without_api_key_returns_empty(self):
        """Without API keys, on_tick should catch the error and return []."""
        from strategies.claude_agent import ClaudeStrategy
        # Use gemini model (default) — no API key set
        strat = ClaudeStrategy(model="gemini-2.0-flash")
        # Clear any env vars
        old_key = os.environ.pop("GEMINI_API_KEY", None)
        old_key2 = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            orders = strat.on_tick(_snap(), _ctx())
            # Should return empty due to caught exception
            assert orders == []
        finally:
            if old_key:
                os.environ["GEMINI_API_KEY"] = old_key
            if old_key2:
                os.environ["GOOGLE_API_KEY"] = old_key2
