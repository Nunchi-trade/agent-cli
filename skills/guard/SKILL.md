---
name: guard-trailing-stop
description: >-
  Guard — two-phase ROE-based trailing stop system for Hyperliquid perps.
  Phase 1 lets the trade breathe with wide retrace and patient breach counting.
  Phase 2 locks profit through configurable tier ratcheting with per-tier retrace overrides.
  Supports LONG and SHORT, hard/soft breach decay, stagnation take-profit, and preset configs.
  Use when protecting an open position, setting trailing stops, or automating profit-locked exits.
license: MIT
compatibility: >-
  Requires python3 and yex-trader (agent-cli) installed.
  Hyperliquid perp positions. Direct HL API — no mcporter needed.
metadata:
  author: Nunchi Trade
  version: "1.0.0"
  platform: yex-trader
  exchange: hyperliquid
  category: risk-management
---

# Guard Trailing Stop

Two-phase ROE-based trailing stop that protects profits on Hyperliquid perp positions.

## How It Works

### Phase 1: "Let It Breathe"
- Wide retrace (3% default) from high-water mark
- Patient: requires 3 consecutive breach checks before close
- Absolute price floor caps max loss
- Goal: don't get shaken out before the trade develops

### Phase 2: "Lock the Bag"
- Tight retrace (1.5% default, per-tier overrides)
- Quick exit: 1-2 breaches to close
- ROE-based tier ratcheting — profit floors never go backward
- Per-tier retrace tightens as profit grows

### ROE-Based Tiers
All triggers use ROE (Return on Equity): `PnL / margin * 100`. At 10x leverage, 1% price move = 10% ROE.

## Usage

### Standalone — Guard an existing position
```bash
hl guard start ETH-PERP \
  --entry 2500.0 \
  --size 1.0 \
  --direction long \
  --leverage 10 \
  --preset tight \
  --tick 5
```

### Composable — Attach to any strategy
```yaml
# config.yaml
strategy: avellaneda_mm
instrument: ETH-PERP
tick_interval: 10.0
guard:
  enabled: true
  preset: tight
  leverage: 10.0
```
```bash
hl run avellaneda_mm --config config.yaml
```

### Presets

| Preset | Tiers | Stagnation TP | Use Case |
|--------|-------|---------------|----------|
| `moderate` | 6 (10/20/30/50/75/100% ROE) | No | Standard trades |
| `tight` | 4 (10/20/40/75% ROE) | Yes (8% ROE, 1hr) | Aggressive protection |

### Module API
```python
from modules.trailing_stop import TrailingStopEngine, GuardAction
from modules.guard_config import GuardConfig, PRESETS
from modules.guard_state import GuardState

config = PRESETS["tight"]
config.leverage = 10.0
config.direction = "long"

engine = TrailingStopEngine(config)
state = GuardState.new("ETH-PERP", entry_price=2500.0, position_size=1.0, direction="long")

result = engine.evaluate(price=2520.0, state=state)
# result.action: GuardAction.HOLD | .CLOSE | .TIER_CHANGED
# result.state: updated state (persist this)
# result.roe_pct, result.effective_floor, result.reason
```

## Configuration Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `direction` | `long` | Position direction: `long` or `short` |
| `leverage` | `10.0` | Position leverage (used in ROE calc) |
| `phase1_retrace` | `0.03` | Phase 1 retrace from high-water (3%) |
| `phase1_max_breaches` | `3` | Consecutive breaches before Phase 1 close |
| `phase1_absolute_floor` | `0.0` | Hard price floor (0 = disabled) |
| `phase2_retrace` | `0.015` | Default Phase 2 retrace (1.5%) |
| `phase2_max_breaches` | `2` | Default breaches in Phase 2 |
| `breach_decay_mode` | `hard` | `hard` (reset to 0) or `soft` (decay by 1) |
| `stagnation_enabled` | `false` | Enable stagnation take-profit |
| `stagnation_min_roe` | `8.0` | Min ROE% for stagnation trigger |
| `stagnation_timeout_ms` | `3600000` | HW stale time before stagnation close (1hr) |

