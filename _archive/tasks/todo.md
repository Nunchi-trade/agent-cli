# Agent-CLI Roadmap

## Phase 1: P0 — Execution Layer ✅

- [x] Add `order_type` field to `StrategyDecision` (Gtc/Alo/Ioc)
- [x] MM strategies use GTC by default
- [x] Directional strategies explicitly set IOC
- [x] 318 tests passing

## Phase 2: Order Execution Reliability ✅

- [x] Order size precision (szDecimals) — lazy caching + rounding
- [x] API rate limiting — backoff + batching for PULSE scan
- [x] Keystore filename case mismatch

---

## Phase 2.5: Production Safety

### 2.5a Clearinghouse Reconciliation
- [ ] `modules/reconciliation.py` — pure bidirectional recon engine
- [ ] `skills/apex/scripts/standalone_runner.py` — replace `_watchdog()` with recon engine
- [ ] `cli/commands/apex.py` — add `hl apex reconcile [--fix]`
- [ ] Startup reconciliation in ApexRunner.__init__
- [ ] `tests/test_reconciliation.py`

### 2.5b Exchange-Level SL Sync
- [ ] `cli/hl_adapter.py` — add `place_trigger_order()`, `cancel_trigger_order()`
- [ ] `modules/guard_state.py` — add `exchange_sl_oid` field
- [ ] `modules/guard_bridge.py` — add `sync_exchange_sl()`, `cancel_exchange_sl()`
- [ ] `skills/apex/scripts/standalone_runner.py` — sync on entry/tier change, cancel on close
- [ ] `cli/engine.py` — same pattern for TradingEngine
- [ ] `skills/guard/scripts/standalone_runner.py` — same for standalone GUARD
- [ ] `tests/test_exchange_sl.py`

### 2.5c State File Archiving on Close
- [ ] `modules/archiver.py` — StateArchiver
- [ ] `skills/apex/scripts/standalone_runner.py` — auto-archive in `_close_slot()`
- [ ] `cli/engine.py` — auto-archive in `_guard_close_position()`
- [ ] `cli/commands/apex.py` — add `hl apex archive [--days N] [--dry-run]`
- [ ] `tests/test_archiver.py`

---

## Phase 3: Senpi Parity — Execution & Risk

### 3a FIRST_JUMP Signal Taxonomy (5-tier hierarchy)
- [ ] `modules/pulse_engine.py` — 5-tier classifier replacing `_detect_signals()`
- [ ] `modules/pulse_config.py` — per-tier thresholds, sector mapping
- [ ] `modules/pulse_state.py` — add `signal_tier` to PulseSignal
- [ ] `modules/apex_engine.py` — tier-based entry priority in `_evaluate_entries()`
- [ ] `modules/apex_config.py` — per-tier entry thresholds
- [ ] `tests/test_signal_taxonomy.py`

### 3b Phase 1 Auto-Cut
- [ ] `modules/trailing_stop.py` — time checks in Phase 1 evaluate()
- [ ] `modules/guard_config.py` — `phase1_max_duration_ms` (90 min), `phase1_weak_peak_ms` (45 min)
- [ ] `modules/guard_state.py` — `phase1_start_ts` field
- [ ] `modules/apex_engine.py` — handle PHASE1_TIMEOUT, WEAK_PEAK_CUT actions
- [ ] `tests/test_trailing_stop.py` — new time-based tests

### 3c ALO Fee Optimization
- [ ] `cli/hl_adapter.py` — ALO validation, cross-spread fallback to Gtc
- [ ] `skills/apex/scripts/standalone_runner.py` — ALO for entries, IOC for exits
- [ ] `modules/apex_config.py` — `entry_order_type: "Alo"`
- [ ] `modules/reflect_engine.py` — track maker vs taker fill ratio
- [ ] `tests/test_engine_strategies.py` — ALO routing tests

### 3d Rotation Cooldown
- [ ] `modules/apex_engine.py` — age check before conviction collapse + stagnation exits
- [ ] `modules/apex_state.py` — `close_ts` on slot, cooldown check in `get_empty_slot()`
- [ ] `modules/apex_config.py` — `min_hold_ms` (45 min), `slot_cooldown_ms` (5 min)
- [ ] `tests/test_apex_engine.py` — cooldown + min-hold tests

### 3e Risk Guardian Gate Machine
- [ ] `parent/risk_manager.py` — RiskGate enum (OPEN/COOLDOWN/CLOSED), state transitions
- [ ] `modules/apex_engine.py` — check risk_gate before entries
- [ ] `modules/apex_config.py` — cooldown_duration_ms, cooldown_trigger_losses
- [ ] `cli/commands/apex.py` — `hl apex risk`, `hl apex risk reset`
- [ ] `modules/reflect_engine.py` — track cooldown frequency
- [ ] `tests/test_risk_guardian.py`

---

## Phase 4: Self-Improvement

### 4a Autoresearch-Powered REFLECT
- [ ] `scripts/backtest_apex.py` — backtest harness (replay trades with config)
- [ ] `modules/apex_config.py` — `to_json()` / `from_json()` serialization
- [ ] `configs/autoresearch_program.md` — program.md template for APEX optimization
- [ ] `cli/commands/reflect.py` — `hl reflect optimize [--tag] [--metric]`
- [ ] `modules/reflect_adapter.py` — `suggest_research_directions(metrics)`
- [ ] `tests/test_backtest_harness.py`

---

## Phase 5: Architecture

### 5a Multi-Strategy Wallets
- [ ] `modules/wallet_manager.py` — wallet registry
- [ ] `cli/keystore.py` — multi-keystore support
- [ ] `modules/apex_config.py` — per-wallet config overrides
- [ ] `modules/apex_state.py` — `wallet_id` on slot
- [ ] `skills/apex/scripts/standalone_runner.py` — per-wallet HLProxy routing
- [ ] `parent/position_tracker.py` — per-wallet + house tracking
- [ ] `parent/risk_manager.py` — per-wallet + house risk limits
- [ ] `tests/test_multi_wallet.py`

---

## Phase 6 (deferred): Onboarding

- [ ] Ship or gate `quoting_engine` cleanly
- [ ] Fund builder wallet or make opt-in
- [ ] Add `--yes` flag to `hl builder approve`

## Phase 7 (deferred): DX Polish

- [ ] CI/CD pipeline (GitHub Actions: lint, test, Docker build)
- [ ] Bootstrap portability
- [ ] APEX budget-aware instrument filtering
- [ ] Live dashboard / TUI
- [ ] Backtesting mode
- [ ] Telegram/Discord notifications

---

## Execution Order

```
Week 1:  2.5a + 2.5b + 2.5c  (Safety)
Week 2:  3b + 3d + 3e         (Loss prevention)
Week 3:  3a + 3c              (Alpha refinement)
Week 4:  4a                   (Self-improvement)
Week 5:  5a                   (Architecture)
After:   Phase 6-7            (Onboarding + DX)
```

Full plan: `~/.claude/plans/concurrent-mixing-lampson.md`
