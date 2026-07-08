"""Tests for modules.reflect_convergence — convergence tracking and hysteresis."""
from __future__ import annotations

import pytest

from modules.reflect_convergence import ConvergenceTracker, DirectionalHysteresis


class TestConvergenceTracker:
    def test_insufficient_data(self):
        ct = ConvergenceTracker(lookback_cycles=3)
        ct.record_cycle(win_rate=50, net_pnl=100, fdr=10, total_round_trips=10, adjustments_made=1)
        converging, reason = ct.is_converging()
        assert converging
        assert "insufficient" in reason

    def test_improving_win_rate(self):
        ct = ConvergenceTracker(lookback_cycles=3)
        ct.record_cycle(win_rate=40, net_pnl=50, fdr=20, total_round_trips=10, adjustments_made=1)
        ct.record_cycle(win_rate=42, net_pnl=50, fdr=20, total_round_trips=15, adjustments_made=1)
        ct.record_cycle(win_rate=45, net_pnl=50, fdr=20, total_round_trips=20, adjustments_made=1)
        ct.record_cycle(win_rate=48, net_pnl=50, fdr=20, total_round_trips=25, adjustments_made=1)
        converging, reason = ct.is_converging()
        assert converging
        assert "improving" in reason

    def test_not_converging(self):
        ct = ConvergenceTracker(lookback_cycles=3)
        ct.record_cycle(win_rate=40, net_pnl=50, fdr=20, total_round_trips=10, adjustments_made=2)
        ct.record_cycle(win_rate=38, net_pnl=45, fdr=22, total_round_trips=15, adjustments_made=2)
        ct.record_cycle(win_rate=37, net_pnl=42, fdr=23, total_round_trips=20, adjustments_made=2)
        ct.record_cycle(win_rate=36, net_pnl=40, fdr=25, total_round_trips=25, adjustments_made=2)
        converging, reason = ct.is_converging()
        assert not converging
        assert "not converging" in reason

    def test_stable_no_adjustments(self):
        ct = ConvergenceTracker(lookback_cycles=3)
        ct.record_cycle(win_rate=55, net_pnl=200, fdr=10, total_round_trips=10, adjustments_made=0)
        ct.record_cycle(win_rate=55, net_pnl=200, fdr=10, total_round_trips=15, adjustments_made=0)
        ct.record_cycle(win_rate=55, net_pnl=200, fdr=10, total_round_trips=20, adjustments_made=0)
        ct.record_cycle(win_rate=55, net_pnl=200, fdr=10, total_round_trips=25, adjustments_made=0)
        converging, reason = ct.is_converging()
        assert converging
        assert "stable" in reason

    def test_pnl_improvement_counts(self):
        ct = ConvergenceTracker(lookback_cycles=3)
        ct.record_cycle(win_rate=40, net_pnl=50, fdr=20, total_round_trips=10, adjustments_made=1)
        ct.record_cycle(win_rate=40, net_pnl=55, fdr=20, total_round_trips=15, adjustments_made=1)
        ct.record_cycle(win_rate=40, net_pnl=60, fdr=20, total_round_trips=20, adjustments_made=1)
        ct.record_cycle(win_rate=40, net_pnl=65, fdr=20, total_round_trips=25, adjustments_made=1)
        converging, _ = ct.is_converging()
        assert converging

    def test_fdr_improvement_counts(self):
        ct = ConvergenceTracker(lookback_cycles=3)
        ct.record_cycle(win_rate=40, net_pnl=50, fdr=25, total_round_trips=10, adjustments_made=1)
        ct.record_cycle(win_rate=40, net_pnl=50, fdr=22, total_round_trips=15, adjustments_made=1)
        ct.record_cycle(win_rate=40, net_pnl=50, fdr=20, total_round_trips=20, adjustments_made=1)
        ct.record_cycle(win_rate=40, net_pnl=50, fdr=18, total_round_trips=25, adjustments_made=1)
        converging, _ = ct.is_converging()
        assert converging


class TestDirectionalHysteresis:
    def test_first_adjustment_always_allowed(self):
        h = DirectionalHysteresis(required_consecutive=2)
        assert h.should_apply("radar_score_threshold", "up")

    def test_same_direction_allowed(self):
        h = DirectionalHysteresis(required_consecutive=2)
        h.should_apply("radar_score_threshold", "up")
        assert h.should_apply("radar_score_threshold", "up")

    def test_single_flip_blocked(self):
        h = DirectionalHysteresis(required_consecutive=2)
        h.should_apply("radar_score_threshold", "up")
        h.should_apply("radar_score_threshold", "up")
        # First flip — blocked (need 2 consecutive)
        assert not h.should_apply("radar_score_threshold", "down")

    def test_double_flip_allowed(self):
        h = DirectionalHysteresis(required_consecutive=2)
        h.should_apply("radar_score_threshold", "up")
        h.should_apply("radar_score_threshold", "up")
        h.should_apply("radar_score_threshold", "down")  # blocked
        assert h.should_apply("radar_score_threshold", "down")  # 2nd consecutive → allowed

    def test_independent_params(self):
        h = DirectionalHysteresis(required_consecutive=2)
        h.should_apply("radar_score_threshold", "up")
        h.should_apply("radar_score_threshold", "up")
        # Different param — independent, first adjustment
        assert h.should_apply("pulse_confidence_threshold", "down")

    def test_reset_specific_param(self):
        h = DirectionalHysteresis(required_consecutive=2)
        h.should_apply("radar_score_threshold", "up")
        h.reset("radar_score_threshold")
        # After reset, first adjustment is always allowed
        assert h.should_apply("radar_score_threshold", "down")

    def test_reset_all(self):
        h = DirectionalHysteresis(required_consecutive=2)
        h.should_apply("radar_score_threshold", "up")
        h.should_apply("pulse_confidence_threshold", "down")
        h.reset()
        assert h.should_apply("radar_score_threshold", "down")
        assert h.should_apply("pulse_confidence_threshold", "up")
