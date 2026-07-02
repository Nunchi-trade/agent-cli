"""Tests for modules/reflect_reporter.py — markdown report generation."""
from __future__ import annotations

import pytest

from modules.reflect_engine import ReflectMetrics
from modules.reflect_reporter import ReflectReporter, _ms_to_human, _pf_str


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestMsToHuman:
    def test_seconds(self):
        assert _ms_to_human(30_000) == "30s"

    def test_minutes(self):
        assert _ms_to_human(300_000) == "5m"

    def test_hours(self):
        assert _ms_to_human(7_200_000) == "2.0h"

    def test_days(self):
        assert _ms_to_human(172_800_000) == "2.0d"


class TestPfStr:
    def test_finite(self):
        assert _pf_str(2.50) == "2.50"

    def test_infinity(self):
        assert _pf_str(float("inf")) == "∞"


# ---------------------------------------------------------------------------
# ReflectReporter.generate
# ---------------------------------------------------------------------------

class TestReflectReporterGenerate:
    @pytest.fixture
    def reporter(self):
        return ReflectReporter()

    @pytest.fixture
    def basic_metrics(self):
        return ReflectMetrics(
            total_trades=10,
            total_round_trips=5,
            win_count=3,
            loss_count=2,
            win_rate=60.0,
            gross_pnl=50.0,
            total_fees=5.0,
            net_pnl=45.0,
            gross_profit_factor=3.0,
            net_profit_factor=2.5,
            fdr=10.0,
            long_count=3,
            long_wins=2,
            long_pnl=30.0,
            short_count=2,
            short_wins=1,
            short_pnl=15.0,
            holding_buckets={"<5m": 2, "5-15m": 3},
            avg_holding_ms=480_000,
            max_consecutive_wins=3,
            max_consecutive_losses=1,
            best_trade_pnl=20.0,
            worst_trade_pnl=-5.0,
            monster_dependency_pct=44.0,
            recommendations=["Hold steady", "Consider tighter stops"],
        )

    def test_report_contains_header(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics, date="2025-03-15")
        assert "# REFLECT Report — 2025-03-15" in report

    def test_report_contains_core_stats(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics)
        assert "| Trades | 10 |" in report
        assert "| Round Trips | 5 |" in report
        assert "60.0%" in report
        assert "$+45.00" in report

    def test_report_contains_fee_analysis(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics)
        assert "FDR (Fee Drag Ratio): 10.0%" in report

    def test_fdr_critical_flag(self, reporter, basic_metrics):
        basic_metrics.fdr = 35.0
        report = reporter.generate(basic_metrics)
        assert "**CRITICAL**" in report

    def test_fdr_warning_flag(self, reporter, basic_metrics):
        basic_metrics.fdr = 25.0
        report = reporter.generate(basic_metrics)
        assert "**WARNING**" in report

    def test_fees_exceed_pnl_message(self, reporter, basic_metrics):
        basic_metrics.total_fees = 100.0
        basic_metrics.gross_pnl = 50.0
        report = reporter.generate(basic_metrics)
        assert "Fees exceed gross PnL" in report

    def test_report_contains_direction_analysis(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics)
        assert "Long" in report
        assert "Short" in report
        assert "$+30.00" in report

    def test_report_contains_holding_periods(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics)
        assert "<5m: 2 trades" in report
        assert "5-15m: 3 trades" in report

    def test_report_contains_streaks(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics)
        assert "Max consecutive wins: 3" in report
        assert "Max consecutive losses: 1" in report

    def test_report_contains_monster_dependency(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics)
        assert "Best trade: $+20.00" in report
        assert "Dependency: 44%" in report

    def test_monster_dependency_high_flag(self, reporter, basic_metrics):
        basic_metrics.monster_dependency_pct = 75.0
        report = reporter.generate(basic_metrics)
        assert "**HIGH**" in report

    def test_report_contains_strategy_breakdown(self, reporter, basic_metrics):
        basic_metrics.strategy_stats = {
            "momentum": {"count": 3, "win_rate": 66.7, "net_pnl": 25.0, "total_fees": 2.0},
        }
        report = reporter.generate(basic_metrics)
        assert "momentum" in report
        assert "Strategy Breakdown" in report

    def test_report_contains_exit_types(self, reporter, basic_metrics):
        basic_metrics.exit_type_counts = {"guard_close": 3, "take_profit": 2}
        report = reporter.generate(basic_metrics)
        assert "guard_close: 3" in report

    def test_report_contains_recommendations(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics)
        assert "Hold steady" in report

    def test_no_recommendations_message(self, reporter):
        metrics = ReflectMetrics()
        report = reporter.generate(metrics)
        assert "No data to generate recommendations" in report

    def test_no_holding_buckets_skips_section(self, reporter):
        metrics = ReflectMetrics()
        report = reporter.generate(metrics)
        assert "Holding Periods" not in report

    def test_default_date_used(self, reporter, basic_metrics):
        report = reporter.generate(basic_metrics)
        # Should contain today's date in header
        assert "# REFLECT Report —" in report


# ---------------------------------------------------------------------------
# ReflectReporter.distill
# ---------------------------------------------------------------------------

class TestReflectReporterDistill:
    @pytest.fixture
    def reporter(self):
        return ReflectReporter()

    def test_distill_basic(self, reporter):
        metrics = ReflectMetrics(
            total_round_trips=10,
            win_rate=55.0,
            net_pnl=30.0,
            total_fees=8.0,
            fdr=15.0,
            net_profit_factor=1.8,
            long_count=6,
            long_pnl=20.0,
            short_count=4,
            short_pnl=10.0,
            recommendations=["Tighten stops"],
        )
        result = reporter.distill(metrics)
        assert "10 round trips" in result
        assert "55% WR" in result
        assert "net $+30.00" in result
        assert "FDR 15%" in result
        assert "Long: 6" in result
        assert "Short: 4" in result
        assert "Tighten stops" in result

    def test_distill_no_direction(self, reporter):
        metrics = ReflectMetrics(total_round_trips=1, net_pnl=5.0)
        result = reporter.distill(metrics)
        assert "Long" not in result

    def test_distill_no_recommendations(self, reporter):
        metrics = ReflectMetrics(total_round_trips=3, net_pnl=10.0)
        result = reporter.distill(metrics)
        assert "Top issue" not in result
