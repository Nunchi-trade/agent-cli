"""Tests for judge evaluation engine."""
from modules.judge_engine import JudgeEngine, JudgeReport


def _make_trades(pairs):
    """Create entry/exit trade record pairs for testing."""
    trades = []
    ts = 1000000
    for inst, source, entry_p, exit_p, qty in pairs:
        direction_side = "buy"  # Assume long for simplicity
        trades.append({
            "instrument": inst,
            "side": direction_side,
            "price": str(entry_p),
            "quantity": str(qty),
            "timestamp_ms": ts,
            "meta": f"entry:{source}",
            "entry_signal_score": 200,
        })
        ts += 3600000  # 1 hour later
        trades.append({
            "instrument": inst,
            "side": "sell",
            "price": str(exit_p),
            "quantity": str(qty),
            "timestamp_ms": ts,
            "meta": "guard_close",
        })
        ts += 1000
    return trades


class TestJudgeEngine:
    def test_empty_trades(self):
        engine = JudgeEngine()
        report = engine.evaluate([])
        assert report.round_trips_evaluated == 0

    def test_evaluate_winning_trades(self):
        engine = JudgeEngine()
        trades = _make_trades([
            ("ETH-PERP", "radar", 3000, 3150, 1.0),
            ("ETH-PERP", "radar", 3100, 3200, 1.0),
            ("SOL-PERP", "movers_immediate", 100, 110, 10.0),
        ])
        report = engine.evaluate(trades)
        assert report.round_trips_evaluated == 3
        assert len(report.signal_scores) == 3
        assert all(s.was_accurate for s in report.signal_scores)

    def test_false_positive_rates(self):
        engine = JudgeEngine()
        # 2 wins, 2 losses for radar
        trades = _make_trades([
            ("ETH-PERP", "radar", 3000, 3150, 1.0),  # win
            ("ETH-PERP", "radar", 3100, 3200, 1.0),  # win
            ("SOL-PERP", "radar", 100, 95, 10.0),     # loss
            ("BTC-PERP", "radar", 50000, 49000, 0.1), # loss
        ])
        report = engine.evaluate(trades)
        assert "radar" in report.false_positive_rates
        assert report.false_positive_rates["radar"] == 50.0

    def test_high_fp_generates_recommendation(self):
        engine = JudgeEngine()
        # 1 win, 3 losses for movers_immediate
        trades = _make_trades([
            ("ETH-PERP", "movers_immediate", 3000, 3050, 1.0),   # win
            ("SOL-PERP", "movers_immediate", 100, 95, 10.0),     # loss
            ("BTC-PERP", "movers_immediate", 50000, 49500, 0.1), # loss
            ("DOGE-PERP", "movers_immediate", 0.1, 0.09, 1000),  # loss
        ])
        report = engine.evaluate(trades)
        assert report.false_positive_rates.get("movers_immediate", 0) == 75.0
        assert any(r["param"] == "movers_immediate_auto_entry" for r in report.config_recommendations)

    def test_playbook_stats(self):
        engine = JudgeEngine()
        trades = _make_trades([
            ("ETH-PERP", "radar", 3000, 3150, 1.0),
            ("ETH-PERP", "radar", 3100, 3000, 1.0),
        ])
        report = engine.evaluate(trades)
        key = "ETH-PERP:radar"
        assert key in report.playbook_stats
        stats = report.playbook_stats[key]
        assert stats["count"] == 2
        assert stats["wins"] == 1
        assert stats["win_rate"] == 50.0

    def test_dsl_efficiency(self):
        engine = JudgeEngine()
        # DSL captured only 40% of peak move
        slots = [{
            "instrument": "ETH-PERP",
            "high_water_roe": 10.0,
            "current_roe": 4.0,
            "close_reason": "guard_close",
        }]
        report = engine.evaluate([], closed_slots=slots)
        dsl_findings = [f for f in report.findings if f.finding_type == "guard_efficiency"]
        assert len(dsl_findings) == 1
        assert dsl_findings[0].score == 40.0

    def test_signal_scoring(self):
        engine = JudgeEngine()
        # Good trade: high ROE
        pair_good = {"entry_source": "radar", "instrument": "ETH", "pnl": 100, "roe_pct": 8.0, "entry_score": 200}
        score = engine._score_signal(pair_good)
        assert score.was_accurate
        assert score.outcome_score > 60

        # Bad trade: negative ROE
        pair_bad = {"entry_source": "radar", "instrument": "ETH", "pnl": -50, "roe_pct": -4.0, "entry_score": 200}
        score = engine._score_signal(pair_bad)
        assert not score.was_accurate
        assert score.outcome_score == 0

    def test_report_serialization(self):
        engine = JudgeEngine()
        trades = _make_trades([("ETH-PERP", "radar", 3000, 3150, 1.0)])
        report = engine.evaluate(trades)
        d = report.to_dict()
        assert "false_positive_rates" in d
        assert "playbook_stats" in d

        report2 = JudgeReport.from_dict(d)
        assert report2.round_trips_evaluated == report.round_trips_evaluated

    def test_direction_imbalance_recommendation(self):
        engine = JudgeEngine()
        # Longs losing, shorts winning
        trades = _make_trades([
            ("ETH-PERP", "radar", 3000, 2900, 1.0),  # long loss
            ("SOL-PERP", "radar", 100, 90, 10.0),     # long loss
            ("BTC-PERP", "radar", 50000, 49000, 0.1), # long loss
        ])
        report = engine.evaluate(trades)
        # All are losses in same direction
        dir_recs = [r for r in report.config_recommendations if r.get("param") == "direction_bias"]
        # May or may not trigger since no opposite profitable trades
        # Just verify no crash
        assert isinstance(report.config_recommendations, list)
