"""WOLF standalone runner — multi-slot orchestrator tick loop.

Composes scanner + movers + DSL + HOWL into a single autonomous strategy.
Each tick: fetch prices → update ROEs → check DSL → run movers → evaluate.
Periodic: HOWL performance review → auto-adjust config parameters.
Scheduled: daily PnL reset, comprehensive HOWL reports.
"""
from __future__ import annotations

import skills._bootstrap  # noqa: F401 — auto-setup sys.path

import json
import logging
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.dsl_config import DSLConfig, PRESETS as DSL_PRESETS
from modules.dsl_guard import DSLGuard
from modules.dsl_state import DSLState, DSLStateStore
from modules.howl_adapter import adapt, apply_adjustments
from modules.howl_engine import HowlEngine, TradeRecord
from modules.howl_reporter import HowlReporter
from modules.journal_engine import JournalEngine
from modules.journal_guard import JournalGuard
from modules.judge_guard import JudgeGuard
from modules.memory_engine import MemoryEngine
from modules.memory_guard import MemoryGuard
from modules.movers_guard import MoversGuard
from modules.scanner_guard import ScannerGuard
from modules.wolf_config import WolfConfig
from modules.wolf_engine import WolfAction, WolfEngine
from modules.wolf_state import WolfSlot, WolfState, WolfStateStore
from execution.portfolio_risk import PortfolioRiskManager, PortfolioRiskConfig
from parent.store import JSONLStore

log = logging.getLogger("wolf_runner")


