"""Shared fixtures for quoting engine tests."""
import pytest
from quoting_engine.config import (
    MarketConfig, FairValueWeights, SpreadParams,
    LadderParams, SkewParams,
)


@pytest.fixture
def default_config() -> MarketConfig:
    return MarketConfig()


@pytest.fixture
def funding_rate_config() -> MarketConfig:
    return MarketConfig(
        market_name="funding_rate",
        instrument="FR-PERP",
        tick_size=0.0001,
        fv_weights=FairValueWeights(
            w_oracle=0.50, w_external=0.00,
            w_microprice=0.30, w_inventory=0.20,
        ),
        spread=SpreadParams(
            h_fee_bps=1.5, vol_scale=1.2,
            rebate_credit_bps=0.2,
            min_spread_bps=2.0, max_spread_bps=40.0,
        ),
        ladder=LadderParams(num_levels=3, delta_bps=1.0, s0=1.0, lam=0.5),
        skew=SkewParams(k_inv=0.5, inv_limit=10.0, mode="both"),
        vol_window=30,
    )
