"""Tests for cli/engine.py — TradingEngine tick loop."""
import pytest
import tempfile
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

from common.models import MarketSnapshot, StrategyDecision
from parent.hl_proxy import HLFill
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext
from cli.engine import TradingEngine, TICK_TIMEOUT_S, MAX_CONSECUTIVE_TIMEOUTS


class StubStrategy(BaseStrategy):
    """Strategy that returns configurable decisions."""

    def __init__(self, decisions=None):
        super().__init__(strategy_id="test_stub")
        self._decisions = decisions or []

    def on_tick(self, snapshot, context=None):
        return list(self._decisions)


class MockHL:
    """Minimal mock of DirectHLProxy / DirectMockProxy for engine tests."""

    def __init__(self, mid=2500.0):
        self._mid = mid
        self._fill_on_order = True

    def get_snapshot(self, instrument="ETH-PERP"):
        return MarketSnapshot(
            instrument=instrument,
            mid_price=self._mid,
            bid=self._mid - 0.5,
            ask=self._mid + 0.5,
            spread_bps=4.0,
            timestamp_ms=int(time.time() * 1000),
        )

    def place_order(self, instrument, side, size, price, tif="Ioc", builder=None):
        if not self._fill_on_order:
            return None
        return HLFill(
            oid=f"mock-{int(time.time()*1000)}",
            instrument=instrument,
            side=side.lower(),
            price=Decimal(str(price)),
            quantity=Decimal(str(size)),
            timestamp_ms=int(time.time() * 1000),
        )

    def cancel_order(self, instrument, oid):
        return True

    def get_open_orders(self, instrument=""):
        return []

    def get_account_state(self):
        return {
            "account_value": 10000.0,
            "total_margin": 0.0,
            "withdrawable": 10000.0,
            "marginSummary": {"accountValue": "10000"},
        }

    def set_leverage(self, leverage, coin="ETH", is_cross=True):
        pass


def _make_engine(strategy=None, tmp_dir=None, **kwargs):
    hl = MockHL()
    strat = strategy or StubStrategy()
    data_dir = tmp_dir or tempfile.mkdtemp()
    return TradingEngine(
        hl=hl,
        strategy=strat,
        instrument="ETH-PERP",
        tick_interval=0,
        dry_run=True,
        data_dir=data_dir,
        **kwargs,
    )


class TestTickCycle:
    def test_single_tick_increments_count(self):
        engine = _make_engine()
        engine._tick()
        assert engine.tick_count == 1

    def test_float_fill_from_venue_is_coerced_for_position_tracker(self):
        engine = _make_engine(strategy=StubStrategy())
        engine.dry_run = False
        engine.order_manager.update = MagicMock(return_value=[
            type("Fill", (), {
                "oid": "fill-1",
                "instrument": "ETH-PERP",
                "side": "BUY",
                "price": 2500.5,
                "quantity": 1.25,
                "timestamp_ms": 123,
                "fee": 0.0,
            })()
        ])
        engine._tick()
        pos = engine.position_tracker.get_agent_position("test_stub", "ETH-PERP")
        assert pos.net_qty == Decimal("1.25")
        assert pos.avg_entry_price == Decimal("2500.5")

    def test_noop_strategy_no_fills(self):
        engine = _make_engine(strategy=StubStrategy(decisions=[]))
        engine._tick()
        assert engine.tick_count == 1
        # No fills logged
        records = engine.trade_log.read_all()
        assert len(records) == 0

    def test_place_order_decision_fills(self):
        decisions = [
            StrategyDecision(
                action="place_order",
                side="buy",
                size=1.0,
                limit_price=2500.0,
            )
        ]
        hl = MockHL()
        strat = StubStrategy(decisions=decisions)
        tmp = tempfile.mkdtemp()
        engine = TradingEngine(
            hl=hl, strategy=strat, instrument="ETH-PERP",
            tick_interval=0, dry_run=False, data_dir=tmp,
        )
        engine._tick()
        records = engine.trade_log.read_all()
        assert len(records) == 1
        assert records[0]["side"] == "buy"

    def test_run_respects_max_ticks(self):
        engine = _make_engine()
        engine.run(max_ticks=3, resume=False)
        assert engine.tick_count == 3


