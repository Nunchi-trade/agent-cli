"""Tests for portfolio-level risk checks."""
from __future__ import annotations

import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from execution.portfolio_risk import (
    COIN_TO_GROUP,
    CORRELATION_GROUPS,
    PortfolioRiskConfig,
    PortfolioRiskManager,
    PortfolioRiskState,
)


def test_correlation_groups():
    """Verify COIN_TO_GROUP mapping is correct and complete."""
    total_coins = sum(len(coins) for coins in CORRELATION_GROUPS.values())
    assert len(COIN_TO_GROUP) == total_coins

    # Spot-check some mappings
    assert COIN_TO_GROUP["BTC"] == "large_cap"
    assert COIN_TO_GROUP["ETH"] == "large_cap"
    assert COIN_TO_GROUP["ARB"] == "l2"
    assert COIN_TO_GROUP["OP"] == "l2"
    assert COIN_TO_GROUP["SOL"] == "alt_l1"
    assert COIN_TO_GROUP["DOGE"] == "meme"
    assert COIN_TO_GROUP["FET"] == "ai"
    assert COIN_TO_GROUP["AAVE"] == "defi_blue"


def test_assess_no_positions():
    """Empty portfolio should produce no warnings."""
    mgr = PortfolioRiskManager()
    state = mgr.assess({})
    assert state.warnings == []
    assert not state.blocked
    assert state.margin_utilization == 0.0


def test_assess_correlated_positions():
    """Three L2 positions should trigger a correlation warning."""
    mgr = PortfolioRiskManager(PortfolioRiskConfig(max_correlated_positions=2))
    positions = {
        "ARB-PERP": {"direction": "long", "notional": 1000},
        "OP-PERP": {"direction": "long", "notional": 1000},
        "STRK-PERP": {"direction": "long", "notional": 1000},
    }
    state = mgr.assess(positions)
    assert len(state.warnings) > 0
    assert any("l2" in w for w in state.warnings)


def test_assess_direction_concentration():
    """Four longs should trigger a direction concentration warning."""
    mgr = PortfolioRiskManager(PortfolioRiskConfig(max_same_direction_total=3))
    positions = {
        "BTC-PERP": {"direction": "long", "notional": 5000},
        "SOL-PERP": {"direction": "long", "notional": 2000},
        "DOGE-PERP": {"direction": "long", "notional": 500},
        "FET-PERP": {"direction": "long", "notional": 300},
    }
    state = mgr.assess(positions)
    assert any("Direction concentration" in w for w in state.warnings)


def test_margin_utilization_warn():
    """75% margin utilization should trigger a warning but not block."""
    mgr = PortfolioRiskManager(PortfolioRiskConfig(
        margin_utilization_warn=0.7,
        margin_utilization_block=0.9,
    ))
    state = mgr.assess(
        {"BTC-PERP": {"direction": "long", "notional": 5000}},
        account_state={"account_value": 10000, "total_margin": 7500},
    )
    assert state.margin_utilization == 0.75
    assert any("High margin" in w for w in state.warnings)
    assert not state.blocked


def test_margin_utilization_block():
    """95% margin utilization should block new entries."""
    mgr = PortfolioRiskManager(PortfolioRiskConfig(
        margin_utilization_warn=0.7,
        margin_utilization_block=0.9,
    ))
    state = mgr.assess(
        {"BTC-PERP": {"direction": "long", "notional": 5000}},
        account_state={"account_value": 10000, "total_margin": 9500},
    )
    assert state.margin_utilization == 0.95
    assert state.blocked
    assert "Margin utilization" in state.block_reason


def test_check_entry_ok():
    """Entry into an uncorrelated asset should pass."""
    mgr = PortfolioRiskManager(PortfolioRiskConfig(max_correlated_positions=2))
    current = {
        "BTC-PERP": {"direction": "long", "notional": 5000},
    }
    ok, reason = mgr.check_entry("SOL-PERP", "long", current)
    assert ok
    assert reason == "ok"


def test_check_entry_blocked_correlation():
    """Entry into a 3rd L2 asset should be blocked."""
    mgr = PortfolioRiskManager(PortfolioRiskConfig(max_correlated_positions=2))
    current = {
        "ARB-PERP": {"direction": "long", "notional": 1000},
        "OP-PERP": {"direction": "long", "notional": 1000},
    }
    ok, reason = mgr.check_entry("STRK-PERP", "long", current)
    assert not ok
    assert "correlation" in reason.lower()


def test_check_entry_blocked_direction():
    """Entry into a 4th long should be blocked when max is 3."""
    mgr = PortfolioRiskManager(PortfolioRiskConfig(max_same_direction_total=3))
    current = {
        "BTC-PERP": {"direction": "long", "notional": 5000},
        "SOL-PERP": {"direction": "long", "notional": 2000},
        "DOGE-PERP": {"direction": "long", "notional": 500},
    }
    ok, reason = mgr.check_entry("FET-PERP", "long", current)
    assert not ok
    assert "direction" in reason.lower()


def test_disabled():
    """When enabled=False, everything should pass."""
    mgr = PortfolioRiskManager(PortfolioRiskConfig(enabled=False))
    # Even extreme positions should be fine
    positions = {
        "ARB-PERP": {"direction": "long", "notional": 1000},
        "OP-PERP": {"direction": "long", "notional": 1000},
        "STRK-PERP": {"direction": "long", "notional": 1000},
        "MANTA-PERP": {"direction": "long", "notional": 1000},
    }
    state = mgr.assess(
        positions,
        account_state={"account_value": 1000, "total_margin": 990},
    )
    assert state.warnings == []
    assert not state.blocked

    ok, reason = mgr.check_entry("BLAST-PERP", "long", positions)
    assert ok
    assert reason == "ok"
