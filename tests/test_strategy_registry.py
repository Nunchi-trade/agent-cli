"""Tests for cli/strategy_registry.py — strategy name resolution."""
import pytest

from cli.strategy_registry import (
    STRATEGY_REGISTRY,
    YEX_MARKETS,
    resolve_strategy_path,
    resolve_instrument,
)


class TestResolveStrategyPath:
    def test_valid_short_name(self):
        path = resolve_strategy_path("simple_mm")
        assert path == "strategies.simple_mm:SimpleMMStrategy"

    def test_all_registered_strategies_resolve(self):
        for name in STRATEGY_REGISTRY:
            path = resolve_strategy_path(name)
            assert ":" in path

    def test_full_path_passthrough(self):
        full = "strategies.custom:MyStrategy"
        assert resolve_strategy_path(full) == full

    def test_invalid_name_raises(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            resolve_strategy_path("nonexistent_strategy")

    def test_error_shows_available(self):
        with pytest.raises(ValueError, match="simple_mm"):
            resolve_strategy_path("bad_name")

    def test_claude_agent_registered(self):
        path = resolve_strategy_path("claude_agent")
        assert "ClaudeStrategy" in path

    def test_registry_has_params(self):
        for name, entry in STRATEGY_REGISTRY.items():
            assert "path" in entry
            assert "description" in entry


class TestResolveInstrument:
    def test_standard_perp_unchanged(self):
        assert resolve_instrument("ETH-PERP") == "ETH-PERP"
        assert resolve_instrument("BTC-PERP") == "BTC-PERP"

    def test_yex_name_unchanged(self):
        assert resolve_instrument("VXX-USDYP") == "VXX-USDYP"

    def test_yex_coin_reverse_lookup(self):
        assert resolve_instrument("yex:VXX") == "VXX-USDYP"
        assert resolve_instrument("yex:US3M") == "US3M-USDYP"

    def test_unknown_instrument_passthrough(self):
        assert resolve_instrument("UNKNOWN-PERP") == "UNKNOWN-PERP"
