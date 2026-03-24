"""MultiWalletEngine — orchestrates N wallet-isolated TradingEngine instances.

Single-wallet mode (default): delegates to a plain TradingEngine.
Multi-wallet mode: creates one TradingEngine per WalletConfig, each with its
own VenueAdapter, PositionTracker, RiskManager, and strategy instance.
House-level risk is aggregated after each tick cycle.
"""
from __future__ import annotations

import logging
import signal
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from cli.engine import TradingEngine
from common.venue_adapter import VenueAdapter
from modules.wallet_manager import WalletConfig, WalletManager
from parent.house_risk import HouseRiskManager
from parent.risk_manager import RiskLimits, RiskManager
from sdk.strategy_sdk.base import BaseStrategy

log = logging.getLogger("multi_wallet_engine")


class WalletEngineContext:
    """Bundle of per-wallet runtime objects for introspection."""

    def __init__(
        self,
        wallet_id: str,
        config: WalletConfig,
        adapter: VenueAdapter,
        strategy: BaseStrategy,
        engine: TradingEngine,
    ):
        self.wallet_id = wallet_id
        self.config = config
        self.adapter = adapter
        self.strategy = strategy
        self.engine = engine


class MultiWalletEngine:
    """Orchestrates multiple TradingEngine instances, one per wallet.

    Constructor params:
        wallet_manager:   WalletManager with 1..N wallet configs.
        adapter_factory:  Callable[[WalletConfig], VenueAdapter] — creates
                          a venue adapter per wallet.
        strategy_factory: Callable[[WalletConfig], BaseStrategy] — creates
                          a strategy instance per wallet.
        instrument:       Default instrument for all engines.
        tick_interval:    Seconds between tick cycles.
        dry_run:          If True, no real orders.
        data_dir:         Base data directory (sub-dirs created per wallet).
        builder:          Optional builder fee info.
        max_house_drawdown:  House-level drawdown limit ($).
        max_house_exposure:  House-level max notional exposure ($).
    """

    def __init__(
        self,
        wallet_manager: WalletManager,
        adapter_factory: Callable[[WalletConfig], VenueAdapter],
        strategy_factory: Callable[[WalletConfig], BaseStrategy],
        instrument: str = "ETH-PERP",
        tick_interval: float = 10.0,
        dry_run: bool = False,
        data_dir: str = "data/multi",
        builder: Optional[dict] = None,
        max_house_drawdown: float = 2000.0,
        max_house_exposure: float = 100_000.0,
    ):
        self.wallet_manager = wallet_manager
        self.tick_interval = tick_interval
        self.dry_run = dry_run
        self.instrument = instrument
        self._running = False
        self.tick_count = 0

        # House-level risk
        self.house_risk = HouseRiskManager(
            max_house_drawdown=max_house_drawdown,
            max_house_exposure=max_house_exposure,
        )

        # Build per-wallet engine contexts
        self._contexts: Dict[str, WalletEngineContext] = {}
        for wid in wallet_manager.wallet_ids:
            wc = wallet_manager.get(wid)
            if wc is None:
                continue

            adapter = adapter_factory(wc)
            strategy = strategy_factory(wc)
            risk_limits = wc.to_risk_limits()
            wallet_data_dir = f"{data_dir}/{wid}"

            engine = TradingEngine(
                hl=adapter,
                strategy=strategy,
                instrument=instrument,
                tick_interval=0,  # we control timing externally
                dry_run=dry_run,
                data_dir=wallet_data_dir,
                risk_limits=risk_limits,
                builder=builder,
            )

            self._contexts[wid] = WalletEngineContext(
                wallet_id=wid,
                config=wc,
                adapter=adapter,
                strategy=strategy,
                engine=engine,
            )

        log.info(
            "MultiWalletEngine initialized: %d wallet(s), instrument=%s, "
            "house_drawdown=$%.0f, house_exposure=$%.0f",
            len(self._contexts),
            instrument,
            max_house_drawdown,
            max_house_exposure,
        )

    @property
    def wallet_ids(self) -> List[str]:
        return list(self._contexts.keys())

    def get_engine(self, wallet_id: str) -> Optional[TradingEngine]:
        """Get a per-wallet TradingEngine for inspection."""
        ctx = self._contexts.get(wallet_id)
        return ctx.engine if ctx else None

    def run(self, max_ticks: int = 0, resume: bool = True) -> None:
        """Main loop.  Ticks all wallet engines in sequence, then aggregates
        house risk.  Blocks until max_ticks reached or signal received."""
        self._running = True

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        # Restore state per wallet
        if resume:
            for ctx in self._contexts.values():
                ctx.engine._restore_state()

        # Set leverage per wallet
        for ctx in self._contexts.values():
            eng = ctx.engine
            if not eng.dry_run and hasattr(eng.hl, "set_leverage"):
                coin = eng.instrument.replace("-PERP", "").replace("-perp", "")
                max_lev = int(eng.risk_manager.limits.max_leverage)
                eng.hl.set_leverage(max_lev, coin)

        log.info("MultiWalletEngine started: %d wallets", len(self._contexts))

        while self._running:
            if max_ticks > 0 and self.tick_count >= max_ticks:
                log.info("Reached max ticks (%d), stopping", max_ticks)
                break

            # Check house halt before ticking
            if self.house_risk.should_halt_all():
                log.critical("House risk halt — stopping all wallets: %s",
                             self.house_risk.state.halt_reason)
                break

            # Tick each wallet engine
            for wid, ctx in self._contexts.items():
                try:
                    ctx.engine._tick()
                except Exception as e:
                    log.error("Wallet %s tick failed: %s", wid, e, exc_info=True)

            self.tick_count += 1

            # Aggregate house risk
            self._update_house_risk()

            if self._running and self.tick_interval > 0:
                time.sleep(self.tick_interval)

        self._shutdown()

    def _update_house_risk(self) -> None:
        """Aggregate per-wallet risk states into house-level check."""
        wallet_states = {}
        wallet_exposures: Dict[str, Decimal] = {}

        for wid, ctx in self._contexts.items():
            wallet_states[wid] = ctx.engine.risk_manager.state
            # Sum notional across all house positions for this wallet engine
            total_notional = Decimal("0")
            for inst, pos in ctx.engine.position_tracker.house_positions.items():
                total_notional += abs(pos.notional)
            wallet_exposures[wid] = total_notional

        self.house_risk.update(wallet_states)
        self.house_risk.update_exposure(wallet_exposures)

    def house_risk_summary(self) -> Dict[str, Any]:
        """Aggregate PnL, drawdown, positions across all wallets."""
        summary = self.house_risk.summary()

        # Per-wallet details
        per_wallet = {}
        for wid, ctx in self._contexts.items():
            eng = ctx.engine
            agent_id = eng.strategy.strategy_id
            positions = {}
            for inst, pos in eng.position_tracker.agent_positions.get(agent_id, {}).items():
                positions[inst] = {
                    "net_qty": str(pos.net_qty),
                    "avg_entry": str(pos.avg_entry_price),
                    "realized_pnl": str(pos.realized_pnl),
                }
            per_wallet[wid] = {
                "daily_pnl": str(eng.risk_manager.state.daily_pnl),
                "daily_drawdown": str(eng.risk_manager.state.daily_drawdown),
                "risk_gate": eng.risk_manager.state.risk_gate.value,
                "safe_mode": eng.risk_manager.state.safe_mode,
                "positions": positions,
                "tick_count": eng.tick_count,
            }

        summary["wallets"] = per_wallet
        summary["total_ticks"] = self.tick_count
        return summary

    def _handle_shutdown(self, signum, frame):
        log.info("MultiWalletEngine shutdown signal received")
        self._running = False

    def _shutdown(self) -> None:
        log.info("Shutting down MultiWalletEngine...")
        for wid, ctx in self._contexts.items():
            try:
                ctx.engine._shutdown()
            except Exception as e:
                log.error("Error shutting down wallet %s: %s", wid, e)
        log.info("MultiWalletEngine shutdown complete")
