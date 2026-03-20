"""Tests for cli/x402_config.py — x402 agentic payment configuration."""
import os
import pytest
from unittest.mock import patch

from cli.x402_config import X402Config


class TestDefaults:
    def test_default_values(self):
        cfg = X402Config()
        assert cfg.wallet_key == ""
        assert cfg.payment_chain == "base"
        assert cfg.proxy_port == 8402

    def test_proxy_url(self):
        cfg = X402Config()
        assert cfg.proxy_url == "http://localhost:8402"

    def test_custom_port(self):
        cfg = X402Config(proxy_port=9999)
        assert cfg.proxy_url == "http://localhost:9999"


class TestEnabled:
    def test_disabled_when_no_wallet(self):
        cfg = X402Config()
        assert cfg.enabled is False

    def test_enabled_when_wallet_set(self):
        cfg = X402Config(wallet_key="0x" + "ab" * 32)
        assert cfg.enabled is True


class TestFromEnv:
    def test_loads_from_env(self):
        env = {
            "BLOCKRUN_WALLET_KEY": "0xtest123",
            "BLOCKRUN_PAYMENT_CHAIN": "solana",
            "BLOCKRUN_PROXY_PORT": "9000",
        }
        with patch.dict(os.environ, env):
            cfg = X402Config.from_env()
        assert cfg.wallet_key == "0xtest123"
        assert cfg.payment_chain == "solana"
        assert cfg.proxy_port == 9000

    def test_defaults_when_env_empty(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in ["BLOCKRUN_WALLET_KEY", "BLOCKRUN_PAYMENT_CHAIN", "BLOCKRUN_PROXY_PORT"]:
                os.environ.pop(key, None)
            cfg = X402Config.from_env()
        assert cfg.wallet_key == ""
        assert cfg.payment_chain == "base"
        assert cfg.proxy_port == 8402