class TestRiskBlock:
    def test_risk_block_skips_execution(self):
        decisions = [
            StrategyDecision(
                action="place_order",
                side="buy",
                size=1.0,
                limit_price=2500.0,
            )
        ]
        engine = _make_engine(strategy=StubStrategy(decisions=decisions))
        # Force risk block
        engine.risk_manager.state.safe_mode = True
        engine._tick()
        # No fills should occur
        records = engine.trade_log.read_all()
        assert len(records) == 0


class TestStatePersistence:
    def test_state_persists_and_restores(self):
        tmp = tempfile.mkdtemp()
        engine1 = _make_engine(tmp_dir=tmp)
        engine1.run(max_ticks=5, resume=False)
        assert engine1.tick_count == 5

        # Create new engine from same data dir
        engine2 = _make_engine(tmp_dir=tmp)
        engine2._restore_state()
        assert engine2.tick_count == 5

    def test_resume_runs_additional_ticks_instead_of_stopping_immediately(self):
        tmp = tempfile.mkdtemp()
        engine1 = _make_engine(tmp_dir=tmp)
        engine1.run(max_ticks=5, resume=False)
        assert engine1.tick_count == 5

        engine2 = _make_engine(tmp_dir=tmp)
        engine2.run(max_ticks=3, resume=True)
        assert engine2.tick_count == 8

    def test_strategy_mismatch_starts_fresh(self):
        tmp = tempfile.mkdtemp()
        engine1 = _make_engine(
            strategy=StubStrategy(decisions=[]),
            tmp_dir=tmp,
        )
        engine1.strategy.strategy_id = "strategy_A"
        engine1.run(max_ticks=3, resume=False)
        assert engine1.tick_count == 3

        # Different strategy ID
        engine2 = _make_engine(
            strategy=StubStrategy(decisions=[]),
            tmp_dir=tmp,
        )
        engine2.strategy.strategy_id = "strategy_B"
        engine2._restore_state()
        # Should NOT restore (strategy mismatch → fresh start)
        assert engine2.tick_count == 0


class TestZeroMarketData:
    def test_zero_mid_price_skips_tick(self):
        engine = _make_engine()
        engine.hl._mid = 0.0  # simulate no data
        engine._tick()
        assert engine.tick_count == 1
        # No fills
        records = engine.trade_log.read_all()
        assert len(records) == 0


class TestPreflightCheck:
    def test_preflight_logs_warning_on_zero_balance(self):
        engine = _make_engine()
        engine.dry_run = False
        engine.hl.get_account_state = lambda: {
            "marginSummary": {"accountValue": "0"},
        }
        # Should not raise — just logs a warning
        engine._preflight_check()

    def test_preflight_counts_non_hl_balances(self):
        engine = _make_engine()
        engine.dry_run = False
        engine.hl.get_account_state = lambda: {
            "venue": "paradex",
            "balances": [{"token": "USDC", "size": "487.48"}],
        }
        assert engine._estimate_account_balance(engine.hl.get_account_state()) == pytest.approx(487.48)
        engine._preflight_check()

    def test_preflight_handles_failure(self):
        engine = _make_engine()
        engine.dry_run = False
        engine.hl.get_account_state = MagicMock(side_effect=Exception("network"))
        # Should not raise
        engine._preflight_check()


class TestTickTimeout:
    def test_timeout_constants_exist(self):
        assert TICK_TIMEOUT_S == 30
        assert MAX_CONSECUTIVE_TIMEOUTS == 3

    def test_engine_has_timeout_state(self):
        engine = _make_engine()
        assert engine._consecutive_timeouts == 0
        assert engine._tick_executor is not None


class TestShutdownClose:
    def test_shutdown_closes_position(self):
        decisions = [
            StrategyDecision(
                action="place_order",
                side="buy",
                size=1.0,
                limit_price=2500.0,
            )
        ]
        hl = MockHL()
        strat = StubStrategy(decisions=decisions)
        tmp = tempfile.mkdtemp()
        engine = TradingEngine(
            hl=hl, strategy=strat, instrument="ETH-PERP",
            tick_interval=0, dry_run=False, data_dir=tmp,
        )
        engine._tick()  # open a position

        # Verify position exists
        pos = engine.position_tracker.get_agent_position("test_stub", "ETH-PERP")
        assert pos.net_qty != Decimal("0")

        # Shutdown close
        engine._close_all_positions()
        pos = engine.position_tracker.get_agent_position("test_stub", "ETH-PERP")
        # After close, net position should be zero
        assert pos.net_qty == Decimal("0")
