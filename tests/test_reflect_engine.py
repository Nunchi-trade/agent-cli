"""Tests for REFLECT engine — metrics, round trip pairing, recommendations, reporter."""
import os
import sys

import pytest

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from modules.reflect_engine import ReflectEngine, ReflectMetrics, RoundTrip, TradeRecord


def _trade(side="buy", price=100.0, qty=1.0, ts=1000, fee=0.5,
           instrument="ETH-PERP", strategy="test", meta=""):
    return TradeRecord(
        tick=1, oid=f"t-{ts}", instrument=instrument, side=side,
        price=price, quantity=qty, timestamp_ms=ts, fee=fee,
        strategy=strategy, meta=meta,
    )


# ---------------------------------------------------------------------------
# TradeRecord
# ---------------------------------------------------------------------------

class TestTradeRecord:
    def test_from_dict(self):
        d = {
            "tick": 5, "oid": "abc", "instrument": "ETH-PERP",
            "side": "buy", "price": "2500.5", "quantity": "1.5",
            "timestamp_ms": 12345, "fee": "0.25", "strategy": "mm",
        }
        tr = TradeRecord.from_dict(d)
        assert tr.price == 2500.5
        assert tr.quantity == 1.5
        assert tr.fee == 0.25
        assert tr.strategy == "mm"

    def test_from_dict_empty(self):
        tr = TradeRecord.from_dict({})
        assert tr.price == 0.0
        assert tr.side == ""


# ---------------------------------------------------------------------------
# RoundTrip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_long_winner(self):
        rt = RoundTrip(
            direction="long", entry_price=100, exit_price=110,
            quantity=1.0, entry_fee=0.5, exit_fee=0.5,
        )
        assert rt.gross_pnl == 10.0
        assert rt.total_fees == 1.0
        assert rt.net_pnl == 9.0
        assert rt.is_winner

    def test_long_loser(self):
        rt = RoundTrip(
            direction="long", entry_price=100, exit_price=90,
            quantity=1.0, entry_fee=0.5, exit_fee=0.5,
        )
        assert rt.gross_pnl == -10.0
        assert rt.net_pnl == -11.0
        assert not rt.is_winner

    def test_short_winner(self):
        rt = RoundTrip(
            direction="short", entry_price=100, exit_price=90,
            quantity=1.0, entry_fee=0.5, exit_fee=0.5,
        )
        assert rt.gross_pnl == 10.0
        assert rt.net_pnl == 9.0
        assert rt.is_winner

    def test_short_loser(self):
        rt = RoundTrip(
            direction="short", entry_price=100, exit_price=110,
            quantity=1.0, entry_fee=0.5, exit_fee=0.5,
        )
        assert rt.gross_pnl == -10.0
        assert not rt.is_winner

    def test_holding_ms(self):
        rt = RoundTrip(entry_ts=1000, exit_ts=61000)
        assert rt.holding_ms == 60000


# ---------------------------------------------------------------------------
# FIFO Round Trip Pairing
# ---------------------------------------------------------------------------

class TestRoundTripPairing:
    def test_single_long_round_trip(self):
        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000),
            _trade(side="sell", price=110, qty=1.0, ts=2000),
        ]
        engine = ReflectEngine()
        rts = engine._pair_round_trips(trades)
        assert len(rts) == 1
        assert rts[0].direction == "long"
        assert rts[0].entry_price == 100
        assert rts[0].exit_price == 110

    def test_single_short_round_trip(self):
        trades = [
            _trade(side="sell", price=110, qty=1.0, ts=1000),
            _trade(side="buy", price=100, qty=1.0, ts=2000),
        ]
        engine = ReflectEngine()
        rts = engine._pair_round_trips(trades)
        assert len(rts) == 1
        assert rts[0].direction == "short"
        assert rts[0].entry_price == 110
        assert rts[0].exit_price == 100

    def test_partial_fill_split(self):
        trades = [
            _trade(side="buy", price=100, qty=2.0, ts=1000),
            _trade(side="sell", price=110, qty=1.0, ts=2000),
            _trade(side="sell", price=115, qty=1.0, ts=3000),
        ]
        engine = ReflectEngine()
        rts = engine._pair_round_trips(trades)
        assert len(rts) == 2
        assert rts[0].quantity == 1.0
        assert rts[0].exit_price == 110
        assert rts[1].quantity == 1.0
        assert rts[1].exit_price == 115

    def test_multi_instrument(self):
        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000, instrument="ETH-PERP"),
            _trade(side="buy", price=50, qty=1.0, ts=1001, instrument="SOL-PERP"),
            _trade(side="sell", price=110, qty=1.0, ts=2000, instrument="ETH-PERP"),
            _trade(side="sell", price=55, qty=1.0, ts=2001, instrument="SOL-PERP"),
        ]
        engine = ReflectEngine()
        rts = engine._pair_round_trips(trades)
        assert len(rts) == 2
        instruments = {r.instrument for r in rts}
        assert instruments == {"ETH-PERP", "SOL-PERP"}

    def test_unmatched_trades_no_crash(self):
        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000),
        ]
        engine = ReflectEngine()
        rts = engine._pair_round_trips(trades)
        assert len(rts) == 0


# ---------------------------------------------------------------------------
# Full ReflectEngine.compute()
# ---------------------------------------------------------------------------

