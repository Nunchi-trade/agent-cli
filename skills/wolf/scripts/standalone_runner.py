"""WOLF standalone runner — multi-slot orchestrator tick loop.

Composes scanner + movers + DSL into a single autonomous strategy.
Each tick: fetch prices → update ROEs → check DSL → run movers → evaluate.
"""
from __future__ import annotations

import skills._bootstrap  # noqa: F401 — auto-setup sys.path

import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from modules.dsl_config import DSLConfig, PRESETS as DSL_PRESETS
from modules.dsl_guard import DSLGuard
from modules.dsl_state import DSLState, DSLStateStore
from modules.movers_guard import MoversGuard
from modules.scanner_guard import ScannerGuard
from modules.wolf_config import WolfConfig
from modules.wolf_engine import WolfAction, WolfEngine
from modules.wolf_state import WolfSlot, WolfState, WolfStateStore

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
        self.state = self.state_store.load() or WolfState.new(self.config.max_slots)

        # Sub-guards
        self.movers_guard = MoversGuard()
        self.scanner_guard = ScannerGuard()
        self.scanner_guard.history.path = f"{data_dir}/scanner-history.json"

        # DSL guards per slot (created on entry, removed on exit)
        self.dsl_guards: Dict[int, DSLGuard] = {}
        self._restore_dsl_guards()

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

    def _tick(self) -> List[WolfAction]:
        """Execute a single WOLF tick cycle."""
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

        # 4. Run scanner (every N ticks)
        scanner_opps = []
        if tick % self.config.scanner_interval_ticks == 0:
            scanner_opps = self._run_scanner()

        # 5. Watchdog (every N ticks)
        if tick % self.config.watchdog_interval_ticks == 0:
            self._watchdog()

        # 6. Engine evaluation
        actions = self.engine.evaluate(
            state=self.state,
            movers_signals=movers_signals,
            scanner_opps=scanner_opps,
            slot_prices=slot_prices,
            slot_dsl_results=slot_dsl_results,
            now_ms=now_ms,
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
                    name = universe[i].get("name", "")
                    vol = float(ctx.get("dayNtlVlm", 0))
                    if vol >= self.movers_guard.config.volume_min_24h and name:
                        try:
                            c4h = self.hl.get_candles(name, "4h", 7 * 24 * 3600 * 1000)
                            c1h = self.hl.get_candles(name, "1h", 48 * 3600 * 1000)
                            asset_candles[name] = {"4h": c4h, "1h": c1h}
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

            size = (self.config.margin_per_slot * self.config.leverage) / mid
            side = "buy" if action.direction == "long" else "sell"

            fill = self.hl.place_order(
                instrument=action.instrument,
                side=side,
                size=round(size, 4),
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

        # Reset slot
        slot.close_ts = int(time.time() * 1000)
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

    def _handle_shutdown(self, signum, frame):
        log.info("Shutdown signal received")
        self._running = False
