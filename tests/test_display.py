"""Tests for cli/display.py — console formatting functions."""
from __future__ import annotations

import pytest

from cli.display import (
    tick_line,
    shutdown_summary,
    status_table,
    strategy_table,
    account_table,
    _pnl_color,
    _sign,
    GREEN,
    RED,
    DIM,
    RESET,
    BOLD,
    CYAN,
    YELLOW,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestPnlColor:
    def test_positive(self):
        assert _pnl_color(1.0) == GREEN

    def test_negative(self):
        assert _pnl_color(-1.0) == RED

    def test_zero(self):
        assert _pnl_color(0.0) == DIM


class TestSign:
    def test_positive(self):
        assert _sign(1.5) == "+1.5"

    def test_negative(self):
        assert _sign(-1.5) == "-1.5"

    def test_zero(self):
        assert _sign(0) == "+0"


# ---------------------------------------------------------------------------
# tick_line
# ---------------------------------------------------------------------------

class TestTickLine:
    def test_basic_format(self):
        line = tick_line(
            tick=42,
            instrument="ETH-PERP",
            mid=2500.1234,
            pos_qty=0.1,
            avg_entry=2490.0,
            upnl=10.0,
            rpnl=5.0,
            orders_sent=3,
            orders_filled=1,
            risk_ok=True,
        )
        assert "T42" in line
        assert "ETH" in line
        assert "2500.1234" in line
        assert "+0.1" in line
        assert "3 sent 1 filled" in line

    def test_flat_position(self):
        line = tick_line(
            tick=1,
            instrument="BTC-PERP",
            mid=60000.0,
            pos_qty=0,
            avg_entry=0,
            upnl=0,
            rpnl=0,
            orders_sent=0,
            orders_filled=0,
            risk_ok=True,
        )
        assert "flat" in line
        assert "@ " not in line  # no entry price shown when flat

    def test_risk_blocked(self):
        line = tick_line(
            tick=1,
            instrument="ETH-PERP",
            mid=2500.0,
            pos_qty=0,
            avg_entry=0,
            upnl=0,
            rpnl=0,
            orders_sent=0,
            orders_filled=0,
            risk_ok=False,
        )
        assert "BLOCKED" in line

    def test_reduce_only(self):
        line = tick_line(
            tick=1,
            instrument="ETH-PERP",
            mid=2500.0,
            pos_qty=0,
            avg_entry=0,
            upnl=0,
            rpnl=0,
            orders_sent=0,
            orders_filled=0,
            risk_ok=True,
            reduce_only=True,
        )
        assert "REDUCE" in line

    def test_strips_instrument_suffix(self):
        line = tick_line(
            tick=1,
            instrument="SOL-USDYP",
            mid=100.0,
            pos_qty=0,
            avg_entry=0,
            upnl=0,
            rpnl=0,
            orders_sent=0,
            orders_filled=0,
            risk_ok=True,
        )
        assert "SOL" in line
        assert "USDYP" not in line

    def test_negative_pnl(self):
        line = tick_line(
            tick=5,
            instrument="ETH-PERP",
            mid=2500.0,
            pos_qty=-0.5,
            avg_entry=2510.0,
            upnl=-5.0,
            rpnl=-3.0,
            orders_sent=2,
            orders_filled=1,
            risk_ok=True,
        )
        assert "-5.0" in line
        assert "-3.0" in line


# ---------------------------------------------------------------------------
# shutdown_summary
# ---------------------------------------------------------------------------

class TestShutdownSummary:
    def test_basic_format(self):
        summary = shutdown_summary(
            tick_count=100,
            total_placed=50,
            total_filled=20,
            total_pnl=15.5,
            elapsed_s=3600.0,
        )
        assert "Shutdown Summary" in summary
        assert "100" in summary
        assert "50 placed" in summary
        assert "20 filled" in summary
        assert "+15.5" in summary
        assert "3600s" in summary

    def test_zero_ticks(self):
        summary = shutdown_summary(
            tick_count=0,
            total_placed=0,
            total_filled=0,
            total_pnl=0.0,
            elapsed_s=0.0,
        )
        assert "Ticks:   0" in summary
        assert "0 placed, 0 filled" in summary

    def test_zero_fills(self):
        summary = shutdown_summary(
            tick_count=10,
            total_placed=5,
            total_filled=0,
            total_pnl=-2.0,
            elapsed_s=60.0,
        )
        assert "0 filled" in summary
        assert "-2.0" in summary

    def test_negative_pnl(self):
        summary = shutdown_summary(
            tick_count=50,
            total_placed=30,
            total_filled=10,
            total_pnl=-100.0,
            elapsed_s=1800.0,
        )
        assert "-100.0" in summary


# ---------------------------------------------------------------------------
# status_table
# ---------------------------------------------------------------------------

class TestStatusTable:
    def test_basic_format(self):
        import time
        table = status_table(
            strategy="momentum",
            instrument="ETH-PERP",
            network="testnet",
            tick_count=100,
            start_time_ms=int(time.time() * 1000) - 60000,
            pos_qty=0.5,
            avg_entry=2500.0,
            notional=1250.0,
            upnl=10.0,
            rpnl=5.0,
            drawdown_pct=1.5,
            reduce_only=False,
            safe_mode=False,
            total_orders=20,
            total_fills=8,
            recent_fills=[],
        )
        assert "momentum" in table
        assert "ETH-PERP" in table
        assert "testnet" in table
        assert "+0.5" in table

    def test_with_recent_fills(self):
        import time
        table = status_table(
            strategy="test",
            instrument="ETH-PERP",
            network="testnet",
            tick_count=10,
            start_time_ms=int(time.time() * 1000),
            pos_qty=0,
            avg_entry=0,
            notional=0,
            upnl=0,
            rpnl=0,
            drawdown_pct=0,
            reduce_only=False,
            safe_mode=False,
            total_orders=0,
            total_fills=0,
            recent_fills=[
                {"side": "buy", "quantity": "0.1", "price": "2500", "timestamp": "12:00:00"},
            ],
        )
        assert "Recent Fills" in table
        assert "BUY" in table


# ---------------------------------------------------------------------------
# strategy_table
# ---------------------------------------------------------------------------

class TestStrategyTable:
    def test_formats_registry(self):
        registry = {
            "momentum": {"description": "Trend following strategy", "params": {"window": 20}},
            "mm": {"description": "Market making", "params": {"spread": 0.01}},
        }
        table = strategy_table(registry)
        assert "momentum" in table
        assert "Market making" in table
        assert "window=20" in table


# ---------------------------------------------------------------------------
# account_table
# ---------------------------------------------------------------------------

class TestAccountTable:
    def test_formats_account(self):
        state = {
            "address": "0xABC123",
            "account_value": 10000.0,
            "spot_usdc": 500.0,
            "total_margin": 2000.0,
            "withdrawable": 8000.0,
            "spot_balances": [],
        }
        table = account_table(state)
        assert "0xABC123" in table
        assert "$10500.00" in table  # total
        assert "$10000.00" in table  # perps
        assert "$500.00" in table  # spot usdc