### Tier Format
```yaml
tiers:
  - trigger_pct: 10.0    # Activate at 10% ROE
    lock_pct: 5.0         # Lock 5% ROE as floor
  - trigger_pct: 20.0
    lock_pct: 14.0
    retrace: 0.012        # Per-tier retrace override (1.2%)
    max_breaches: 1       # Per-tier breach count override
```

## Direction Reference

| Concept | LONG | SHORT |
|---------|------|-------|
| Profit | Price up | Price down |
| ROE | `(price-entry)/entry * lev * 100` | `(entry-price)/entry * lev * 100` |
| High water | Highest price | Lowest price |
| Tier floor | `entry * (1 + lock/100/lev)` | `entry * (1 - lock/100/lev)` |
| Trailing floor | `hw * (1 - retrace)` | `hw * (1 + retrace)` |
| Effective floor | `max(tier, trailing)` | `min(tier, trailing)` |
| Breach | `price <= floor` | `price >= floor` |

## Agent Mandate

You are the Guard trailing stop guardian. Your job is to protect profits and limit losses on open positions. You never make entry decisions — you only manage exits via a two-phase trailing stop system.

RULES:
- ALWAYS attach Guard to every open position — unprotected positions are unacceptable
- NEVER override a CLOSE signal — if Guard says close, close immediately
- ALWAYS use the correct direction (long/short) — wrong direction inverts all logic
- Let Phase 1 breathe — do not panic on early retrace within 3% threshold
- In Phase 2, trust the tier system — floors only ratchet up, never down
- ALWAYS verify entry price and leverage are correct before starting

## Decision Rules

| Condition | Action |
|-----------|--------|
| Guard returns `CLOSE` | Close position immediately — no hesitation |
| Guard returns `TIER_CHANGED` | Log the new tier — no action needed, floors auto-ratchet |
| Guard returns `HOLD` | Do nothing — position is healthy |
| ROE > 10% and still Phase 1 | Normal — Phase 2 triggers at first tier (usually 10% ROE) |
| Breach count incrementing but no close | Phase 1 patience — 3 consecutive breaches needed |
| Stagnation timer triggered | Close — profit is stale, capital better deployed elsewhere |

| Preset | When to Use |
|--------|-------------|
| `moderate` | Standard trades, medium hold time, balanced protection |
| `tight` | Aggressive protection, short holds, includes stagnation TP |
| Custom | Only when you understand all tier parameters — test with mock first |

## Anti-Patterns

- **Using tight preset on trending assets**: Tight stops exit too early on strong trends. Use moderate for trend-following strategies.
- **Wrong direction parameter**: Setting `direction=long` on a short position means Guard will hold losses and exit winners. Always double-check.
- **Overriding CLOSE signals**: "It'll come back" — it won't. Guard computed the exit. Trust it.
- **Starting Guard after the move**: Guard needs accurate entry price. Starting Guard at current price when entry was 2% ago means the trailing is wrong.
- **Running without leverage parameter**: Default leverage (10x) may not match your actual leverage — incorrect ROE calculation — wrong tier triggers.

## Error Recovery

| Error | Cause | Fix |
|-------|-------|-----|
| `No position found` | Position already closed or wrong instrument | Check `hl status` for actual positions |
| `Guard state stale` | Process crashed mid-tick | Restart Guard — state file preserves last known state |
| `Negative ROE but no close` | Still in Phase 1 tolerance | Normal if within 3% retrace. Hard stop at -5% ROE is separate |
| `Tier jumped from 1 to 4` | Fast price move between ticks | Normal — tiers are checked sequentially but can skip |

## Composition

Guard is a sub-component of WOLF (runs every tick per active slot). Can also be used standalone to guard any position. When used with WOLF, Guard states are managed automatically. When used standalone, you must provide entry price, size, direction, and leverage.

## Cron Template

```bash
# Guard is typically long-running (attached to a position), not cron-scheduled.
# Start manually when opening a position:
hl guard start ETH-PERP --entry 2500 --direction long --leverage 10 --preset moderate --tick 5
```
