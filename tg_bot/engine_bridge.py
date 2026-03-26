"""Thread-safe bridge between Telegram bot and TradingEngine."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
from decimal import Decimal
from typing import Any, Dict, Optional

from cli.engine import TradingEngine

log = logging.getLogger("telegram.bridge")
ZERO = Decimal("0")


class NotifyingEngine(TradingEngine):
    """TradingEngine subclass that pushes events to an asyncio queue."""

    def __init__(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, **kwargs):
        super().__init__(**kwargs)
        self._event_queue = event_queue
        self._loop = loop

    def _push_event(self, event: Dict[str, Any]) -> None:
        """Thread-safe push from engine thread to async queue."""
        try:
            self._loop.call_soon_threadsafe(self._event_queue.put_nowait, event)
        except Exception:
            pass  # Don't crash engine if notification fails

    def _log_tick(self, snapshot, decisions, fills, ok: bool) -> None:
        """Override to also push tick data to Telegram."""
        super()._log_tick(snapshot, decisions, fills, ok)

        agent_id = self.strategy.strategy_id
        pos = self.position_tracker.get_agent_position(agent_id, self.instrument)
        mid_dec = Decimal(str(snapshot.mid_price))

        # Push fills as individual events
        for fill in fills:
            self._push_event({
                "type": "fill",
                "side": fill.side,
                "quantity": str(fill.quantity),
                "price": str(fill.price),
                "instrument": fill.instrument,
                "strategy": self.strategy.strategy_id,
                "tick": self.tick_count,
            })

        # Push tick summary
        self._push_event({
            "type": "tick",
            "tick_count": self.tick_count,
            "instrument": self.instrument,
            "strategy": self.strategy.strategy_id,
            "mid_price": snapshot.mid_price,
            "pos_qty": float(pos.net_qty),
            "avg_entry": float(pos.avg_entry_price),
            "upnl": float(pos.unrealized_pnl(mid_dec)),
            "rpnl": float(pos.realized_pnl),
            "orders_sent": len(decisions),
            "orders_filled": len(fills),
            "risk_ok": ok,
            "reduce_only": self.risk_manager.state.reduce_only,
            "safe_mode": self.risk_manager.state.safe_mode,
        })

    def _handle_shutdown(self, signum, frame):
        """Override to push shutdown event instead of setting signal handler."""
        log.info("Engine shutdown signal received")
        self._running = False

    def _shutdown(self):
        """Override to push shutdown summary."""
        super()._shutdown()

        agent_id = self.strategy.strategy_id
        pos = self.position_tracker.get_agent_position(agent_id, self.instrument)
        elapsed = (time.time() * 1000 - self.start_time_ms) / 1000

        try:
            snap = self.hl.get_snapshot(self.instrument)
            mid = Decimal(str(snap.mid_price)) if snap.mid_price > 0 else pos.avg_entry_price
        except Exception:
            mid = pos.avg_entry_price

        stats = self.order_manager.stats
        self._push_event({
            "type": "shutdown",
            "tick_count": self.tick_count,
            "total_placed": stats["total_placed"],
            "total_filled": stats["total_filled"],
            "total_pnl": float(pos.total_pnl(mid)),
            "elapsed_s": elapsed,
        })


class EngineBridge:
    """Manages TradingEngine lifecycle from async Telegram context."""

    def __init__(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.event_queue = event_queue
        self.loop = loop
        self.engine: Optional[NotifyingEngine] = None
        self.engine_thread: Optional[threading.Thread] = None
        self._paused = False

    def is_running(self) -> bool:
        return (
            self.engine is not None
            and self.engine._running
            and self.engine_thread is not None
            and self.engine_thread.is_alive()
        )

    def start_agent(
        self,
        strategy_name: str,
        instrument: str,
        mainnet: bool = False,
        risk_overrides: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        mock: bool = False,
    ) -> Dict[str, Any]:
        """Start a trading agent in a background thread."""
        if self.is_running():
            raise RuntimeError("Agent is already running. Stop it first.")

        import sys
        from pathlib import Path

        project_root = str(Path(__file__).resolve().parent.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from cli.config import TradingConfig
        from cli.strategy_registry import resolve_instrument, resolve_strategy_path
        from sdk.strategy_sdk.loader import load_strategy

        # Build config
        cfg = TradingConfig()
        cfg.strategy = strategy_name
        cfg.instrument = resolve_instrument(instrument)
        cfg.mainnet = mainnet
        cfg.dry_run = dry_run

        if risk_overrides:
            for key, val in risk_overrides.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, val)

        # Network guard
        if mainnet:
            env_testnet = os.environ.get("HL_TESTNET", "true").lower()
            if env_testnet == "true":
                raise RuntimeError(
                    "Cannot deploy on mainnet: HL_TESTNET=true in environment. "
                    "Set HL_TESTNET=false first."
                )

        # Resolve strategy
        strategy_path = resolve_strategy_path(cfg.strategy)
        strategy_cls = load_strategy(strategy_path)
        strategy_instance = strategy_cls(strategy_id=cfg.strategy, **dict(cfg.strategy_params))

        # Build HL adapter
        if mock or dry_run:
            from cli.hl_adapter import DirectMockProxy
            hl = DirectMockProxy()
        else:
            from cli.hl_adapter import DirectHLProxy
            from parent.hl_proxy import HLProxy

            private_key = cfg.get_private_key()
            raw_hl = HLProxy(private_key=private_key, testnet=not cfg.mainnet)
            hl = DirectHLProxy(raw_hl)

        # Builder fee
        builder_cfg = cfg.get_builder_config()
        builder_info = builder_cfg.to_builder_info()

        # Create engine
        self.engine = NotifyingEngine(
            event_queue=self.event_queue,
            loop=self.loop,
            hl=hl,
            strategy=strategy_instance,
            instrument=cfg.instrument,
            tick_interval=cfg.tick_interval,
            dry_run=cfg.dry_run,
            data_dir=cfg.data_dir,
            risk_limits=cfg.to_risk_limits(),
            builder=builder_info,
        )

        # Start in background thread
        self.engine_thread = threading.Thread(
            target=self._run_engine,
            name="trading-engine",
            daemon=True,
        )
        self.engine_thread.start()
        self._paused = False

        log.info("Agent started: strategy=%s instrument=%s mainnet=%s",
                 strategy_name, instrument, mainnet)
        return {"status": "started", "strategy": strategy_name, "instrument": instrument}

    def _run_engine(self) -> None:
        """Engine thread entry point."""
        try:
            self.engine.run(resume=True)
        except Exception as e:
            log.error("Engine crashed: %s", e, exc_info=True)
            self.engine._push_event({
                "type": "error",
                "message": f"Engine crashed: {e}",
            })

    def stop_agent(self) -> Dict[str, Any]:
        """Stop the running agent. Returns shutdown summary."""
        if not self.engine:
            return {"status": "not_running"}

        self.engine._running = False

        if self.engine_thread and self.engine_thread.is_alive():
            self.engine_thread.join(timeout=30)

        stats = self.engine.order_manager.stats if self.engine else {}
        agent_id = self.engine.strategy.strategy_id if self.engine else "unknown"
        elapsed = (time.time() * 1000 - self.engine.start_time_ms) / 1000 if self.engine else 0

        # Get final PnL
        total_pnl = 0.0
        if self.engine:
            pos = self.engine.position_tracker.get_agent_position(agent_id, self.engine.instrument)
            try:
                snap = self.engine.hl.get_snapshot(self.engine.instrument)
                mid = Decimal(str(snap.mid_price))
            except Exception:
                mid = pos.avg_entry_price
            total_pnl = float(pos.total_pnl(mid))

        result = {
            "status": "stopped",
            "tick_count": self.engine.tick_count if self.engine else 0,
            "total_placed": stats.get("total_placed", 0),
            "total_filled": stats.get("total_filled", 0),
            "total_pnl": total_pnl,
            "elapsed_s": elapsed,
        }

        self.engine = None
        self.engine_thread = None
        return result

    def pause_agent(self) -> None:
        """Pause the engine (stops ticking but keeps state)."""
        if self.engine:
            self.engine._running = False
            self._paused = True

    def resume_agent(self) -> None:
        """Resume from paused state."""
        if not self.engine or not self._paused:
            raise RuntimeError("No paused agent to resume")

        self.engine_thread = threading.Thread(
            target=self._run_engine,
            name="trading-engine",
            daemon=True,
        )
        self.engine._running = True
        self.engine_thread.start()
        self._paused = False

    def get_status(self) -> Dict[str, Any]:
        """Get current engine status (thread-safe read of engine state)."""
        if not self.engine:
            return {"running": False}

        agent_id = self.engine.strategy.strategy_id
        pos = self.engine.position_tracker.get_agent_position(agent_id, self.engine.instrument)
        mid_dec = Decimal(str(1.0))

        try:
            snap = self.engine.hl.get_snapshot(self.engine.instrument)
            mid_dec = Decimal(str(snap.mid_price))
        except Exception:
            mid_dec = pos.avg_entry_price if pos.avg_entry_price > 0 else Decimal("1")

        elapsed = (time.time() * 1000 - self.engine.start_time_ms) / 1000

        return {
            "running": self.engine._running,
            "strategy": agent_id,
            "instrument": self.engine.instrument,
            "tick_count": self.engine.tick_count,
            "pos_qty": float(pos.net_qty),
            "avg_entry": float(pos.avg_entry_price),
            "upnl": float(pos.unrealized_pnl(mid_dec)),
            "rpnl": float(pos.realized_pnl),
            "elapsed_s": elapsed,
            "risk_ok": self.engine.risk_manager.can_trade(),
            "reduce_only": self.engine.risk_manager.state.reduce_only,
            "safe_mode": self.engine.risk_manager.state.safe_mode,
        }
