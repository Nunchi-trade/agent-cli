"""Tests for cli/config.py — TradingConfig loading and conversion."""
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from cli.config import TradingConfig


class TestDefaults:
    def test_default_values(self):
        cfg = TradingConfig()
        assert cfg.strategy == "avellaneda_mm"
        assert cfg.instrument == "ETH-PERP"
        assert cfg.mainnet is False
        assert cfg.dry_run is False
        assert cfg.max_leverage == 3.0

    def test_default_is_testnet_risk(self):
        cfg = TradingConfig()
        assert cfg._is_default_risk() is True

    def test_custom_risk_not_default(self):
        cfg = TradingConfig(max_position_qty=20.0)
        assert cfg._is_default_risk() is False


class TestFromYaml:
    def test_loads_valid_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("strategy: engine_mm\ninstrument: BTC-PERP\ntick_interval: 30.0\n")
            f.flush()
            cfg = TradingConfig.from_yaml(f.name)
        os.unlink(f.name)
        assert cfg.strategy == "engine_mm"
        assert cfg.instrument == "BTC-PERP"
        assert cfg.tick_interval == 30.0

    def test_empty_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            f.flush()
            cfg = TradingConfig.from_yaml(f.name)
        os.unlink(f.name)
        assert cfg.strategy == "avellaneda_mm"  # defaults

    def test_unknown_fields_ignored(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("strategy: simple_mm\nunknown_field: 42\n")
            f.flush()
            cfg = TradingConfig.from_yaml(f.name)
        os.unlink(f.name)
        assert cfg.strategy == "simple_mm"
        assert not hasattr(cfg, "unknown_field")

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            TradingConfig.from_yaml("/nonexistent/config.yaml")


class TestToRiskLimits:
    def test_testnet_defaults(self):
        cfg = TradingConfig()
        limits = cfg.to_risk_limits()
        assert limits.max_leverage == 3.0

    def test_mainnet_defaults_override(self):
        cfg = TradingConfig(mainnet=True)
        limits = cfg.to_risk_limits()
        # Mainnet should have different (stricter) defaults
        assert limits.max_leverage <= 3.0

    def test_custom_risk_preserved(self):
        cfg = TradingConfig(max_position_qty=20.0, max_leverage=5.0)
        limits = cfg.to_risk_limits()
        assert float(limits.max_position_qty) == 20.0
        assert float(limits.max_leverage) == 5.0


class TestGetBuilderConfig:
    def test_returns_default_when_no_override(self):
        cfg = TradingConfig()
        builder = cfg.get_builder_config()
        assert builder.builder_address == "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"
        assert builder.fee_rate_tenths_bps == 100

    def test_yaml_builder_override(self):
        cfg = TradingConfig(builder={
            "builder_address": "0xCUSTOM",
            "fee_rate_tenths_bps": 50,
        })
        builder = cfg.get_builder_config()
        assert builder.builder_address == "0xCUSTOM"
        assert builder.fee_rate_tenths_bps == 50

    def test_env_override(self):
        cfg = TradingConfig()
        with patch.dict(os.environ, {"BUILDER_ADDRESS": "0xENV"}):
            builder = cfg.get_builder_config()
        assert builder.builder_address == "0xENV"


class TestGetPrivateKey:
    def test_env_var_fallback(self):
        cfg = TradingConfig()
        with patch("cli.keystore.get_keystore_key", return_value=None):
            with patch.dict(os.environ, {"HL_PRIVATE_KEY": "0xtest"}):
                key = cfg.get_private_key()
        assert key == "0xtest"

    def test_no_key_raises(self):
        cfg = TradingConfig()
        with patch("cli.keystore.get_keystore_key", return_value=None):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("HL_PRIVATE_KEY", None)
                with pytest.raises(RuntimeError, match="No private key"):
                    cfg.get_private_key()

    def test_keystore_takes_priority(self):
        cfg = TradingConfig()
        with patch("cli.keystore.get_keystore_key", return_value="0xfrom_keystore"):
            key = cfg.get_private_key()
        assert key == "0xfrom_keystore"
