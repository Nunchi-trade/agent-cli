"""APEX standalone runner — multi-slot orchestrator tick loop.

Composes radar + pulse + Guard + REFLECT into a single autonomous strategy.
Each tick: fetch prices -> update ROEs -> check Guard -> run pulse -> evaluate.
Periodic: REFLECT performance review → auto-adjust config parameters.
Scheduled: daily PnL reset, comprehensive REFLECT reports.
"""
from __future__ import annotations

import skills._bootstrap  # noqa: F401 — auto-setup sys.path

import json
import logging
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from cli.hl_adapter import APICircuitBreakerOpen
except ImportError:
    class APICircuitBreakerOpen(Exception):  # type: ignore[no-redef]
        pass

from common.models import (
    asset_matches_allowed, asset_to_coin, coin_to_instrument, instrument_to_coin,
    get_hip3_dex_ids, HIP3_DEXS,
)
from modules.guard_config import GuardConfig, PRESETS as GUARD_PRESETS
from modules.guard_bridge import GuardBridge
from modules.guard_state import GuardState, GuardStateStore
from modules.reflect_adapter import adapt, apply_adjustments
from modules.reflect_engine import ReflectEngine, TradeRecord
from modules.reflect_reporter import ReflectReporter
from modules.journal_engine import JournalEngine
from modules.journal_guard import JournalGuard
from modules.judge_guard import JudgeGuard
from modules.memory_engine import MemoryEngine
from modules.memory_guard import MemoryGuard
from modules.pulse_guard import PulseGuard
from modules.radar_guard import RadarGuard
from modules.strategy_guard import StrategyGuard
from modules.apex_config import ApexConfig
from modules.apex_engine import ApexAction, ApexEngine
from modules.apex_state import ApexSlot, ApexState, ApexStateStore
from execution.portfolio_risk import PortfolioRiskManager, PortfolioRiskConfig
from modules.reconciliation import ReconciliationEngine
from modules.wallet_manager import WalletManager
from parent.store import JSONLStore
from cli.telemetry import create_telemetry

log = logging.getLogger("apex_runner")


