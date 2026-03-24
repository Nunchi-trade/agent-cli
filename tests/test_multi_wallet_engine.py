"""Tests for MultiWalletEngine and HouseRiskManager integration."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

# Ensure project root is on path
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from cli.multi_wallet_engine import MultiWalletEngine, WalletEngineContext
from common.models import MarketSnapshot, StrategyDecision
from common.venue_adapter import Fill, VenueAdapter, VenueCapabilities
from modules.wallet_manager import WalletConfig, WalletManager
from parent.house_risk import HouseRiskManager, HouseRiskState
from parent.risk_manager import RiskLimits, RiskManager, RiskState
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


# ─── Fixtures ─────────────────────────────────────────────────────────


class StubAdapter(VenueAdapter):
    """Minimal VenueAdapter for testing — returns deterministic data."""

    def __init__(self, mid_price: float = 3000.0):
        self._mid = mid_price

    def connect(self, private_key: str, testnet: bool = True) -> None:
        pass

    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities()

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        return MarketSnapshot(
            instrument=instrument,
            mid_price=self._mid,
            bid=self._mid * 0.999,
            ask=self._mid * 1.001,
            timestamp_ms=1000000,
        )

    def get_candles(self, coin, interval, lookback_ms):
        return []

    def get_all_markets(self):
        return []

    def get_all_mids(self):
        return {self._mid: str(self._mid)}

    def place_order(self, instrument, side, size, price, tif="Ioc", builder=None):
        return Fill(
            oid="test-oid",
            instrument=instrument,
            side=side,
            price=price,
            quantity=size,
            timestamp_ms=1000000,
            fee=0.0,
        )

    def cancel_order(self, instrument, oid):
        return True

    def get_open_orders(self, instrument=""):
        return []

    def get_account_state(self):
        return {"crossMarginSummary": {"accountValue": "50000"}}

    def set_leverage(self, leverage, coin, is_cross=True):
        pass


class StubStrategy(BaseStrategy):
    """Strategy that does nothing — just returns empty decisions."""

    def __init__(self, strategy_id: str = "stub"):
        super().__init__(strategy_id=strategy_id)

    def on_tick(self, snapshot, context=None) -> List[StrategyDecision]:
        return []


def _make_wallet_manager(n: int = 2) -> WalletManager:
    """Create a WalletManager with N wallets."""
    wallets = {}
    for i in range(n):
        wid = f"wallet_{i}"
        wallets[wid] = WalletConfig(
            wallet_id=wid,
            address=f"0x{i:040x}",
            budget=5000.0 * (i + 1),
            leverage=10.0,
            guard_preset="tight",
            max_slots=2,
            daily_loss_limit=250.0 * (i + 1),
        )
    return WalletManager(wallets=wallets)


def _adapter_factory(wc: WalletConfig) -> VenueAdapter:
    return StubAdapter(mid_price=3000.0)


def _strategy_factory(wc: WalletConfig) -> BaseStrategy:
    return StubStrategy(strategy_id=f"test_{wc.wallet_id}")


# ─── Tests: WalletConfig.to_risk_limits ───────────────────────────────


class TestWalletConfigRiskLimits:
    def test_to_risk_limits_basic(self):
        wc = WalletConfig(budget=10_000.0, leverage=10.0)
        limits = wc.to_risk_limits()
        assert limits.max_notional_usd == Decimal("10000.0")
        assert limits.max_leverage == Decimal("10.0")
        assert limits.tvl == Decimal("10000.0")

    def test_to_risk_limits_position_qty_scales_with_leverage(self):
        wc = WalletConfig(budget=5000.0, leverage=5.0)
        limits = wc.to_risk_limits()
        # max_position_qty = budget / leverage = 1000
        assert limits.max_position_qty == Decimal("1000.0")

    def test_to_risk_limits_returns_risk_limits_type(self):
        wc = WalletConfig()
        limits = wc.to_risk_limits()
        assert isinstance(limits, RiskLimits)


# ─── Tests: HouseRiskManager ─────────────────────────────────────────


class TestHouseRiskManager:
    def test_init_defaults(self):
        hrm = HouseRiskManager()
        assert not hrm.should_halt_all()
        assert hrm.state.total_daily_pnl == Decimal("0")

    def test_update_aggregates_pnl(self):
        hrm = HouseRiskManager(max_house_drawdown=1000.0)
        states = {
            "w1": RiskState(daily_pnl=Decimal("100"), daily_drawdown=Decimal("50")),
            "w2": RiskState(daily_pnl=Decimal("-200"), daily_drawdown=Decimal("300")),
        }
        hrm.update(states)
        assert hrm.state.total_daily_pnl == Decimal("-100")
        assert hrm.state.total_daily_drawdown == Decimal("350")
        assert not hrm.should_halt_all()

    def test_halt_on_drawdown_breach(self):
        hrm = HouseRiskManager(max_house_drawdown=500.0)
        states = {
            "w1": RiskState(daily_drawdown=Decimal("300")),
            "w2": RiskState(daily_drawdown=Decimal("250")),
        }
        hrm.update(states)
        assert hrm.should_halt_all()
        assert "house_drawdown" in hrm.state.halt_reason

    def test_halt_on_exposure_breach(self):
        hrm = HouseRiskManager(max_house_exposure=10_000.0)
        hrm.update_exposure({"w1": Decimal("6000"), "w2": Decimal("5000")})
        assert hrm.should_halt_all()
        assert "house_exposure" in hrm.state.halt_reason

    def test_clear_halt(self):
        hrm = HouseRiskManager(max_house_drawdown=100.0)
        hrm.state.halt_triggered = True
        hrm.state.halt_reason = "test"
        hrm.clear_halt()
        assert not hrm.should_halt_all()

    def test_summary_includes_limits(self):
        hrm = HouseRiskManager(max_house_drawdown=999.0, max_house_exposure=50000.0)
        s = hrm.summary()
        assert s["max_house_drawdown"] == "999.0"
        assert s["max_house_exposure"] == "50000.0"
        assert "halt_triggered" in s


# ─── Tests: MultiWalletEngine ────────────────────────────────────────


class TestMultiWalletEngineInit:
    def test_creates_engines_per_wallet(self, tmp_path):
        wm = _make_wallet_manager(3)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            data_dir=str(tmp_path),
        )
        assert len(mwe.wallet_ids) == 3
        assert set(mwe.wallet_ids) == {"wallet_0", "wallet_1", "wallet_2"}

    def test_per_wallet_risk_limits(self, tmp_path):
        wm = _make_wallet_manager(2)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            data_dir=str(tmp_path),
        )
        # wallet_0 has budget=5000, wallet_1 has budget=10000
        eng0 = mwe.get_engine("wallet_0")
        eng1 = mwe.get_engine("wallet_1")
        assert eng0 is not None
        assert eng1 is not None
        assert eng0.risk_manager.limits.max_notional_usd == Decimal("5000.0")
        assert eng1.risk_manager.limits.max_notional_usd == Decimal("10000.0")

    def test_per_wallet_strategy_ids_are_unique(self, tmp_path):
        wm = _make_wallet_manager(2)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            data_dir=str(tmp_path),
        )
        ids = set()
        for wid in mwe.wallet_ids:
            eng = mwe.get_engine(wid)
            ids.add(eng.strategy.strategy_id)
        assert len(ids) == 2  # unique per wallet


class TestMultiWalletEngineRun:
    def test_ticks_all_engines(self, tmp_path):
        wm = _make_wallet_manager(2)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            tick_interval=0,
            data_dir=str(tmp_path),
            dry_run=True,
        )
        mwe.run(max_ticks=3, resume=False)
        assert mwe.tick_count == 3
        # Each per-wallet engine should also have been ticked 3 times
        for wid in mwe.wallet_ids:
            eng = mwe.get_engine(wid)
            assert eng.tick_count == 3

    def test_house_risk_updated_after_ticks(self, tmp_path):
        wm = _make_wallet_manager(2)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            tick_interval=0,
            data_dir=str(tmp_path),
            dry_run=True,
        )
        mwe.run(max_ticks=1, resume=False)
        summary = mwe.house_risk_summary()
        assert "wallets" in summary
        assert len(summary["wallets"]) == 2

    def test_house_halt_stops_run(self, tmp_path):
        wm = _make_wallet_manager(2)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            tick_interval=0,
            data_dir=str(tmp_path),
            dry_run=True,
            max_house_drawdown=0.0001,  # Extremely tight — will halt immediately
        )
        # Pre-seed drawdown in per-wallet risk states so house aggregation
        # sees drawdown > limit after the first tick cycle
        for ctx in mwe._contexts.values():
            ctx.engine.risk_manager.state.daily_drawdown = Decimal("1")
            ctx.engine.risk_manager.state.daily_high_water = Decimal("1")

        # Directly trigger house risk update to simulate post-tick aggregation
        mwe._update_house_risk()
        assert mwe.house_risk.should_halt_all()

        mwe.run(max_ticks=100, resume=False)
        # Should have stopped immediately since halt was already triggered
        assert mwe.tick_count == 0


class TestMultiWalletEngineIsolation:
    def test_position_isolation(self, tmp_path):
        """One wallet's fill should not appear in another wallet's tracker."""
        wm = _make_wallet_manager(2)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            tick_interval=0,
            data_dir=str(tmp_path),
            dry_run=True,
        )

        # Manually apply a fill to wallet_0's engine only
        eng0 = mwe.get_engine("wallet_0")
        eng1 = mwe.get_engine("wallet_1")
        eng0.position_tracker.apply_fill(
            eng0.strategy.strategy_id, "ETH-PERP", "buy",
            Decimal("1.0"), Decimal("3000"),
        )

        # wallet_0 should have a position
        pos0 = eng0.position_tracker.get_agent_position(
            eng0.strategy.strategy_id, "ETH-PERP"
        )
        assert pos0.net_qty == Decimal("1.0")

        # wallet_1 should be flat
        pos1 = eng1.position_tracker.get_agent_position(
            eng1.strategy.strategy_id, "ETH-PERP"
        )
        assert pos1.net_qty == Decimal("0")

    def test_risk_manager_isolation(self, tmp_path):
        """Each wallet has its own RiskManager instance."""
        wm = _make_wallet_manager(2)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            data_dir=str(tmp_path),
        )
        eng0 = mwe.get_engine("wallet_0")
        eng1 = mwe.get_engine("wallet_1")
        assert eng0.risk_manager is not eng1.risk_manager
        assert eng0.position_tracker is not eng1.position_tracker


class TestSingleWalletBackwardCompat:
    def test_single_wallet_engine_works(self, tmp_path):
        """Single-wallet WalletManager should produce 1 engine."""
        wm = WalletManager.from_single(budget=10_000.0, leverage=10.0)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            tick_interval=0,
            data_dir=str(tmp_path),
            dry_run=True,
        )
        assert len(mwe.wallet_ids) == 1
        assert mwe.wallet_ids[0] == "default"

        mwe.run(max_ticks=2, resume=False)
        assert mwe.tick_count == 2


class TestHouseRiskSummary:
    def test_summary_has_per_wallet_details(self, tmp_path):
        wm = _make_wallet_manager(2)
        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            tick_interval=0,
            data_dir=str(tmp_path),
            dry_run=True,
        )
        mwe.run(max_ticks=1, resume=False)
        summary = mwe.house_risk_summary()

        assert "wallets" in summary
        assert "wallet_0" in summary["wallets"]
        assert "wallet_1" in summary["wallets"]
        for wid in ("wallet_0", "wallet_1"):
            ws = summary["wallets"][wid]
            assert "daily_pnl" in ws
            assert "risk_gate" in ws
            assert "positions" in ws
            assert "tick_count" in ws
        assert summary["total_ticks"] == 1