class WolfRunner:
    """Autonomous WOLF strategy tick loop.

    Tick schedule (60s base):
      Every tick:      Fetch prices → update ROEs → check DSL → run movers → evaluate
      Every 5 ticks:   Watchdog health check
      Every 15 ticks:  Run scanner → queue high-score opportunities
    """

    def __init__(
        self,
        hl,
        config: Optional[WolfConfig] = None,
        tick_interval: float = 60.0,
        json_output: bool = False,
        data_dir: str = "data/wolf",
        builder: Optional[dict] = None,
        resume: bool = True,
    ):
        self.hl = hl
        self.config = config or WolfConfig()
        self.tick_interval = tick_interval
        self.json_output = json_output
        self.data_dir = data_dir
        self.builder = builder

        # Core engine (pure, zero I/O)
        self.engine = WolfEngine(self.config)

        # State + persistence
        self.state_store = WolfStateStore(path=f"{data_dir}/state.json")
        if resume:
            self.state = self.state_store.load() or WolfState.new(self.config.max_slots)
        else:
            self.state = WolfState.new(self.config.max_slots)

        # Sub-guards
        self.movers_guard = MoversGuard()
        self.scanner_guard = ScannerGuard()
        self.scanner_guard.history.path = f"{data_dir}/scanner-history.json"

        # DSL guards per slot (created on entry, removed on exit)
        self.dsl_guards: Dict[int, DSLGuard] = {}
        self._restore_dsl_guards()

        # Trade logging for HOWL
        self.trade_log = JSONLStore(path=f"{data_dir}/trades.jsonl")

        # Self-improvement subsystems
        self.memory_engine = MemoryEngine()
        self.memory_guard = MemoryGuard(data_dir=f"{data_dir}/memory")
        self.journal_engine = JournalEngine()
        self.journal_guard = JournalGuard(data_dir=data_dir)
        self.judge_guard = JudgeGuard(data_dir=data_dir)

        # Obsidian integration (optional)
        self._obsidian_writer = None
        self._obsidian_reader = None
        self._obsidian_context = None
        if self.config.obsidian_vault_path:
            try:
                from modules.obsidian_reader import ObsidianReader
                from modules.obsidian_writer import ObsidianWriter
                self._obsidian_reader = ObsidianReader(self.config.obsidian_vault_path)
                self._obsidian_writer = ObsidianWriter(self.config.obsidian_vault_path)
                if self._obsidian_reader.available:
                    self._obsidian_context = self._obsidian_reader.read_trading_context()
                    log.info("Obsidian vault loaded: %d watchlist, %d theses",
                             len(self._obsidian_context.watchlist),
                             len(self._obsidian_context.market_theses))
            except Exception as e:
                log.warning("Obsidian integration failed: %s", e)

        # Portfolio risk manager
        self.portfolio_risk = PortfolioRiskManager(PortfolioRiskConfig(
            max_correlated_positions=self.config.portfolio_max_correlated,
            max_same_direction_total=self.config.portfolio_max_same_direction,
            margin_utilization_warn=self.config.portfolio_margin_warn,
            margin_utilization_block=self.config.portfolio_margin_block,
            enabled=self.config.portfolio_risk_enabled,
        ))

        # Smart money tracker (optional)
        self.smart_money_tracker = None
        if self.config.smart_money_enabled and self.config.smart_money_addresses:
            from modules.smart_money.tracker import SmartMoneyTracker
            from modules.smart_money.config import SmartMoneyConfig
            sm_cfg = SmartMoneyConfig(
                watch_addresses=self.config.smart_money_addresses,
                min_position_usd=self.config.smart_money_min_position_usd,
                conviction_threshold=self.config.smart_money_conviction_threshold,
                poll_interval_ticks=self.config.smart_money_poll_interval_ticks,
            )
            self.smart_money_tracker = SmartMoneyTracker(sm_cfg)
            log.info("Smart money tracker: watching %d addresses", len(sm_cfg.watch_addresses))

        # Scheduled task tracking (UTC hour -> last executed date string)
        self._last_scheduled: Dict[str, str] = {}

        self._running = False

    def _restore_dsl_guards(self) -> None:
        """Restore DSL guards for active slots from persisted state."""
        dsl_store = DSLStateStore(data_dir=f"{self.data_dir}/dsl")
        for slot in self.state.active_slots():
            pos_id = f"wolf-slot-{slot.slot_id}"
            guard = DSLGuard.from_store(pos_id, store=dsl_store)
            if guard and guard.is_active:
                self.dsl_guards[slot.slot_id] = guard
                log.info("Restored DSL guard for slot %d (%s)", slot.slot_id, slot.instrument)

    def run(self, max_ticks: int = 0) -> None:
        """Main loop. Blocks until max_ticks reached or SIGINT."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        log.info("WOLF started: slots=%d leverage=%.0fx budget=$%.0f tick=%ds",
                 self.config.max_slots, self.config.leverage,
                 self.config.total_budget, self.tick_interval)

        # Log session start to memory
        try:
            event = self.memory_engine.create_session_event(
                event_type="session_start",
                tick_count=self.state.tick_count,
                total_pnl=self.state.total_pnl,
                active_slots=len(self.state.active_slots()),
                total_trades=self.state.total_trades,
            )
            self.memory_guard.log_event(event)
        except Exception:
            pass  # Memory logging should never break the runner

        while self._running:
            if max_ticks > 0 and self.state.tick_count >= max_ticks:
                log.info("Reached max ticks (%d), stopping", max_ticks)
                break

            try:
                self._tick()
            except Exception as e:
                log.error("Tick %d failed: %s", self.state.tick_count, e, exc_info=True)

            if self._running and self.tick_interval > 0 and (max_ticks == 0 or self.state.tick_count < max_ticks):
                time.sleep(self.tick_interval)

        self._print_summary()
        log.info("WOLF stopped after %d ticks", self.state.tick_count)

    def run_once(self) -> List[WolfAction]:
        """Single tick pass — no loop."""
        actions = self._tick()
        self._print_status()
        return actions

    def _check_config_override(self):
        """Check for and apply config override from UI."""
        override_path = Path(self.data_dir) / "config-override.json"
        if not override_path.exists():
            return
        try:
            with open(override_path) as f:
                override = json.load(f)
            params = override.get("params", {})
            changed = []
            for key, value in params.items():
                if hasattr(self.config, key):
                    old = getattr(self.config, key)
                    if old != value:
                        setattr(self.config, key, value)
                        changed.append(f"{key}: {old} -> {value}")
            if override.get("preset"):
                self.state.preset = override["preset"]
            if changed:
                self.engine = WolfEngine(self.config)
                log.info("Config override applied: %s", ", ".join(changed))
            override_path.unlink()
        except Exception as e:
            log.warning("Config override failed: %s", e)

    def _persist_account_state(self):
        """Write account state to disk for HTTP API."""
        try:
            state = self.hl.info.user_state(self.hl.wallet.address)
            margin = state.get("marginSummary", {})
            account = {
                "value": float(margin.get("accountValue", 0)),
                "margin": float(margin.get("totalMarginUsed", 0)),
                "withdrawable": float(margin.get("totalRawUsd", 0)),
                "network": "testnet" if os.environ.get("HL_TESTNET", "true").lower() == "true" else "mainnet",
                "updated_at": int(time.time() * 1000),
            }
            account_path = Path(self.data_dir) / "account.json"
            with open(account_path, "w") as f:
                json.dump(account, f)
        except Exception as e:
            log.debug("Account persist failed: %s", e)

    def _tick(self) -> List[WolfAction]:
        """Execute a single WOLF tick cycle."""
        self._check_config_override()
        self.state.tick_count += 1
        tick = self.state.tick_count
        now_ms = int(time.time() * 1000)

        log.info("--- WOLF tick %d ---", tick)

        # 1. Fetch current prices for active slots
        slot_prices = self._fetch_slot_prices()

        # 2. Run DSL checks for active slots
        slot_dsl_results = self._run_dsl_checks(slot_prices)

        # 3. Run movers (every tick)
        movers_signals = self._run_movers()

        # 3b. Run smart money tracker
        smart_money_signals = []
        if self.smart_money_tracker:
            try:
                smart_money_signals = self.smart_money_tracker.scan(self.hl)
            except Exception as e:
                log.warning("Smart money scan failed: %s", e)

        # 4. Run scanner (every N ticks)
        scanner_opps = []
        if tick % self.config.scanner_interval_ticks == 0:
            scanner_opps = self._run_scanner()

        # 5. Watchdog (every N ticks)
        if tick % self.config.watchdog_interval_ticks == 0:
            self._watchdog()
            self._persist_account_state()

        # 5b. HOWL self-improvement (every N ticks)
        if tick % self.config.howl_interval_ticks == 0:
            self._run_howl()

        # 5c. Scheduled tasks (time-based)
        self._check_scheduled_tasks(now_ms)

        # 6. Engine evaluation
        actions = self.engine.evaluate(
            state=self.state,
            movers_signals=movers_signals,
            scanner_opps=scanner_opps,
            slot_prices=slot_prices,
            slot_dsl_results=slot_dsl_results,
            now_ms=now_ms,
            smart_money_signals=smart_money_signals,
        )

        # 7. Execute actions
        for action in actions:
            self._execute_action(action)

        # 8. Persist state
        self.state_store.save(self.state)

        self._print_status()
        return actions

    def _fetch_slot_prices(self) -> Dict[int, float]:
        """Fetch current prices for all active slot instruments."""
        prices: Dict[int, float] = {}
        active = self.state.active_slots()
        if not active:
            return prices

        try:
            all_mids = self.hl.get_all_mids()
        except Exception as e:
            log.warning("Failed to fetch mids: %s", e)
            return prices

        for slot in active:
            coin = slot.instrument.replace("-PERP", "")
            mid = all_mids.get(coin)
            if mid:
                prices[slot.slot_id] = float(mid)

        return prices

    def _run_dsl_checks(self, slot_prices: Dict[int, float]) -> Dict[int, Dict[str, Any]]:
        """Run DSL guard checks for each active slot with a DSL guard."""
        results: Dict[int, Dict[str, Any]] = {}

        for slot in self.state.active_slots():
            guard = self.dsl_guards.get(slot.slot_id)
            if guard is None or not guard.is_active:
                continue

            price = slot_prices.get(slot.slot_id, 0)
            if price <= 0:
                continue

            try:
                dsl_result = guard.check(price)
                if dsl_result.action.value == "CLOSE":
                    results[slot.slot_id] = {
                        "action": "close",
                        "reason": dsl_result.reason,
                    }
                else:
                    results[slot.slot_id] = {
                        "action": dsl_result.action.value.lower(),
                        "roe_pct": dsl_result.roe_pct,
                    }
            except Exception as e:
                log.warning("DSL check failed for slot %d: %s", slot.slot_id, e)

        return results

    def _run_movers(self) -> List[Dict[str, Any]]:
        """Run movers scan and return signal dicts for the engine."""
        try:
            all_markets = self.hl.get_all_markets()

            # Fetch 4h candles for qualifying assets so volume surge detection works
            asset_candles: Dict[str, Dict[str, List[Dict]]] = {}
            if len(all_markets) >= 2:
                universe = all_markets[0].get("universe", [])
                ctxs = all_markets[1]
                for i, ctx in enumerate(ctxs):
                    if i >= len(universe):
                        break
                    try:
                        name = universe[i].get("name", "")
                    except (IndexError, AttributeError):
                        continue
                    vol = float(ctx.get("dayNtlVlm", 0))
                    if vol >= self.movers_guard.config.volume_min_24h and name:
                        try:
                            c4h = self.hl.get_candles(name, "4h", 7 * 24 * 3600 * 1000)
                            c1h = self.hl.get_candles(name, "1h", 48 * 3600 * 1000)
                            asset_candles[name] = {"4h": c4h, "1h": c1h}
                            time.sleep(0.05)  # Rate limit: ~20 req/s to avoid HL 429s
                        except Exception:
                            pass

            result = self.movers_guard.scan(all_markets=all_markets, asset_candles=asset_candles)
            return [
                {
                    "asset": sig.asset,
                    "signal_type": sig.signal_type,
                    "direction": sig.direction,
                    "confidence": sig.confidence,
                }
                for sig in result.signals
            ]
        except Exception as e:
            log.warning("Movers scan failed: %s", e)
            return []

    def _run_scanner(self) -> List[Dict[str, Any]]:
        """Run scanner and return opportunity dicts for the engine."""
        try:
            all_markets = self.hl.get_all_markets()

            # Fetch BTC candles
            btc_4h = self.hl.get_candles("BTC", "4h", 7 * 24 * 3600 * 1000)
            btc_1h = self.hl.get_candles("BTC", "1h", 48 * 3600 * 1000)

            result = self.scanner_guard.scan(
                all_markets=all_markets,
                btc_candles_4h=btc_4h,
                btc_candles_1h=btc_1h,
                asset_candles={},
            )

            return [
                {
                    "asset": opp.asset,
                    "direction": opp.direction,
                    "final_score": opp.final_score,
                }
                for opp in result.opportunities
            ]
        except Exception as e:
            log.warning("Scanner failed: %s", e)
            return []

    def _watchdog(self) -> None:
        """Health check — verify positions match exchange state."""
        active = self.state.active_slots()
        if not active:
            return

        try:
            account = self.hl.get_account_state()
            positions = account.get("assetPositions", [])
            exchange_instruments = set()
            for pos in positions:
                p = pos.get("position", {})
                if float(p.get("szi", "0")) != 0:
                    coin = p.get("coin", "")
                    exchange_instruments.add(f"{coin}-PERP")

            for slot in active:
                if slot.instrument not in exchange_instruments:
                    log.warning("Watchdog: slot %d (%s) has no exchange position — marking closed",
                                slot.slot_id, slot.instrument)
                    self._close_slot(slot, reason="watchdog_no_position", pnl=0)
        except Exception as e:
            log.warning("Watchdog check failed: %s", e)

    def _execute_action(self, action: WolfAction) -> None:
        """Execute a single WolfAction (enter or exit)."""
        if action.action == "enter":
            self._execute_enter(action)
        elif action.action == "exit":
            self._execute_exit(action)

    def _execute_enter(self, action: WolfAction) -> None:
        """Execute an entry order."""
        slot = next((s for s in self.state.slots if s.slot_id == action.slot_id), None)
        if slot is None:
            return

        coin = action.instrument.replace("-PERP", "")
        try:
            # Get current price for size calculation
            mids = self.hl.get_all_mids()
            mid = float(mids.get(coin, "0"))
            if mid <= 0:
                log.warning("Cannot enter %s: no mid price", action.instrument)
                slot.status = "empty"
                slot.instrument = ""
                return

            # Portfolio risk check
            current_positions = {}
            for s in self.state.active_slots():
                if s.is_active():
                    current_positions[s.instrument] = {
                        "direction": s.direction,
                        "notional": s.margin_allocated * self.config.leverage,
                    }

            ok, reason = self.portfolio_risk.check_entry(
                action.instrument, action.direction, current_positions)
            if not ok:
                log.warning("Portfolio risk blocked entry for %s: %s",
                            action.instrument, reason)
                slot.status = "empty"
                slot.instrument = ""
                return

            size = (self.config.margin_per_slot * self.config.leverage) / mid
            side = "buy" if action.direction == "long" else "sell"

            fill = self.hl.place_order(
                instrument=action.instrument,
                side=side,
                size=size,  # adapter rounds to szDecimals
                price=mid,
                tif="Ioc",
                builder=self.builder,
            )

            if fill:
                slot.status = "active"
                slot.entry_price = float(fill.price)
                slot.entry_size = float(fill.quantity)
                slot.margin_allocated = self.config.margin_per_slot
                slot.direction = action.direction
                slot.entry_source = action.source
                slot.entry_signal_score = action.signal_score
                slot.entry_ts = int(time.time() * 1000)
                slot.last_progress_ts = slot.entry_ts
                slot.last_signal_seen_ts = slot.entry_ts
                slot.high_water_roe = 0.0
                slot.current_roe = 0.0

                # Create DSL guard for this slot
                self._create_dsl_guard(slot)

                self.state.total_trades += 1
                self._log_trade(
                    tick=self.state.tick_count, instrument=action.instrument,
                    side=side, price=float(fill.price),
                    quantity=float(fill.quantity), fee=float(getattr(fill, "fee", 0)),
                    meta=f"entry:{action.source}",
                )
                log.info("ENTERED slot %d: %s %s @ %.4f size=%.4f (%s)",
                         slot.slot_id, action.direction, action.instrument,
                         float(fill.price), float(fill.quantity), action.reason)
            else:
                log.warning("Entry fill failed for %s", action.instrument)
                slot.status = "empty"
                slot.instrument = ""

        except Exception as e:
            log.error("Entry failed for %s: %s", action.instrument, e)
            slot.status = "empty"
            slot.instrument = ""

    def _execute_exit(self, action: WolfAction) -> None:
        """Execute an exit order."""
        slot = next((s for s in self.state.slots if s.slot_id == action.slot_id), None)
        if slot is None or not slot.is_active():
            return

        coin = action.instrument.replace("-PERP", "")
        try:
            mids = self.hl.get_all_mids()
            mid = float(mids.get(coin, "0"))
            side = "sell" if slot.direction == "long" else "buy"

            fill = self.hl.place_order(
                instrument=action.instrument,
                side=side,
                size=slot.entry_size,
                price=mid if mid > 0 else slot.current_price,
                tif="Ioc",
                builder=self.builder,
            )

            exit_price = fill.price if fill else mid
            pnl = 0.0
            if slot.entry_price > 0 and exit_price > 0:
                if slot.direction == "long":
                    pnl = (exit_price - slot.entry_price) / slot.entry_price * slot.margin_allocated * self.config.leverage
                else:
                    pnl = (slot.entry_price - exit_price) / slot.entry_price * slot.margin_allocated * self.config.leverage

            self._close_slot(slot, reason=action.reason, pnl=pnl)
            self._log_trade(
                tick=self.state.tick_count, instrument=action.instrument,
                side=side, price=float(exit_price),
                quantity=slot.entry_size, fee=float(getattr(fill, "fee", 0)) if fill else 0,
                meta=action.reason,
            )
            log.info("EXITED slot %d: %s %s @ %.4f PnL=$%.2f (%s)",
                     slot.slot_id, slot.direction, action.instrument,
                     exit_price, pnl, action.reason)

        except Exception as e:
            log.error("Exit failed for slot %d (%s): %s", slot.slot_id, action.instrument, e)

    def _close_slot(self, slot: WolfSlot, reason: str, pnl: float) -> None:
        """Reset a slot to empty and update PnL tracking."""
        # Close DSL guard
        guard = self.dsl_guards.pop(slot.slot_id, None)
        if guard:
            guard.mark_closed(slot.current_price, reason)

        # Update PnL
        self.state.daily_pnl += pnl
        self.state.total_pnl += pnl

        if self.state.daily_pnl <= -self.config.daily_loss_limit:
            self.state.daily_loss_triggered = True
            log.warning("DAILY LOSS LIMIT triggered: $%.2f", self.state.daily_pnl)

        # Log to trade journal
        close_ts = int(time.time() * 1000)
        try:
            journal_entry = self.journal_engine.create_entry(
                instrument=slot.instrument,
                direction=slot.direction,
                entry_price=slot.entry_price,
                exit_price=slot.current_price,
                pnl=pnl,
                roe_pct=slot.current_roe,
                entry_source=slot.entry_source,
                entry_signal_score=slot.entry_signal_score,
                close_reason=reason,
                entry_ts=slot.entry_ts,
                close_ts=close_ts,
            )
            self.journal_guard.log_entry(journal_entry)

            # Notable trade -> memory + obsidian
            if abs(pnl) > self.config.margin_per_slot * 0.1:
                mem_event = self.memory_engine.create_notable_trade_event(
                    instrument=slot.instrument,
                    direction=slot.direction,
                    pnl=pnl,
                    roe_pct=slot.current_roe,
                    entry_source=slot.entry_source,
                    close_reason=reason,
                )
                self.memory_guard.log_event(mem_event)

                if self._obsidian_writer:
                    self._obsidian_writer.write_notable_trade(journal_entry.to_dict())
        except Exception as e:
            log.debug("Journal/memory logging failed: %s", e)

        # Reset slot
        slot.close_ts = close_ts
        slot.close_reason = reason
        slot.close_pnl = pnl
        slot.status = "empty"
        slot.instrument = ""
        slot.direction = ""
        slot.entry_price = 0.0
        slot.entry_size = 0.0
        slot.current_price = 0.0
        slot.current_roe = 0.0
        slot.high_water_roe = 0.0

    def _create_dsl_guard(self, slot: WolfSlot) -> None:
        """Create a DSL guard for a newly entered slot."""
        preset_name = self.config.dsl_preset
        dsl_config = DSL_PRESETS.get(preset_name, DSL_PRESETS.get("tight", DSLConfig()))
        dsl_config = DSLConfig.from_dict(dsl_config.to_dict())  # copy
        dsl_config.direction = slot.direction
        dsl_config.leverage = self.config.dsl_leverage_override or self.config.leverage

        dsl_state = DSLState.new(
            instrument=slot.instrument,
            entry_price=slot.entry_price,
            position_size=slot.entry_size,
            direction=slot.direction,
            position_id=f"wolf-slot-{slot.slot_id}",
        )

        dsl_store = DSLStateStore(data_dir=f"{self.data_dir}/dsl")
        guard = DSLGuard(config=dsl_config, state=dsl_state, store=dsl_store)
        self.dsl_guards[slot.slot_id] = guard

    def _log_trade(self, tick: int, instrument: str, side: str,
                   price: float, quantity: float, fee: float = 0,
                   meta: str = "") -> None:
        """Append a trade record to the JSONL log."""
        self.trade_log.append({
            "tick": tick,
            "oid": f"wolf-{tick}-{instrument}",
            "instrument": instrument,
            "side": side,
            "price": str(price),
            "quantity": str(quantity),
            "timestamp_ms": int(time.time() * 1000),
            "fee": str(fee),
            "strategy": "wolf",
            "meta": meta,
        })

    def _run_howl(self) -> None:
        """Run HOWL performance review and optionally auto-adjust config."""
        try:
            raw_trades = self.trade_log.read_all()
            if not raw_trades:
                log.info("HOWL: no trades logged yet, skipping")
                return

            trades = [TradeRecord.from_dict(t) for t in raw_trades]
            metrics = HowlEngine().compute(trades)

            # Log distilled summary
            summary = HowlReporter().distill(metrics)
            log.info(summary)

            # Save report
            howl_dir = Path(self.data_dir) / "howl"
            howl_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
            report = HowlReporter().generate(metrics, date=ts)
            (howl_dir / f"{ts}.md").write_text(report)

            # Log HOWL review to memory
            try:
                howl_event = self.memory_engine.create_howl_event(
                    win_rate=metrics.win_rate,
                    net_pnl=metrics.net_pnl,
                    fdr=metrics.fdr,
                    round_trips=metrics.total_round_trips,
                    distilled=summary,
                )
                self.memory_guard.log_event(howl_event)
            except Exception:
                pass

            # Write HOWL report to Obsidian
            if self._obsidian_writer:
                try:
                    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    self._obsidian_writer.write_howl_report(
                        briefing_md=report, date=date,
                        win_rate=metrics.win_rate, net_pnl=metrics.net_pnl,
                        fdr=metrics.fdr, round_trips=metrics.total_round_trips,
                    )
                except Exception:
                    pass

            # Auto-adjust if enabled and enough data
            if (self.config.howl_auto_adjust
                    and metrics.total_round_trips >= self.config.howl_min_round_trips):
                adjustments, adj_log = adapt(metrics, self.config)
                if adjustments:
                    apply_adjustments(adjustments, self.config)
                    log.info(adj_log)
                    # Re-sync engine with updated config
                    self.engine = WolfEngine(self.config)

                    # Log param changes to memory
                    try:
                        pc_event = self.memory_engine.create_param_change_event(
                            adjustments, metrics_summary=summary,
                        )
                        self.memory_guard.log_event(pc_event)
                    except Exception:
                        pass
                else:
                    log.info("HOWL: no adjustments needed")

            # Run Judge evaluation
            try:
                judge_report = self.judge_guard.run_evaluation(self.trade_log)
                if judge_report.round_trips_evaluated > 0:
                    self.judge_guard.save_report(judge_report)
                    self.judge_guard.apply_to_memory(judge_report, self.memory_guard)
                    if judge_report.config_recommendations:
                        recs = "; ".join(r.get("summary", "") for r in judge_report.config_recommendations)
                        log.info("Judge recommendations: %s", recs)

                    # Write Judge report to Obsidian
                    if self._obsidian_writer:
                        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        self._obsidian_writer.write_judge_report(
                            judge_report.to_dict(), date=date,
                        )
            except Exception as e:
                log.debug("Judge evaluation failed: %s", e)

            # Update playbook from closed slot data
            try:
                closed = [
                    s.to_dict() if hasattr(s, 'to_dict') else {}
                    for s in self.state.slots if s.status == "empty" and s.close_pnl != 0
                ]
                if closed:
                    playbook = self.memory_guard.load_playbook()
                    playbook = self.memory_engine.update_playbook(playbook, closed)
                    self.memory_guard.save_playbook(playbook)
            except Exception:
                pass

        except Exception as e:
            log.warning("HOWL review failed: %s", e)

    def _check_scheduled_tasks(self, now_ms: int) -> None:
        """Run time-based scheduled tasks (daily reset, HOWL reports)."""
        now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        today = now.strftime("%Y-%m-%d")
        current_hour = now.hour

        # Daily PnL reset
        if (current_hour == self.config.daily_reset_hour
                and self._last_scheduled.get("daily_reset") != today):
            self._last_scheduled["daily_reset"] = today
            old_pnl = self.state.daily_pnl
            self.state.daily_pnl = 0.0
            self.state.daily_loss_triggered = False
            log.info("Daily PnL reset (was $%.2f)", old_pnl)

        # Scheduled HOWL comprehensive report
        if (current_hour == self.config.howl_report_hour
                and self._last_scheduled.get("howl_report") != today):
            self._last_scheduled["howl_report"] = today
            log.info("Scheduled HOWL report (UTC %02d:00)", current_hour)
            self._run_howl()

        # Nightly review (today vs 7-day average)
        if (self.config.nightly_review_enabled
                and current_hour == self.config.nightly_review_hour
                and self._last_scheduled.get("nightly_review") != today):
            self._last_scheduled["nightly_review"] = today
            log.info("Running nightly review (UTC %02d:00)", current_hour)
            self._run_nightly_review(today)

        # Obsidian context refresh
        if (self._obsidian_reader
                and self.state.tick_count % self.config.obsidian_scan_interval_ticks == 0
                and self.state.tick_count > 0):
            try:
                self._obsidian_context = self._obsidian_reader.read_trading_context()
            except Exception:
                pass

    def _print_status(self) -> None:
        """Print current WOLF status."""
        if self.json_output:
            import json
            print(json.dumps(self.state.to_dict(), indent=2))
            return

        active = self.state.active_slots()
        print(f"\n{'='*60}")
        print(f"WOLF tick #{self.state.tick_count}  |  "
              f"Active: {len(active)}/{self.config.max_slots}  |  "
              f"Daily PnL: ${self.state.daily_pnl:+.2f}  |  "
              f"Total PnL: ${self.state.total_pnl:+.2f}")
        print(f"{'='*60}")

        if not active:
            print("  No active positions.")
        else:
            print(f"  {'Slot':<5} {'Dir':<6} {'Instrument':<12} {'ROE':<8} {'HW':<8} {'Source':<16}")
            print(f"  {'-'*55}")
            for s in active:
                print(f"  {s.slot_id:<5} {s.direction:<6} {s.instrument:<12} "
                      f"{s.current_roe:+.1f}%{'':>2} {s.high_water_roe:.1f}%{'':>3} "
                      f"{s.entry_source:<16}")

        print()

    def _print_summary(self) -> None:
        """Print session summary on shutdown."""
        print(f"\n{'='*60}")
        print("WOLF SESSION SUMMARY")
        print(f"{'='*60}")
        print(f"  Ticks: {self.state.tick_count}")
        print(f"  Total trades: {self.state.total_trades}")
        print(f"  Daily PnL: ${self.state.daily_pnl:+.2f}")
        print(f"  Total PnL: ${self.state.total_pnl:+.2f}")
        if self.state.daily_loss_triggered:
            print("  ** Daily loss limit was triggered **")
        print(f"{'='*60}\n")

    def _run_nightly_review(self, today: str) -> None:
        """Run nightly review comparing today vs. 7-day rolling average."""
        try:
            raw_trades = self.trade_log.read_all()
            if not raw_trades:
                return

            now_ms = int(time.time() * 1000)
            day_ms = 86_400_000
            midnight = now_ms - (now_ms % day_ms)

            today_trades = [
                TradeRecord.from_dict(t) for t in raw_trades
                if t.get("timestamp_ms", 0) >= midnight
            ]
            week_trades = [
                TradeRecord.from_dict(t) for t in raw_trades
                if t.get("timestamp_ms", 0) >= midnight - (7 * day_ms)
            ]

            result = self.journal_engine.compute_nightly_review(
                today_trades, week_trades, date=today,
            )

            # Save briefing
            howl_dir = Path(self.data_dir) / "howl"
            howl_dir.mkdir(parents=True, exist_ok=True)
            (howl_dir / f"{today}-nightly.md").write_text(result.briefing_md)

            # Write findings to memory
            for finding in result.key_findings:
                event = self.memory_engine.create_howl_event(
                    distilled=f"Nightly: {finding}",
                )
                self.memory_guard.log_event(event)

            # Append to Obsidian daily note
            if self._obsidian_writer:
                summary_lines = [f"**{today}** — {result.round_trips_today} round trips"]
                for f in result.key_findings:
                    summary_lines.append(f"- {f}")
                self._obsidian_writer.append_to_daily(today, "\n".join(summary_lines))

            log.info("Nightly review: %d RTs today, findings: %s",
                     result.round_trips_today, "; ".join(result.key_findings))

        except Exception as e:
            log.warning("Nightly review failed: %s", e)

    def _handle_shutdown(self, signum, frame):
        log.info("Shutdown signal received")
        self._running = False

        # Log session end to memory
        try:
            event = self.memory_engine.create_session_event(
                event_type="session_end",
                tick_count=self.state.tick_count,
                total_pnl=self.state.total_pnl,
                active_slots=len(self.state.active_slots()),
                total_trades=self.state.total_trades,
            )
            self.memory_guard.log_event(event)
        except Exception:
            pass
