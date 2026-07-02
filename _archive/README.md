# Archived legacy components

**Date:** 2026-07-02  
**Reason:** Product focus narrowed to CFI v2 funding-rate hedge only (`hl hedge`, `cfi_hedge` strategy). The multi-slot APEX orchestrator, Radar/Pulse scanners, Guard trailing stops, Reflect reviews, market-making stack, SEDA oracle, and Hermes/OpenClaw Railway deploy templates are no longer maintained in the active tree.

## What moved here

| Path | Description |
|------|-------------|
| `_archive/cli/commands/` | APEX, Radar, Pulse, Guard, Reflect CLI commands |
| `_archive/modules/` | APEX/Radar/Pulse/Guard/Reflect engines and state |
| `_archive/skills/` | Agent skills for the legacy operator stack |
| `_archive/adapters/` | Venue adapter layer for APEX standalone runner |
| `_archive/quoting_engine/` | Full MM stack; includes `feeds/seda_oracle.py` (deprecated) |
| `_archive/deploy/` | Hermes + OpenClaw Railway agent gateway templates |
| `_archive/configs/` | YEX protected-MM configs (US3M, VXX, BTCSWP) |
| `_archive/execution/portfolio_risk.py` | Portfolio-level entry caps for APEX multi-slot mode |
| `_archive/scripts/` | `backtest_apex.py`, `run_protected_mm.sh` |
| `_archive/tests/` | Tests for archived modules and CLI |
| `_archive/docs/hl_feature_audit.md` | Legacy feature audit notes |
| `_archive/tasks/todo.md` | Stale task list |

## Active replacement

- **Primary product:** `hl hedge propose|execute|status|auto|backtest` and `hl run cfi_hedge`
- **K2 inputs:** Hyperliquid funding history via `strategies/cfi_funding.py` (no SEDA oracle)
- **MCP:** `funding_hedge_propose`, `funding_hedge_execute`, `funding_hedge_backtest`

## Running archived tests manually

```bash
PYTHONPATH=_archive:. pytest tests/_archive/quoting_engine/ -v
PYTHONPATH=. pytest _archive/tests/ -v
```
