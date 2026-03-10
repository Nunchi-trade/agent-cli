---
name: wolf-strategy
version: 1.0.0
description: Autonomous multi-slot trading orchestrator
author: YEX
dependencies:
  - modules/wolf_config.py
  - modules/wolf_state.py
  - modules/wolf_engine.py
  - modules/radar_guard.py
  - modules/pulse_guard.py
  - modules/dsl_guard.py
---

# WOLF Strategy

Autonomous multi-slot trading strategy that composes Radar + Pulse + DSL into a unified orchestrator.

## Architecture

WOLF runs a single tick loop (60s base) that:

1. **Every tick**: Fetch prices, update ROEs, check DSL guards, run pulse, evaluate entry/exit
2. **Every 5 ticks** (5 min): Watchdog health check (verify positions match exchange)
3. **Every 15 ticks** (15 min): Run opportunity radar, queue high-score setups

## Slot Management

- 2-3 concurrent positions (configurable)
- Each slot: EMPTY -> ACTIVE -> CLOSED (reset to EMPTY)
- No duplicate instruments across slots
- Max 2 same-direction positions

## Entry Priority

1. Pulse IMMEDIATE_MOVER -> auto-enter
2. Radar score > 170 -> queue entry
3. Pulse other signals (confidence > 70) -> enter

## Exit Priority

1. DSL trailing stop CLOSE
2. Hard stop: ROE < -5%
3. Conviction collapse: signal gone + negative PnL for 30+ min
4. Stagnation: ROE stuck above 3% for 60+ min

## Risk Management

- Per-slot margin: total_budget / max_slots
- Daily loss limit: $500 (default)
- Daily loss trigger: close all positions immediately

## Usage

```bash
# Mock mode
hl wolf run --mock --max-ticks 10

# Live (testnet)
hl wolf run

# Live (mainnet)
hl wolf run --mainnet

# Check status
hl wolf status

# List presets
hl wolf presets
```

## Presets

- **default**: 3 slots, 10x leverage, $10K budget
- **conservative**: 2 slots, 5x leverage, higher thresholds
- **aggressive**: 3 slots, 15x leverage, lower thresholds

## Agent Mandate

You are the WOLF orchestrator. Your job is to hunt for high-probability setups and manage 2-3 concurrent positions with strict risk controls.

RULES:
- NEVER exceed `max_slots` concurrent positions
- ALWAYS check `daily_loss_limit` before entering new positions
- NEVER enter a position without Radar score > 170 OR Pulse IMMEDIATE signal
- ALWAYS run DSL trailing stop on every active position
- Exit ALL positions immediately if daily loss exceeds limit
- ALWAYS run `--mock --max-ticks 5` before first live deployment
- Log every entry/exit decision with reasoning

## Decision Rules

| Condition | Action |
|-----------|--------|
| Radar score > 200 + Pulse IMMEDIATE | Enter with 1.5x size — strongest conviction |
| Radar score > 170, no Pulse signal | Enter with 1.0x size — radar-only conviction |
| Radar score 140-170 | Queue entry, wait for Pulse confirmation within 15 min |
| Radar score < 140 | Skip — insufficient edge |
| ROE > 5% and DSL Phase 2 active | Let DSL manage exit — do not manually close |
| ROE < -3% for > 15 min | Exit — conviction lost, don't wait for hard stop |
| 2 consecutive losses same session | Reduce position size by 50% for next 2 trades |
| Daily loss > 50% of limit | Switch to conservative preset for remainder |
| All slots filled | Wait for exit before scanning new entries |
| Pulse IMMEDIATE but all slots full | Evaluate weakest slot for replacement |

## Anti-Patterns

- **Over-leveraging on volatile days**: Using aggressive preset during high-VIX or post-CPI → blown account. Use conservative preset on macro days.
- **"One more trade to recover"**: After hitting daily loss limit, entering another trade always makes it worse. Hard stop means hard stop.
- **Chasing Pulse signals alone**: Entering on VOLUME_SURGE without Radar confirmation → 60% historical loss rate. IMMEDIATE_MOVER is the only standalone entry signal.
- **Tight stops on entry**: DSL Phase 1 exists to give the trade room. Overriding with tight custom stops → premature exits on noise.
- **Running without budget cap**: Always set `--budget`. Unbounded budget = unbounded loss.

## Error Recovery

| Error | Cause | Fix |
|-------|-------|-----|
| `No positions but slots show ACTIVE` | Stale state after restart | `hl wolf status`, manually reset via state file |
| `Radar returned 0 candidates` | Low-vol period or API issue | Normal during weekends/low-vol — WOLF will idle safely |
| `Daily loss limit reached` | Bad session | WOLF auto-closes all. Review with `hl reflect run` tomorrow |
| `Builder fee not approved` | Skipped onboarding step | `hl builder approve` then restart WOLF |
| `Connection timeout` | HL API rate limit | WOLF auto-retries with backoff — no action needed |

## Composition

WOLF is the top-level orchestrator. It composes Radar (opportunity finding), Pulse (real-time signal detection), and DSL (risk management) into one tick loop. Use WOLF for autonomous trading. Use individual skills when you need manual control.

## Cron Template

```bash
# Start WOLF at market open, stop at EOD
0 8 * * 1-5  cd ~/agent-cli && source .venv/bin/activate && hl wolf run --budget 5000 >> logs/wolf.log 2>&1
0 20 * * 1-5 pkill -f "hl wolf run"
# Nightly REFLECT review
55 23 * * * cd ~/agent-cli && source .venv/bin/activate && hl reflect run >> logs/reflect.log 2>&1
```
