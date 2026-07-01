# Agent-CLI Autonomous Build Script

You are building the agent-cli roadmap. Work through each phase sequentially.
After each phase: run tests, commit if passing, move to next phase.
Do NOT push to GitHub — only local commits on branch `safety/phase-2.5`.

## Current state
- Phase 2.5a (Reconciliation): DONE ✅ — committed
- Branch: `safety/phase-2.5`
- All 331 tests passing

## Remaining phases to build (in order)

### Phase 2.5b: Exchange-Level SL Sync
Files to modify/create:
- `cli/hl_adapter.py` — add `place_trigger_order()` + `cancel_trigger_order()` to DirectHLProxy and DirectMockProxy
- `modules/guard_state.py` — add `exchange_sl_oid: str = ""` field to GuardState
- `modules/guard_bridge.py` — add `sync_exchange_sl(hl, instrument)` and `cancel_exchange_sl(hl, instrument)`
- `skills/apex/scripts/standalone_runner.py` — sync on entry/tier change, cancel on close, leave on shutdown
- `cli/engine.py` — same pattern for TradingEngine
- `skills/guard/scripts/standalone_runner.py` — same for standalone GUARD
- `tests/test_exchange_sl.py` — ~8 tests

HL SDK trigger order call:
```python
exchange.order(coin, is_buy, sz, limit_px,
    order_type={"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": "sl"}},
    reduce_only=True)
```

Design: Exchange SL is safety net. GUARD still runs client-side. On SIGINT/SIGTERM, LEAVE SL in place.
Sync triggers: GUARD created → place at phase1 floor; TIER_CHANGED → cancel old + place new; close → cancel; startup → place if missing.
Failure to place/cancel = warning log, does not break GUARD.

### Phase 2.5c: State File Archiving
Files:
- NEW `modules/archiver.py` — StateArchiver class
- MODIFY `skills/apex/scripts/standalone_runner.py` — call archiver in _close_slot()
- MODIFY `cli/engine.py` — call archiver in _guard_close_position()
- MODIFY `cli/commands/apex.py` — add `hl apex archive [--days N] [--dry-run]`
- NEW `tests/test_archiver.py` — ~6 tests

Archive to `data/archive/{YYYY-MM-DD}/`. Never touch trades.jsonl.

### Phase 3b: Phase 1 Auto-Cut
Files:
- MODIFY `modules/trailing_stop.py` — time checks in Phase 1 evaluate()
- MODIFY `modules/guard_config.py` — phase1_max_duration_ms (5400000), phase1_weak_peak_ms (2700000), phase1_weak_peak_min_roe (3.0)
- MODIFY `modules/guard_state.py` — phase1_start_ts field
- MODIFY `modules/apex_engine.py` — handle PHASE1_TIMEOUT and WEAK_PEAK_CUT
- MODIFY `tests/test_trailing_stop.py` — ~6 new tests

### Phase 3d: Rotation Cooldown
Files:
- MODIFY `modules/apex_engine.py` — age check before conviction collapse + stagnation
- MODIFY `modules/apex_state.py` — close_ts field, cooldown check in get_empty_slot()
- MODIFY `modules/apex_config.py` — min_hold_ms (2700000), slot_cooldown_ms (300000)
- MODIFY `tests/test_apex_engine.py` — ~6 new tests

Min hold does NOT override: GUARD exits, hard stop, daily loss. Only gates: conviction collapse, stagnation.

### Phase 3e: Risk Guardian Gate Machine
Files:
- MODIFY `parent/risk_manager.py` — RiskGate enum (OPEN/COOLDOWN/CLOSED), replace safe_mode
- MODIFY `modules/apex_engine.py` — check risk_gate before entries
- MODIFY `modules/apex_config.py` — cooldown_duration_ms, cooldown_trigger_losses, cooldown_drawdown_pct
- MODIFY `cli/commands/apex.py` — hl apex risk, hl apex risk reset
- MODIFY `modules/reflect_engine.py` — track cooldown frequency
- NEW `tests/test_risk_guardian.py` — ~10 tests

### Phase 3c: ALO Fee Optimization
Files:
- MODIFY `cli/hl_adapter.py` — ALO validation, cross-spread fallback
- MODIFY `skills/apex/scripts/standalone_runner.py` — ALO for entries
- MODIFY `modules/apex_config.py` — entry_order_type: "Alo"
- MODIFY `modules/reflect_engine.py` — track maker fill ratio
- MODIFY `tests/test_engine_strategies.py`

### Phase 3a: FIRST_JUMP Signal Taxonomy
Files:
- MODIFY `modules/pulse_engine.py` — 5-tier classifier
- MODIFY `modules/pulse_config.py` — per-tier thresholds, sector mapping
- MODIFY `modules/pulse_state.py` — signal_tier field
- MODIFY `modules/apex_engine.py` — tier-based entry priority
- MODIFY `modules/apex_config.py` — per-tier thresholds
- NEW `tests/test_signal_taxonomy.py` — ~12 tests

5-tier: FIRST_JUMP(100) > CONTRIB_EXPLOSION(95) > IMMEDIATE_MOVER(80) > NEW_ENTRY_DEEP(65) > DEEP_CLIMBER(55)

## Instructions for each phase
1. Read ALL files you need to modify before making changes
2. Write code following existing patterns (dataclass models, pure engines, guard bridges)
3. Run `python3 -m pytest tests/ -v --tb=short` after each phase
4. Only commit if ALL tests pass
5. Commit message format: `feat: <description> (Phase X.Y)`
6. After committing, move to next phase
7. Do NOT push to GitHub

## Test command
```bash
cd ~/agent-cli && python3 -m pytest tests/ -v --tb=short
```
