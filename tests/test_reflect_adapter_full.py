"""Tests for modules/reflect_adapter.py — metrics-to-config adjustments."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from modules.reflect_engine import ReflectMetrics
from modules.reflect_adapter import (
    Adjustment,
    adapt,
    apply_adjustments,
    suggest_research_directions,
)


@dataclass
class FakeConfig:
    """Minimal config stub matching attributes used by reflect_adapter."""
    radar_score_threshold: int = 170
    pulse_confidence_threshold: float = 70.0
    pulse_immediate_auto_entry: bool = True
    daily_loss_limit: float = 500.0
    max_same_direction: int = 3


# ---------------------------------------------------------------------------
# adapt()
# ---------------------------------------------------------------------------

class TestAdapt:
    def test_insufficient_data(self):
        metrics = ReflectMetrics(total_round_trips=2)
        config = FakeConfig()
        adjs, summary = adapt(metrics, config)
        assert adjs == []
        assert "insufficient data" in summary

    def test_emergency_tighten(self):
        metrics = ReflectMetrics(
            total_round_trips=5,
            total_fees=100.0,
            gross_pnl=50.0,
        )
        config = FakeConfig()
        adjs, summary = adapt(metrics, config)
        assert "EMERGENCY" in summary
        param_names = [a.param for a in adjs]
        assert "pulse_immediate_auto_entry" in param_names

    def test_fdr_critical(self):
        metrics = ReflectMetrics(
            total_round_trips=5,
            total_fees=10.0,
            gross_pnl=100.0,
            fdr=35.0,
            win_rate=50.0,
        )
        config = FakeConfig()
        adjs, summary = adapt(metrics, config)
        param_names = [a.param for a in adjs]
        assert "radar_score_threshold" in param_names

    def test_fdr_critical_disables_auto_entry(self):
        metrics = ReflectMetrics(
            total_round_trips=5,
            total_fees=10.0,
            gross_pnl=100.0,
            fdr=35.0,
            win_rate=50.0,
        )
        config = FakeConfig(pulse_immediate_auto_entry=True)
        adjs, _ = adapt(metrics, config)
        auto_entry_adjs = [a for a in adjs if a.param == "pulse_immediate_auto_entry"]
        assert len(auto_entry_adjs) == 1
        assert auto_entry_adjs[0].new_value is False

    def test_fdr_warning(self):
        metrics = ReflectMetrics(
            total_round_trips=5,
            total_fees=10.0,
            gross_pnl=100.0,
            fdr=25.0,
            win_rate=50.0,
        )
        config = FakeConfig()
        adjs, _ = adapt(metrics, config)
        param_names = [a.param for a in adjs]
        assert "pulse_confidence_threshold" in param_names

    def test_low_win_rate(self):
        metrics = ReflectMetrics(
            total_round_trips=6,
            win_rate=30.0,
            total_fees=5.0,
            gross_pnl=100.0,
            fdr=5.0,
        )
        config = FakeConfig()
        adjs, _ = adapt(metrics, config)
        param_names = [a.param for a in adjs]
        assert "radar_score_threshold" in param_names
        assert "pulse_confidence_threshold" in param_names

    def test_loss_streak(self):
        metrics = ReflectMetrics(
            total_round_trips=10,
            max_consecutive_losses=6,
            win_rate=50.0,
            total_fees=5.0,
            gross_pnl=100.0,
            fdr=5.0,
        )
        config = FakeConfig(daily_loss_limit=500.0)
        adjs, _ = adapt(metrics, config)
        loss_adjs = [a for a in adjs if a.param == "daily_loss_limit"]
        assert len(loss_adjs) == 1
        assert loss_adjs[0].new_value == 400.0

    def test_direction_imbalance(self):
        metrics = ReflectMetrics(
            total_round_trips=5,
            long_pnl=-20.0,
            short_pnl=30.0,
            long_count=3,
            win_rate=50.0,
            total_fees=5.0,
            gross_pnl=100.0,
            fdr=5.0,
        )
        config = FakeConfig(max_same_direction=3)
        adjs, _ = adapt(metrics, config)
        dir_adjs = [a for a in adjs if a.param == "max_same_direction"]
        assert len(dir_adjs) == 1
        assert dir_adjs[0].new_value == 1

    def test_healthy_relaxes_threshold(self):
        metrics = ReflectMetrics(
            total_round_trips=10,
            win_rate=60.0,
            net_pnl=50.0,
            fdr=10.0,
            total_fees=5.0,
            gross_pnl=100.0,
        )
        config = FakeConfig(radar_score_threshold=200)
        adjs, _ = adapt(metrics, config)
        param_names = [a.param for a in adjs]
        assert "radar_score_threshold" in param_names
        radar_adj = [a for a in adjs if a.param == "radar_score_threshold"][0]
        assert radar_adj.new_value < 200

    def test_no_adjustments_when_already_at_default(self):
        metrics = ReflectMetrics(
            total_round_trips=10,
            win_rate=60.0,
            net_pnl=50.0,
            fdr=10.0,
            total_fees=5.0,
            gross_pnl=100.0,
        )
        # Already at default threshold; no relaxation possible
        config = FakeConfig(radar_score_threshold=170)
        adjs, summary = adapt(metrics, config)
        assert "no adjustments" in summary

    def test_bounds_are_respected(self):
        metrics = ReflectMetrics(
            total_round_trips=6,
            win_rate=30.0,
            total_fees=5.0,
            gross_pnl=100.0,
            fdr=5.0,
        )
        # Start near upper bound
        config = FakeConfig(radar_score_threshold=275, pulse_confidence_threshold=92.0)
        adjs, _ = adapt(metrics, config)
        for a in adjs:
            if a.param == "radar_score_threshold":
                assert a.new_value <= 280
            if a.param == "pulse_confidence_threshold":
                assert a.new_value <= 95.0


# ---------------------------------------------------------------------------
# apply_adjustments()
# ---------------------------------------------------------------------------

class TestApplyAdjustments:
    def test_applies_in_place(self):
        config = FakeConfig()
        adjs = [
            Adjustment(param="radar_score_threshold", old_value=170, new_value=200, reason="test"),
            Adjustment(param="daily_loss_limit", old_value=500.0, new_value=400.0, reason="test"),
        ]
        apply_adjustments(adjs, config)
        assert config.radar_score_threshold == 200
        assert config.daily_loss_limit == 400.0

    def test_empty_adjustments(self):
        config = FakeConfig()
        apply_adjustments([], config)
        assert config.radar_score_threshold == 170


# ---------------------------------------------------------------------------
# suggest_research_directions()
# ---------------------------------------------------------------------------

class TestSuggestResearchDirections:
    def test_insufficient_data(self):
        metrics = ReflectMetrics(total_round_trips=1)
        dirs = suggest_research_directions(metrics)
        assert len(dirs) == 1
        assert "Collect more trades" in dirs[0]

    def test_high_fdr(self):
        metrics = ReflectMetrics(total_round_trips=5, fdr=35.0)
        dirs = suggest_research_directions(metrics)
        assert any("radar_score_threshold" in d for d in dirs)

    def test_healthy_strategy(self):
        metrics = ReflectMetrics(
            total_round_trips=10,
            win_rate=60.0,
            net_pnl=50.0,
            fdr=10.0,
        )
        dirs = suggest_research_directions(metrics)
        assert any("healthy" in d.lower() for d in dirs)

    def test_emergency(self):
        metrics = ReflectMetrics(
            total_round_trips=5,
            total_fees=100.0,
            gross_pnl=50.0,
        )
        dirs = suggest_research_directions(metrics)
        assert any("CRITICAL" in d for d in dirs)
