"""Shared fixtures for the agent-cli test suite."""
from __future__ import annotations

import time
import tempfile

import pytest

from common.models import MarketSnapshot
from sdk.strategy_sdk.base import StrategyContext


@pytest.fixture
def snapshot():
    return MarketSnapshot(
        instrument="ETH-PERP",
        mid_price=2500.0,
        bid=2499.5,
        ask=2500.5,
        spread_bps=4.0,
        timestamp_ms=int(time.time() * 1000),
    )


@pytest.fixture
def context():
    return StrategyContext(round_number=1)


@pytest.fixture
def tmp_data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d
