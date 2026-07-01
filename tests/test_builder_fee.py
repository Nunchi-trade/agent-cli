"""Tests for builder fee configuration and order passthrough."""
import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from cli.builder_fee import BuilderFeeConfig


# ---------------------------------------------------------------------------
# BuilderFeeConfig
# ---------------------------------------------------------------------------

class TestBuilderFeeConfig:
    def test_defaults_disabled(self):
        """Builder fee enabled by default with Nunchi fee wallet."""
        cfg = BuilderFeeConfig()
        assert cfg.enabled
        assert cfg.builder_address == "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"
        assert cfg.fee_rate_tenths_bps == 100
        assert cfg.to_builder_info() == {"b": "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0", "f": 100}

    def test_enabled_with_address_and_fee(self):
        cfg = BuilderFeeConfig(builder_address="0xABC", fee_rate_tenths_bps=10)
        assert cfg.enabled
        assert cfg.fee_bps == 1.0
        assert cfg.max_fee_rate_str == "0.01%"

    def test_to_builder_info(self):
        cfg = BuilderFeeConfig(builder_address="0xABC", fee_rate_tenths_bps=10)
        info = cfg.to_builder_info()
        assert info == {"b": "0xABC", "f": 10}

    def test_disabled_without_address(self):
        cfg = BuilderFeeConfig(builder_address="", fee_rate_tenths_bps=10)
        assert not cfg.enabled
        assert cfg.to_builder_info() is None

    def test_disabled_without_fee(self):
        cfg = BuilderFeeConfig(builder_address="0xABC", fee_rate_tenths_bps=0)
        assert not cfg.enabled

    def test_from_dict(self):
        cfg = BuilderFeeConfig.from_dict({
            "builder_address": "0xDEF",
            "fee_rate_tenths_bps": 20,
        })
        assert cfg.builder_address == "0xDEF"
        assert cfg.fee_rate_tenths_bps == 20
        assert cfg.fee_bps == 2.0

    def test_from_dict_empty(self):
        cfg = BuilderFeeConfig.from_dict({})
        assert not cfg.enabled

    def test_from_env(self):
        with patch.dict(os.environ, {
            "BUILDER_ADDRESS": "0x123",
            "BUILDER_FEE_TENTHS_BPS": "15",
        }):
            cfg = BuilderFeeConfig.from_env()
            assert cfg.builder_address == "0x123"
            assert cfg.fee_rate_tenths_bps == 15
            assert cfg.fee_bps == 1.5

    def test_from_env_missing_uses_defaults(self):
        """Without env vars, builder fee uses hardcoded Nunchi defaults."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = BuilderFeeConfig.from_env()
            assert cfg.enabled
            assert cfg.builder_address == "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"
            assert cfg.fee_rate_tenths_bps == 100

    def test_fee_bps_fractional(self):
        cfg = BuilderFeeConfig(builder_address="0xA", fee_rate_tenths_bps=5)
        assert cfg.fee_bps == 0.5
        assert cfg.max_fee_rate_str == "0.005%"

    def test_validate_for_broadcast_rejects_disabled(self):
        cfg = BuilderFeeConfig(builder_address="", fee_rate_tenths_bps=0)
        with pytest.raises(RuntimeError, match="builder-code validation failed"):
            cfg.validate_for_broadcast()

    def test_metadata_marks_builder_required(self):
        cfg = BuilderFeeConfig(builder_address="0x0000000000000000000000000000000000000001", fee_rate_tenths_bps=10)
        assert cfg.metadata() == {
            "builder_code_required": True,
            "builder_address": "0x0000000000000000000000000000000000000001",
            "builder_fee_tenths_bps": 10,
            "builder_fee_bps": 1.0,
            "builder_fee_enabled": True,
        }


# ---------------------------------------------------------------------------
# TradingConfig integration
# ---------------------------------------------------------------------------

class TestTradingConfigBuilder:
    def test_get_builder_config_from_yaml_section(self):
        from cli.config import TradingConfig
        cfg = TradingConfig()
        cfg.builder = {
            "builder_address": "0xYAML",
            "fee_rate_tenths_bps": 10,
        }
        bcfg = cfg.get_builder_config()
        assert bcfg.builder_address == "0xYAML"
        assert bcfg.fee_rate_tenths_bps == 10

    def test_get_builder_config_falls_back_to_env(self):
        from cli.config import TradingConfig
        cfg = TradingConfig()
        with patch.dict(os.environ, {
            "BUILDER_ADDRESS": "0xENV",
            "BUILDER_FEE_TENTHS_BPS": "20",
        }):
            bcfg = cfg.get_builder_config()
            assert bcfg.builder_address == "0xENV"
            assert bcfg.fee_rate_tenths_bps == 20


# ---------------------------------------------------------------------------
# OrderManager passthrough
# ---------------------------------------------------------------------------

class TestOrderManagerBuilder:
    def test_builder_passed_to_place_order(self):
        from cli.order_manager import OrderManager
        from common.models import MarketSnapshot, StrategyDecision

        mock_hl = MagicMock()
        mock_hl.get_open_orders.return_value = []
        mock_hl.place_order.return_value = MagicMock(
            oid="test-1", instrument="ETH-PERP", side="buy",
            price=Decimal("2500"), quantity=Decimal("1"),
            timestamp_ms=1000, fee=Decimal("0"),
        )

        builder_info = {"b": "0xTEST", "f": 10}
        om = OrderManager(mock_hl, instrument="ETH-PERP", builder=builder_info)

        snap = MarketSnapshot(instrument="ETH-PERP", mid_price=2500.0,
                              bid=2499.0, ask=2501.0, spread_bps=8.0)
        decisions = [
            StrategyDecision(action="place_order", side="buy",
                             size=1.0, limit_price=2500.0),
        ]
        fills = om.update(decisions, snap)

        assert len(fills) == 1
        mock_hl.place_order.assert_called_once()
        call_kwargs = mock_hl.place_order.call_args
        assert call_kwargs.kwargs.get("builder") == builder_info

    def test_no_builder_when_none(self):
        from cli.order_manager import OrderManager
        from common.models import MarketSnapshot, StrategyDecision

        mock_hl = MagicMock()
        mock_hl.get_open_orders.return_value = []
        mock_hl.place_order.return_value = MagicMock(
            oid="test-2", instrument="ETH-PERP", side="sell",
            price=Decimal("2500"), quantity=Decimal("1"),
            timestamp_ms=1000, fee=Decimal("0"),
        )

        om = OrderManager(mock_hl, instrument="ETH-PERP")

        snap = MarketSnapshot(instrument="ETH-PERP", mid_price=2500.0,
                              bid=2499.0, ask=2501.0, spread_bps=8.0)
        decisions = [
            StrategyDecision(action="place_order", side="sell",
                             size=1.0, limit_price=2500.0),
        ]
        fills = om.update(decisions, snap)

        assert len(fills) == 1
        call_kwargs = mock_hl.place_order.call_args
        assert call_kwargs.kwargs.get("builder") is None


# ---------------------------------------------------------------------------
# ApexRunner accepts builder
# ---------------------------------------------------------------------------

class TestApexRunnerBuilder:
    def test_apex_runner_stores_builder(self):
        from skills.apex.scripts.standalone_runner import ApexRunner

        mock_hl = MagicMock()
        builder_info = {"b": "0xAPEX", "f": 10}
        runner = ApexRunner(hl=mock_hl, builder=builder_info)
        assert runner.builder == builder_info

    def test_apex_runner_builder_default_none(self):
        from skills.apex.scripts.standalone_runner import ApexRunner

        mock_hl = MagicMock()
        runner = ApexRunner(hl=mock_hl)
        assert runner.builder is None
