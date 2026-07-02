# Archived legacy components

**Date:** 2026-07-02  
**Reason:** Product focus narrowed to CFI v2 funding-rate hedge only (`hl hedge`, `cfi_hedge` strategy). Market-making stack, SEDA oracle, and legacy agent deploy templates are no longer maintained in the active tree.

## What moved here

| Path | Description |
|------|-------------|
| `archive/quoting_engine/` | Full market-making stack (fair value, spreads, ladders, toxicity, funding feeds). Includes `feeds/seda_oracle.py` (deprecated). |
| `archive/deploy/hermes-railway/` | Hermes agent gateway Railway template |
| `archive/deploy/openclaw-railway/` | OpenClaw agent gateway Railway template |

## Active replacement

Hedge K2 inputs now come from **Hyperliquid funding only** via `strategies/cfi_funding.py` — no SEDA oracle.

## Running archived tests manually

```bash
PYTHONPATH=archive:. pytest tests/_archive/quoting_engine/ -v
```
