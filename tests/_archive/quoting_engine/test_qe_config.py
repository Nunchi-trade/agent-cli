"""Tests for config loading and validation."""
import os
import pytest
from quoting_engine.config import MarketConfig, load_market_config_by_name


def test_default_config_valid():
    cfg = MarketConfig()
    assert cfg.market_name == "funding_rate"
    assert cfg.tick_size == 0.01
    assert cfg.fv_weights.w_oracle == 0.50
    assert cfg.spread.h_fee_bps == 1.0
    assert cfg.ladder.num_levels == 3
    assert cfg.skew.k_inv == 0.5


def test_load_funding_rate_yaml():
    cfg = load_market_config_by_name("funding_rate")
    assert cfg.instrument == "FR-PERP"
    assert cfg.tick_size == 0.0001
    assert cfg.spread.h_fee_bps == 1.5
    assert cfg.ladder.num_levels == 3


def test_load_vxxn_yaml():
    cfg = load_market_config_by_name("vxxn")
    assert cfg.instrument == "VXXN-PERP"
    assert cfg.tick_size == 0.01
    assert cfg.skew.k_inv == 0.7
    assert cfg.ladder.num_levels == 2


def test_load_us3m_yaml():
    cfg = load_market_config_by_name("us3m")
    assert cfg.instrument == "US3M-PERP"
    assert cfg.skew.mode == "both"
    assert cfg.skew.size_skew_factor == 0.3
    assert cfg.ladder.num_levels == 4


def test_fv_weights_sum():
    for name in ("funding_rate", "vxxn", "us3m"):
        cfg = load_market_config_by_name(name)
        w = cfg.fv_weights
        total = w.w_oracle + w.w_external + w.w_microprice + w.w_inventory
        assert abs(total - 1.0) < 1e-9, f"{name}: weights sum to {total}"


def test_invalid_skew_mode():
    with pytest.raises(Exception):
        MarketConfig(skew={"mode": "invalid"})