class TestReflectCompute:
    def test_empty_trades(self):
        engine = ReflectEngine()
        m = engine.compute([])
        assert m.total_trades == 0
        assert m.total_round_trips == 0

    def test_basic_metrics(self):
        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=0.5),
            _trade(side="sell", price=110, qty=1.0, ts=2000, fee=0.5),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        assert m.total_trades == 2
        assert m.total_round_trips == 1
        assert m.win_count == 1
        assert m.win_rate == 100.0
        assert m.gross_pnl == 10.0
        assert m.total_fees == 1.0
        assert m.net_pnl == 9.0

    def test_fdr_computation(self):
        # Gross win = 10, fees = 5 → FDR = 50%
        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=2.5),
            _trade(side="sell", price=110, qty=1.0, ts=2000, fee=2.5),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        assert m.fdr == 50.0

    def test_direction_analysis(self):
        trades = [
            # Long trade
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=0),
            _trade(side="sell", price=110, qty=1.0, ts=2000, fee=0),
            # Short trade
            _trade(side="sell", price=200, qty=1.0, ts=3000, fee=0),
            _trade(side="buy", price=190, qty=1.0, ts=4000, fee=0),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        assert m.long_count == 1
        assert m.short_count == 1
        assert m.long_pnl == 10.0
        assert m.short_pnl == 10.0

    def test_holding_buckets(self):
        # 30s holding → <5m bucket
        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=0),
            _trade(side="sell", price=110, qty=1.0, ts=31000, fee=0),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        assert m.holding_buckets.get("<5m", 0) == 1

    def test_consecutive_streaks(self):
        trades = [
            # Win
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=0),
            _trade(side="sell", price=110, qty=1.0, ts=2000, fee=0),
            # Win
            _trade(side="buy", price=100, qty=1.0, ts=3000, fee=0),
            _trade(side="sell", price=105, qty=1.0, ts=4000, fee=0),
            # Loss
            _trade(side="buy", price=100, qty=1.0, ts=5000, fee=0),
            _trade(side="sell", price=90, qty=1.0, ts=6000, fee=0),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        assert m.max_consecutive_wins == 2
        assert m.max_consecutive_losses == 1

    def test_monster_dependency(self):
        trades = [
            # Big win: +50
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=0),
            _trade(side="sell", price=150, qty=1.0, ts=2000, fee=0),
            # Small loss: -5
            _trade(side="buy", price=100, qty=1.0, ts=3000, fee=0),
            _trade(side="sell", price=95, qty=1.0, ts=4000, fee=0),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        # net_pnl = 45, best = 50, dependency = 50/45*100 = ~111%
        assert m.monster_dependency_pct > 100

    def test_strategy_breakdown(self):
        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=0, strategy="alpha"),
            _trade(side="sell", price=110, qty=1.0, ts=2000, fee=0, strategy="alpha"),
            _trade(side="buy", price=100, qty=1.0, ts=3000, fee=0, strategy="beta"),
            _trade(side="sell", price=90, qty=1.0, ts=4000, fee=0, strategy="beta"),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        assert "alpha" in m.strategy_stats
        assert "beta" in m.strategy_stats
        assert m.strategy_stats["alpha"]["win_rate"] == 100.0
        assert m.strategy_stats["beta"]["win_rate"] == 0.0


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    def test_high_fdr_warning(self):
        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=5),
            _trade(side="sell", price=110, qty=1.0, ts=2000, fee=5),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        # FDR = 10/10*100 = 100%
        assert any("FDR" in r for r in m.recommendations)

    def test_low_win_rate(self):
        # 5 round trips, all losses
        trades = []
        for i in range(5):
            ts = i * 2000
            trades.append(_trade(side="buy", price=100, qty=1.0, ts=ts, fee=0))
            trades.append(_trade(side="sell", price=90, qty=1.0, ts=ts + 1000, fee=0))
        engine = ReflectEngine()
        m = engine.compute(trades)
        assert any("Win rate" in r for r in m.recommendations)

    def test_no_issues(self):
        # 5 profitable trades, low fees
        trades = []
        for i in range(5):
            ts = i * 2000
            trades.append(_trade(side="buy", price=100, qty=1.0, ts=ts, fee=0.01))
            trades.append(_trade(side="sell", price=110, qty=1.0, ts=ts + 1000, fee=0.01))
        engine = ReflectEngine()
        m = engine.compute(trades)
        assert any("No major issues" in r for r in m.recommendations)


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

class TestReflectReporter:
    def test_generate_report(self):
        from modules.reflect_reporter import ReflectReporter

        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=0.5),
            _trade(side="sell", price=110, qty=1.0, ts=2000, fee=0.5),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        reporter = ReflectReporter()
        report = reporter.generate(m, date="2026-03-03")
        assert "# REFLECT Report — 2026-03-03" in report
        assert "Net PnL" in report
        assert "Fee Analysis" in report
        assert "Recommendations" in report

    def test_distill_summary(self):
        from modules.reflect_reporter import ReflectReporter

        trades = [
            _trade(side="buy", price=100, qty=1.0, ts=1000, fee=0.5),
            _trade(side="sell", price=110, qty=1.0, ts=2000, fee=0.5),
        ]
        engine = ReflectEngine()
        m = engine.compute(trades)
        reporter = ReflectReporter()
        summary = reporter.distill(m)
        assert "REFLECT:" in summary
        assert "100%" in summary  # win rate

    def test_generate_empty(self):
        from modules.reflect_reporter import ReflectReporter

        m = ReflectMetrics()
        reporter = ReflectReporter()
        report = reporter.generate(m)
        assert "REFLECT Report" in report