class ApexRunner:
    """Autonomous APEX strategy tick loop.

    Tick schedule (60s base):
      Every tick:      Fetch prices -> update ROEs -> check Guard -> run pulse -> evaluate
      Every 5 ticks:   Watchdog health check
      Every 15 ticks:  Run radar → queue high-score opportunities
    """

    def __init__(
        self,
        hl,
        config: Optional[ApexConfig] = None,
        tick_interval: float = 60.0,
        json_output: bool = False,
        data_dir: str = "data/apex",
        builder: Optional[dict] = None,
        resume: bool = True,
    ):
        self.hl = hl
        self.config = config or ApexConfig()
        self.tick_interval = tick_interval
        self.json_output = json_output
        self.data_dir = data_dir
        self.builder = builder

        # Wallet manager (single-wallet by default, multi-wallet via config)
        if self.config.wallet_config:
            self.wallet_manager = WalletManager.from_yaml_section(self.config.wallet_config)
            log.info("Multi-wallet mode: %d wallets configured", len(self.wallet_manager.wallet_ids))
        else:
            self.wallet_manager = WalletManager.from_single(
                budget=self.config.total_budget,
                leverage=self.config.leverage,
                guard_preset=self.config.guard_preset,
                max_slots=self.config.max_slots,
                daily_loss_limit=self.config.daily_loss_limit,
            )

        # Core engine (pure, zero I/O)
        self.engine = ApexEngine(self.config)

        # State + persistence
        self.state_store = ApexStateStore(path=f"{data_dir}/state.json")
        if resume:
            self.state = self.state_store.load() or ApexState.new(self.config.max_slots)
        else:
            self.state = ApexState.new(self.config.max_slots)

        # Sub-guards
        self.pulse_guard = PulseGuard()
        self.radar_guard = RadarGuard()
        self.radar_guard.history.path = f"{data_dir}/radar-history.json"

        # Clear radar scan history on --fresh so stale signals don't persist
        if not resume:
            radar_hist = Path(f"{data_dir}/radar-history.json")
            if radar_hist.exists():
                radar_hist.unlink()
                log.info("Cleared radar scan history (--fresh)")

        self.strategy_guard: Optional[StrategyGuard] = None

        # Guard bridges per slot (created on entry, removed on exit)
        self.guard_bridges: Dict[int, GuardBridge] = {}
        self._restore_guard_bridges()

        # Reconciliation engine
        self.recon_engine = ReconciliationEngine()
        self._reconcile_on_startup()

        # Trade logging for REFLECT
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

        self._init_strategy_guard()

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

        # Telemetry (fire-and-forget, never blocks trading)
        try:
            wallet_addr = self.hl.wallet.address if hasattr(self.hl, 'wallet') else os.environ.get("HL_WALLET_ADDRESS", "unknown")
            self.telemetry = create_telemetry(wallet_address=wallet_addr, strategy_name="apex")
        except Exception:
            self.telemetry = None

        self._running = False
        self._consecutive_timeouts = 0
        self._tick_timeout_s = 30  # max seconds per tick
        self._max_consecutive_timeouts = 3
        self._tick_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="apex-tick")

    def _init_strategy_guard(self) -> None:
        """Initialize strategy guard based on config: auto-route for mapped markets, or legacy opt-in."""
        from modules.market_strategy_map import has_strategy_mapping

        if (self.config.strategy_enabled
                and self.config.allowed_instruments
                and has_strategy_mapping(self.config.allowed_instruments)):
            self.strategy_guard = StrategyGuard(
                target_markets=self.config.allowed_instruments,
                enabled=True,
            )
            log.info("Strategy guard (auto-routed): markets=%s", self.config.allowed_instruments)
        elif self.config.strategy_enabled and self.config.strategy_names:
            self.strategy_guard = StrategyGuard(
                strategy_names=self.config.strategy_names,
                enabled=True,
            )
            log.info("Strategy guard (explicit): %d strategies loaded", len(self.strategy_guard.strategies))
        else:
            self.strategy_guard = None

    def _restore_guard_bridges(self) -> None:
        """Restore Guard bridges for active slots from persisted state."""
        guard_store = GuardStateStore(data_dir=f"{self.data_dir}/guard")
        for slot in self.state.active_slots():
            pos_id = f"apex-slot-{slot.slot_id}"
            guard = GuardBridge.from_store(pos_id, store=guard_store)
            if guard and guard.is_active:
                self.guard_bridges[slot.slot_id] = guard
                log.info("Restored Guard bridge for slot %d (%s)", slot.slot_id, slot.instrument)

    def _preflight_check(self) -> None:
        """Verify account has funds before starting. Warns loudly if not."""
        try:
            account = self.hl.get_account_state()
            # get_account_state() returns processed dict with "account_value" key
            balance = float(account.get("account_value", 0))

            if balance <= 0:
                is_testnet = os.environ.get("HL_TESTNET", "true").lower() == "true"
                if is_testnet:
                    log.warning(
                        "** NO FUNDS DETECTED ** "
                        "On testnet, claim USDyP first: hl setup claim-usdyp"
                    )
                else:
                    log.warning(
                        "** NO FUNDS DETECTED ** "
                        "On mainnet, deposit USDC via the Hyperliquid web UI"
                    )
                log.warning("Without funds, all orders will fail silently.")
            else:
                log.info("Account balance: $%.2f", balance)
        except Exception as e:
            log.warning("Preflight balance check failed: %s (continuing anyway)", e)

    def run(self, max_ticks: int = 0) -> None:
        """Main loop. Blocks until max_ticks reached or SIGINT."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        self._preflight_check()

        # Register with telemetry service
        if self.telemetry:
            try:
                self.telemetry.register()
            except Exception:
                pass  # telemetry should never break the runner

        self._start_time = time.time()

        log.info("APEX started: slots=%d leverage=%.0fx budget=$%.0f tick=%ds",
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
                future = self._tick_executor.submit(self._tick)
                future.result(timeout=self._tick_timeout_s)
                self._consecutive_timeouts = 0
            except FuturesTimeoutError:
                self._consecutive_timeouts += 1
                log.error("APEX tick %d timed out after %ds (%d/%d consecutive)",
                          self.state.tick_count, self._tick_timeout_s,
                          self._consecutive_timeouts, self._max_consecutive_timeouts)
                if self._consecutive_timeouts >= self._max_consecutive_timeouts:
                    log.critical("APEX entering safe mode: %d consecutive tick timeouts",
                                 self._consecutive_timeouts)
                    self.state.safe_mode = True
            except APICircuitBreakerOpen as e:
                log.critical("API circuit breaker open — APEX entering safe mode: %s", e)
                self.state.safe_mode = True
            except Exception as e:
                log.error("Tick %d failed: %s", self.state.tick_count, e, exc_info=True)

            if self._running and self.tick_interval > 0 and (max_ticks == 0 or self.state.tick_count < max_ticks):
                time.sleep(self.tick_interval)

        self._print_summary()
        log.info("APEX stopped after %d ticks", self.state.tick_count)

    def run_once(self) -> List[ApexAction]:
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
                elif hasattr(self.radar_guard.config, key):
                    old = getattr(self.radar_guard.config, key)
                    if old != value:
                        setattr(self.radar_guard.config, key, value)
                        self.radar_guard.engine = type(self.radar_guard.engine)(self.radar_guard.config)
                        changed.append(f"radar.{key}: {old} -> {value}")
            # Sync radar_score_threshold to RadarConfig.score_threshold
            if "radar_score_threshold" in params:
                self.radar_guard.config.score_threshold = params["radar_score_threshold"]
                self.radar_guard.engine = type(self.radar_guard.engine)(self.radar_guard.config)
            new_markets = override.get("markets")
            if new_markets is not None and new_markets != self.config.allowed_instruments:
                old_markets = self.config.allowed_instruments
                self.config.allowed_instruments = new_markets
                changed.append(f"allowed_instruments: {old_markets} -> {new_markets}")
            if "strategy_enabled" in params or new_markets is not None:
                self._init_strategy_guard()
            if override.get("preset"):
                self.state.preset = override["preset"]
            if changed:
                self.engine = ApexEngine(self.config)
                log.info("Config override applied: %s", ", ".join(changed))
            override_path.unlink()
        except Exception as e:
            log.warning("Config override failed: %s", e)

    def _merge_hip3_markets(self, all_markets: list) -> list:
        """Merge HIP-3 DEX markets into all_markets if allowed_instruments needs them."""
        dex_ids = get_hip3_dex_ids(self.config.allowed_instruments)
        if not dex_ids:
            return all_markets
        merged_meta = dict(all_markets[0])
        merged_universe = list(merged_meta.get("universe", []))
        merged_ctxs = list(all_markets[1])
        for dex_id in dex_ids:
            try:
                dex_data = self.hl.get_dex_markets(dex_id)
                if not dex_data or len(dex_data) < 2:
                    continue
                prefix = HIP3_DEXS[dex_id]["coin_prefix"]
                for entry in dex_data[0].get("universe", []):
                    entry = dict(entry)
                    name = entry.get("name", "")
                    if name.startswith(prefix):
                        entry["name"] = name[len(prefix):]
                    merged_universe.append(entry)
                merged_ctxs.extend(dex_data[1])
            except Exception as e:
                log.warning("Failed to fetch %s markets: %s", dex_id, e)
        merged_meta["universe"] = merged_universe
        return [merged_meta, merged_ctxs]

    def _get_all_mids(self) -> dict:
        """Fetch mid prices including HIP-3 DEXs if any are needed."""
        mids = self.hl.get_all_mids()
        # Merge HIP-3 mids if allowed_instruments or active positions need them
        active_instruments = [s.instrument for s in self.state.active_slots()]
        all_instruments = list(self.config.allowed_instruments) + active_instruments
        for dex_id in get_hip3_dex_ids(all_instruments):
            try:
                mids.update(self.hl.get_dex_mids(dex_id))
            except Exception as e:
                log.warning("Failed to fetch %s mids: %s", dex_id, e)
        return mids

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

    def _persist_metrics(self, tick_latency_ms: float) -> None:
        """Write operational metrics to disk for /metrics endpoint."""
        try:
            metrics = {
                "tick_count": self.state.tick_count,
                "tick_latency_ms": round(tick_latency_ms, 1),
                "active_slots": len(self.state.active_slots()),
                "daily_pnl": round(self.state.daily_pnl, 2),
                "total_pnl": round(self.state.total_pnl, 2),
                "total_trades": self.state.total_trades,
                "safe_mode": getattr(self.state, "safe_mode", False),
                "consecutive_timeouts": self._consecutive_timeouts,
                "updated_at": int(time.time() * 1000),
            }
            metrics_path = Path(self.data_dir) / "metrics.json"
            with open(metrics_path, "w") as f:
                json.dump(metrics, f)
        except Exception:
            pass  # metrics are best-effort

    def _tick(self) -> List[ApexAction]:
        """Execute a single APEX tick cycle."""
        t0 = time.monotonic()
        self._check_config_override()
        self.state.tick_count += 1
        tick = self.state.tick_count
        now_ms = int(time.time() * 1000)

        log.info("--- APEX tick %d ---", tick)

        # 1. Fetch current prices for active slots
        slot_prices = self._fetch_slot_prices()

        # 2. Run Guard checks for active slots
        slot_guard_results = self._run_guard_checks(slot_prices)

        # 3. Run pulse (every tick)
        pulse_signals = self._run_pulse()

        # 3b. Run smart money tracker
        smart_money_signals = []
        if self.smart_money_tracker:
            try:
                smart_money_signals = self.smart_money_tracker.scan(self.hl)
            except Exception as e:
                log.warning("Smart money scan failed: %s", e)

        # 3c. Run directional strategies
        strategy_signals = []
        if self.strategy_guard and tick % self.config.strategy_interval_ticks == 0:
            try:
                all_markets = self.hl.get_all_markets()
                strategy_signals = self.strategy_guard.scan(
                    all_markets=all_markets,
                    slot_prices=slot_prices,
                    target_markets=self.config.allowed_instruments or None,
                )
            except Exception as e:
                log.warning("Strategy guard scan failed: %s", e)

        # 4. Run radar (every N ticks)
        radar_opps = []
        if tick % self.config.radar_interval_ticks == 0:
            radar_opps = self._run_radar()

        # 5. Watchdog (every N ticks)
        if tick % self.config.watchdog_interval_ticks == 0:
            self._watchdog()
            self._persist_account_state()

        # 5b. REFLECT self-improvement (every N ticks)
        if tick % self.config.reflect_interval_ticks == 0:
            self._run_reflect()

        # 5c. Scheduled tasks (time-based)
        self._check_scheduled_tasks(now_ms)

        # 6. Engine evaluation
        actions = self.engine.evaluate(
            state=self.state,
            pulse_signals=pulse_signals,
            radar_opps=radar_opps,
            slot_prices=slot_prices,
            slot_guard_results=slot_guard_results,
            now_ms=now_ms,
            smart_money_signals=smart_money_signals,
            strategy_signals=strategy_signals,
        )

        # 7. Execute actions
        for action in actions:
            self._execute_action(action)

        # 8. Persist state
        self.state_store.save(self.state)

        # 9. Tick latency tracking
        elapsed_s = time.monotonic() - t0
        elapsed_ms = elapsed_s * 1000
        if elapsed_s > self.tick_interval * 0.8 and self.tick_interval > 0:
            log.warning("Tick %d took %.1fs (%.0f%% of %.0fs interval)",
                        tick, elapsed_s, (elapsed_s / self.tick_interval) * 100,
                        self.tick_interval)
        else:
            log.debug("Tick %d completed in %.1fms", tick, elapsed_ms)

        # 10. Persist metrics for /metrics endpoint
        self._persist_metrics(elapsed_ms)

        # 11. Telemetry heartbeat (every N ticks, fire-and-forget)
        if self.telemetry and self.telemetry.should_heartbeat(tick):
            try:
                self.telemetry.heartbeat(
                    tick_count=tick,
                    uptime_s=time.time() - getattr(self, '_start_time', time.time()),
                    active_positions=len(self.state.active_slots()),
                )
            except Exception:
                pass

        self._print_status()
        return actions

    def _fetch_slot_prices(self) -> Dict[int, float]:
        """Fetch current prices for all active slot instruments."""
        prices: Dict[int, float] = {}
        active = self.state.active_slots()
        if not active:
            return prices

        try:
            all_mids = self._get_all_mids()
        except Exception as e:
            log.warning("Failed to fetch mids: %s", e)
            return prices

        for slot in active:
            coin = instrument_to_coin(slot.instrument)
            mid = all_mids.get(coin)
            if mid:
                prices[slot.slot_id] = float(mid)

        return prices

    def _run_guard_checks(self, slot_prices: Dict[int, float]) -> Dict[int, Dict[str, Any]]:
        """Run Guard checks for each active slot with a Guard bridge."""
        results: Dict[int, Dict[str, Any]] = {}

        for slot in self.state.active_slots():
            guard = self.guard_bridges.get(slot.slot_id)
            if guard is None or not guard.is_active:
                continue

            price = slot_prices.get(slot.slot_id, 0)
            if price <= 0:
                continue

            try:
                guard_result = guard.check(price)
                _close_actions = {"close", "phase1_timeout", "weak_peak_cut"}
                if guard_result.action.value in _close_actions:
                    _labels = {
                        "close": "GUARD CLOSE",
                        "phase1_timeout": "PHASE1 TIMEOUT (90min no-graduation)",
                        "weak_peak_cut": "WEAK PEAK CUT (45min, peak ROE < 3%)",
                    }
                    log.warning("Slot %d — %s: %s | roe=%.2f%% hw=%.4f",
                                slot.slot_id,
                                _labels.get(guard_result.action.value, guard_result.action.value),
                                guard_result.reason,
                                guard_result.roe_pct,
                                guard_result.state.high_water if guard_result.state else 0)
                    results[slot.slot_id] = {
                        "action": "close",
                        "reason": guard_result.reason,
                    }
                else:
                    results[slot.slot_id] = {
                        "action": guard_result.action.value.lower(),
                        "roe_pct": guard_result.roe_pct,
                    }
                    # Sync exchange SL on tier changes
                    if guard_result.action.value == "TIER_CHANGED":
                        guard.sync_exchange_sl(self.hl, slot.instrument)
            except Exception as e:
                log.warning("Guard check failed for slot %d: %s", slot.slot_id, e)

        return results

    def _run_pulse(self) -> List[Dict[str, Any]]:
        """Run pulse scan and return signal dicts for the engine."""
        try:
            all_markets = self.hl.get_all_markets()
            all_markets = self._merge_hip3_markets(all_markets)

            # Fetch 4h candles for qualifying assets so volume surge detection works
            asset_candles: Dict[str, Dict[str, List[Dict]]] = {}
            allowed = set(self.config.allowed_instruments) if self.config.allowed_instruments else None
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
                    if vol >= self.pulse_guard.config.volume_min_24h and name:
                        if allowed and not asset_matches_allowed(name, allowed):
                            continue
                        try:
                            hl_coin = asset_to_coin(name)
                            c4h = self.hl.get_candles(hl_coin, "4h", 7 * 24 * 3600 * 1000)
                            c1h = self.hl.get_candles(hl_coin, "1h", 48 * 3600 * 1000)
                            asset_candles[name] = {"4h": c4h, "1h": c1h}
                            time.sleep(0.05)  # Rate limit: ~20 req/s to avoid HL 429s
                        except Exception:
                            pass

            # Post-scan delay: if we fetched candles, pause to let rate limits reset
            if asset_candles:
                time.sleep(1.0)

            result = self.pulse_guard.scan(all_markets=all_markets, asset_candles=asset_candles)
            if allowed:
                result.signals = [
                    s for s in result.signals
                    if asset_matches_allowed(s.asset, allowed)
                ]
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
            log.warning("Pulse scan failed: %s", e)
            return []

    def _run_radar(self) -> List[Dict[str, Any]]:
        """Run radar and return opportunity dicts for the engine."""
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            all_markets = self.hl.get_all_markets()
            all_markets = self._merge_hip3_markets(all_markets)

            # Pre-screen to find which assets need candle data
            assets = self.radar_guard.engine._bulk_screen(all_markets)
            top_assets = self.radar_guard.engine._select_top(assets)
            asset_names = [a.name for a in top_assets]
            if self.config.allowed_instruments:
                allowed = set(self.config.allowed_instruments)
                asset_names = [n for n in asset_names if asset_matches_allowed(n, allowed)]

            rcfg = self.radar_guard.config
            btc_4h, btc_1h = [], []
            asset_candles = {}

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {}
                futures[pool.submit(self.hl.get_candles, "BTC", "4h", rcfg.lookback_4h_ms)] = ("_btc", "4h")
                futures[pool.submit(self.hl.get_candles, "BTC", "1h", rcfg.lookback_1h_ms)] = ("_btc", "1h")
                for name in asset_names:
                    hl_coin = asset_to_coin(name)
                    for interval, lookback in [("4h", rcfg.lookback_4h_ms), ("1h", rcfg.lookback_1h_ms), ("15m", rcfg.lookback_15m_ms)]:
                        futures[pool.submit(self.hl.get_candles, hl_coin, interval, lookback)] = (name, interval)

                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        data = future.result()
                        if key[0] == "_btc":
                            if key[1] == "4h": btc_4h = data
                            else: btc_1h = data
                        else:
                            asset_candles.setdefault(key[0], {})[key[1]] = data
                    except Exception as e:
                        log.warning("Failed to fetch candles for %s %s: %s", key[0], key[1], e)

            result = self.radar_guard.scan(
                all_markets=all_markets,
                btc_candles_4h=btc_4h,
                btc_candles_1h=btc_1h,
                asset_candles=asset_candles,
            )

            if self.config.allowed_instruments:
                allowed = set(self.config.allowed_instruments)
                result.opportunities = [
                    o for o in result.opportunities
                    if asset_matches_allowed(o.asset, allowed)
                ]

            return [
                {
                    "asset": opp.asset,
                    "direction": opp.direction,
                    "final_score": opp.final_score,
                }
                for opp in result.opportunities
            ]
        except Exception as e:
            log.warning("Radar failed: %s", e)
            return []

    def _reconcile_on_startup(self) -> None:
        """Run reconciliation at startup to detect orphans from crashes."""
        try:
            account = self.hl.get_account_state()
            positions = account.get("positions", [])
            slot_dicts = [s.to_dict() for s in self.state.slots]
            discrepancies = self.recon_engine.reconcile(slot_dicts, positions)

            for d in discrepancies:
                if d.type == "orphan_exchange":
                    log.warning("STARTUP RECON: %s", d.detail)
                    self._adopt_orphan(d)
                elif d.type == "orphan_slot":
                    log.warning("STARTUP RECON: %s", d.detail)
                    slot = next((s for s in self.state.slots if s.slot_id == d.slot_id), None)
                    if slot:
                        self._close_slot(slot, reason="recon_orphan_slot", pnl=0)
                elif d.type == "size_mismatch":
                    log.warning("STARTUP RECON: %s", d.detail)
                    slot = next((s for s in self.state.slots if s.slot_id == d.slot_id), None)
                    if slot:
                        slot.entry_size = d.exchange_size
                        log.info("Corrected slot %d size to %.4f", d.slot_id, d.exchange_size)

            if discrepancies:
                self.state_store.save(self.state)
                log.info("Startup reconciliation: %d discrepancies resolved", len(discrepancies))
        except Exception as e:
            log.warning("Startup reconciliation failed: %s", e)

    def _adopt_orphan(self, discrepancy) -> None:
        """Adopt an orphaned exchange position into an empty slot."""
        empty = self.state.get_empty_slot()
        if not empty:
            log.error("ORPHAN NOT ADOPTED — no empty slot for %s (%.4f)",
                      discrepancy.instrument, discrepancy.exchange_size)
            return

        # Determine direction from exchange
        # We need to re-fetch or infer; the discrepancy has exchange_size (abs)
        # but we need the signed szi. Fetch account again for this position.
        try:
            account = self.hl.get_account_state()
            positions = account.get("positions", [])
            szi = 0.0
            entry_px = 0.0
            for pos in positions:
                p = pos.get("position", pos)
                coin = p.get("coin", "")
                if coin_to_instrument(coin) == discrepancy.instrument:
                    szi = float(p.get("szi", "0"))
                    entry_px = float(p.get("entryPx", "0"))
                    break

            direction = "long" if szi > 0 else "short"
            size = abs(szi)

            empty.status = "active"
            empty.instrument = discrepancy.instrument
            empty.direction = direction
            empty.entry_size = size
            empty.entry_price = entry_px
            empty.entry_ts = int(time.time() * 1000)
            empty.entry_source = "recon_adopted"
            empty.current_price = entry_px

            # Create a GUARD bridge for the adopted position
            guard_cfg = GUARD_PRESETS.get(self.config.guard_preset, GUARD_PRESETS["tight"])
            guard_cfg = GuardConfig.from_dict(guard_cfg.to_dict())  # copy
            guard_cfg.direction = direction
            guard_cfg.leverage = self.config.guard_leverage_override or self.config.leverage
            guard_state = GuardState.new(
                instrument=discrepancy.instrument,
                entry_price=entry_px,
                position_size=size,
                direction=direction,
                position_id=f"apex-slot-{empty.slot_id}",
            )
            guard_store = GuardStateStore(data_dir=f"{self.data_dir}/guard")
            guard = GuardBridge(config=guard_cfg, state=guard_state, store=guard_store)
            self.guard_bridges[empty.slot_id] = guard

            log.info("ADOPTED orphan %s into slot %d: %s %.4f @ %.2f",
                     discrepancy.instrument, empty.slot_id, direction, size, entry_px)
        except Exception as e:
            log.error("Failed to adopt orphan %s: %s", discrepancy.instrument, e)

    def _watchdog(self) -> None:
        """Health check — reconcile positions against exchange state."""
        try:
            account = self.hl.get_account_state()
            positions = account.get("positions", [])
            slot_dicts = [s.to_dict() for s in self.state.slots]
            discrepancies = self.recon_engine.reconcile(slot_dicts, positions)

            for d in discrepancies:
                if d.type == "orphan_slot":
                    log.warning("Watchdog: %s", d.detail)
                    slot = next((s for s in self.state.slots if s.slot_id == d.slot_id), None)
                    if slot:
                        self._close_slot(slot, reason="watchdog_no_position", pnl=0)
                elif d.type == "orphan_exchange":
                    log.warning("Watchdog: %s", d.detail)
                    self._adopt_orphan(d)
                elif d.type == "size_mismatch":
                    log.warning("Watchdog: %s", d.detail)
                    slot = next((s for s in self.state.slots if s.slot_id == d.slot_id), None)
                    if slot:
                        slot.entry_size = d.exchange_size
        except Exception as e:
            log.warning("Watchdog check failed: %s", e)

    def _execute_action(self, action: ApexAction) -> None:
        """Execute a single ApexAction (enter or exit)."""
        if action.action == "enter":
            self._execute_enter(action)
        elif action.action == "exit":
            self._execute_exit(action)

    def _execute_enter(self, action: ApexAction) -> None:
        """Execute an entry order."""
        slot = next((s for s in self.state.slots if s.slot_id == action.slot_id), None)
        if slot is None:
            return

        coin = instrument_to_coin(action.instrument)
        try:
            # Get current price for size calculation
            mids = self._get_all_mids()
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

            # Entry order type: directional strategies use IOC (need immediate fills
            # on fast-moving assets), pulse/radar use configured default (ALO for rebates)
            is_directional = action.source not in ("pulse_immediate", "pulse_signal", "radar")
            entry_tif = "Ioc" if is_directional else getattr(self.config, "entry_order_type", "Alo")
            fill = self.hl.place_order(
                instrument=action.instrument,
                side=side,
                size=size,  # adapter rounds to szDecimals
                price=mid,
                tif=entry_tif,
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
                slot.wallet_id = self.wallet_manager.get_default().wallet_id

                # Create Guard bridge for this slot
                self._create_guard_bridge(slot)

                # Place exchange-level SL for the new position
                guard = self.guard_bridges.get(slot.slot_id)
                if guard:
                    guard.sync_exchange_sl(self.hl, action.instrument)

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

    def _execute_exit(self, action: ApexAction) -> None:
        """Execute an exit order."""
        slot = next((s for s in self.state.slots if s.slot_id == action.slot_id), None)
        if slot is None or not slot.is_active():
            return

        coin = instrument_to_coin(action.instrument)
        try:
            mids = self._get_all_mids()
            mid = float(mids.get(coin, "0"))
            side = "sell" if slot.direction == "long" else "buy"

            # Exits always use IOC — speed > fees (includes guard CLOSE exits)
            fill = self.hl.place_order(
                instrument=action.instrument,
                side=side,
                size=slot.entry_size,
                price=mid if mid > 0 else slot.current_price,
                tif="Ioc",
                builder=self.builder,
            )

            if not fill:
                log.warning("Exit fill failed for slot %d (%s) — position still open on-chain",
                            slot.slot_id, action.instrument)
                return

            exit_price = float(fill.price)
            pnl = 0.0
            try:
                if slot.entry_price > 0 and exit_price > 0:
                    direction_sign = 1.0 if slot.direction == "long" else -1.0
                    pnl = (exit_price - slot.entry_price) * slot.entry_size * direction_sign
            except Exception as e:
                log.warning("PnL calculation failed for slot %d: %s (closing with pnl=0)", slot.slot_id, e)

            self._close_slot(slot, reason=action.reason, pnl=pnl)
            self._log_trade(
                tick=self.state.tick_count, instrument=action.instrument,
                side=side, price=float(exit_price),
                quantity=float(fill.quantity), fee=float(getattr(fill, "fee", 0)),
                pnl=pnl, meta=action.reason,
            )
            log.info("EXITED slot %d: %s %s @ %.4f PnL=$%.2f (%s)",
                     slot.slot_id, slot.direction, action.instrument,
                     exit_price, pnl, action.reason)

        except Exception as e:
            log.error("Exit failed for slot %d (%s): %s", slot.slot_id, action.instrument, e)

    def _close_slot(self, slot: ApexSlot, reason: str, pnl: float) -> None:
        """Reset a slot to empty and update PnL tracking."""
        # Capture slot snapshot BEFORE reset for archival
        slot_snapshot = slot.to_dict()

        # Cancel exchange-level SL before closing guard
        guard = self.guard_bridges.pop(slot.slot_id, None)
        if guard:
            guard.cancel_exchange_sl(self.hl, slot.instrument)
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

        # Archive closed state
        try:
            from modules.archiver import StateArchiver
            archiver = StateArchiver(archive_dir=f"{self.data_dir}/archive")
            archiver.archive_slot_snapshot(slot_snapshot, slot_snapshot.get("slot_id", 0))
            archiver.archive_guard_state(f"{self.data_dir}/guard", f"apex-slot-{slot_snapshot.get('slot_id', 0)}")
        except Exception as e:
            log.warning("Archival failed for slot %d: %s", slot_snapshot.get("slot_id", 0), e)

    def _create_guard_bridge(self, slot: ApexSlot) -> None:
        """Create a Guard bridge for a newly entered slot."""
        preset_name = self.config.guard_preset
        guard_config = GUARD_PRESETS.get(preset_name, GUARD_PRESETS.get("tight", GuardConfig()))
        guard_config = GuardConfig.from_dict(guard_config.to_dict())  # copy
        guard_config.direction = slot.direction
        guard_config.leverage = self.config.guard_leverage_override or self.config.leverage

        guard_state = GuardState.new(
            instrument=slot.instrument,
            entry_price=slot.entry_price,
            position_size=slot.entry_size,
            direction=slot.direction,
            position_id=f"apex-slot-{slot.slot_id}",
        )

        guard_store = GuardStateStore(data_dir=f"{self.data_dir}/guard")
        guard = GuardBridge(config=guard_config, state=guard_state, store=guard_store)
        self.guard_bridges[slot.slot_id] = guard

    def _log_trade(self, tick: int, instrument: str, side: str,
                   price: float, quantity: float, fee: float = 0,
                   pnl: float = 0.0, meta: str = "") -> None:
        """Append a trade record to the JSONL log."""
        self.trade_log.append({
            "tick": tick,
            "oid": f"apex-{tick}-{instrument}",
            "instrument": instrument,
            "side": side,
            "price": str(price),
            "quantity": str(quantity),
            "timestamp_ms": int(time.time() * 1000),
            "fee": str(fee),
            "pnl": str(pnl),
            "strategy": "apex",
            "meta": meta,
        })

    def _run_reflect(self) -> None:
        """Run REFLECT performance review and optionally auto-adjust config."""
        try:
            raw_trades = self.trade_log.read_all()
            if not raw_trades:
                log.info("REFLECT: no trades logged yet, skipping")
                return

            trades = [TradeRecord.from_dict(t) for t in raw_trades]
            metrics = ReflectEngine().compute(trades)

            # Log distilled summary
            summary = ReflectReporter().distill(metrics)
            log.info(summary)

            # Save report
            reflect_dir = Path(self.data_dir) / "reflect"
            reflect_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
            report = ReflectReporter().generate(metrics, date=ts)
            (reflect_dir / f"{ts}.md").write_text(report)

            # Log REFLECT review to memory
            try:
                reflect_event = self.memory_engine.create_reflect_event(
                    win_rate=metrics.win_rate,
                    net_pnl=metrics.net_pnl,
                    fdr=metrics.fdr,
                    round_trips=metrics.total_round_trips,
                    distilled=summary,
                )
                self.memory_guard.log_event(reflect_event)
            except Exception:
                pass

            # Write REFLECT report to Obsidian
            if self._obsidian_writer:
                try:
                    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    self._obsidian_writer.write_reflect_report(
                        briefing_md=report, date=date,
                        win_rate=metrics.win_rate, net_pnl=metrics.net_pnl,
                        fdr=metrics.fdr, round_trips=metrics.total_round_trips,
                    )
                except Exception:
                    pass

            # Auto-adjust if enabled and enough data
            if (self.config.reflect_auto_adjust
                    and metrics.total_round_trips >= self.config.reflect_min_round_trips):
                adjustments, adj_log = adapt(metrics, self.config)
                if adjustments:
                    apply_adjustments(adjustments, self.config)
                    log.info(adj_log)
                    # Re-sync engine with updated config
                    self.engine = ApexEngine(self.config)

                    # Log param changes to memory
                    try:
                        pc_event = self.memory_engine.create_param_change_event(
                            adjustments, metrics_summary=summary,
                        )
                        self.memory_guard.log_event(pc_event)
                    except Exception:
                        pass
                else:
                    log.info("REFLECT: no adjustments needed")

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
            log.warning("REFLECT review failed: %s", e)

    def _check_scheduled_tasks(self, now_ms: int) -> None:
        """Run time-based scheduled tasks (daily reset, REFLECT reports)."""
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

        # Scheduled REFLECT comprehensive report
        if (current_hour == self.config.reflect_report_hour
                and self._last_scheduled.get("reflect_report") != today):
            self._last_scheduled["reflect_report"] = today
            log.info("Scheduled REFLECT report (UTC %02d:00)", current_hour)
            self._run_reflect()

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
        """Print current APEX status."""
        if self.json_output:
            import json
            print(json.dumps(self.state.to_dict(), indent=2))
            return

        active = self.state.active_slots()
        print(f"\n{'='*60}")
        print(f"APEX tick #{self.state.tick_count}  |  "
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
        print("APEX SESSION SUMMARY")
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
            reflect_dir = Path(self.data_dir) / "reflect"
            reflect_dir.mkdir(parents=True, exist_ok=True)
            (reflect_dir / f"{today}-nightly.md").write_text(result.briefing_md)

            # Write findings to memory
            for finding in result.key_findings:
                event = self.memory_engine.create_reflect_event(
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
