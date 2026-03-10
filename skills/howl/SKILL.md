---
name: howl-performance-review
version: 1.0.0
description: Hunt, Optimize, Win, Learn — nightly automated performance review
author: Nunchi Trade
tags: [howl, performance, review, analytics, self-improvement]
cron: "55 23 * * *"
dependencies:
  - modules/howl_engine.py
  - modules/howl_reporter.py
---

# HOWL — Hunt, Optimize, Win, Learn

Nightly automated performance review engine. WOLF hunts all day — HOWL reviews every trade at night, computing metrics, detecting patterns, and producing data-driven improvement recommendations.

## Architecture

```
trades.jsonl → HowlEngine.compute() → HowlMetrics → HowlReporter.generate() → report.md
```

1. **Load**: Read trade log from `data/cli/trades.jsonl`
2. **Pair**: FIFO round-trip matching (buys to sells per instrument)
3. **Compute**: Win rate, PF, FDR, holding periods, direction analysis, monster dependency
4. **Recommend**: Rule-based recommendations from metrics
5. **Report**: Full markdown report saved to `data/howl/YYYY-MM-DD.md`
6. **Distill**: 3-5 line summary for agent memory

## Key Metrics

| Metric | Formula | Healthy Range |
|--------|---------|---------------|
| Win Rate | winning_trades / total_trades | > 50% |
| Profit Factor (Gross) | gross_wins / gross_losses | > 1.5 |
| Profit Factor (Net) | (gross_wins - fees) / gross_losses | > 1.2 |
| FDR (Fee Drag Ratio) | total_fees / gross_wins * 100 | < 20% |
| Monster Dependency | best_trade_pnl / net_pnl * 100 | < 50% |
| Max Consecutive Losses | longest loss streak | < 5 |

## Usage

```bash
hl howl run                      # Review since last report
hl howl run --since 2026-03-01   # Review from specific date
hl howl report                   # View latest report
hl howl report --date 2026-03-03 # View specific date
hl howl history                  # Show metric trend over time
hl howl history -n 30            # Last 30 reports
```

## Agent Mandate

You are the HOWL reviewer. Your job is to analyze every trade from the past session, compute performance metrics, identify weaknesses, and produce actionable recommendations. You run nightly — the WOLF hunts by day, you review at night.

RULES:
- Run HOWL every night after trading stops — no exceptions
- ALWAYS read the full report before the next trading session
- Act on CRITICAL recommendations immediately (FDR > 30%, win rate < 35%)
- Track recommendations across reports — if the same issue appears 3+ times, escalate
- Save the distilled summary to agent memory for next-session context
- NEVER ignore FDR warnings — fees silently kill profitability

## Decision Rules

| Metric State | Severity | Action |
|-------------|----------|--------|
| FDR > 30% | CRITICAL | Reduce trade frequency or widen entry criteria immediately |
| FDR 20-30% | WARNING | Monitor — consider reducing size or frequency |
| FDR < 20% | OK | Fees are manageable |
| Win rate < 35% | CRITICAL | Tighten entry criteria — Radar threshold to 200+ |
| Win rate 35-45% | WARNING | Review losing trades for pattern |
| Win rate > 50% | OK | Entries are working |
| Monster dep > 60% | WARNING | One trade carrying the session — diversify alpha |
| Monster dep > 80% | CRITICAL | Fragile — entire PnL depends on one lucky trade |
| Consec losses > 5 | WARNING | Add loss streak circuit breaker to WOLF |
| Long PnL < 0, Short PnL > 0 | WARNING | Long entries are leaking — reduce long bias |
| Holding < 5 min dominates | WARNING | Over-trading — increase min hold time |

## Anti-Patterns

- **Ignoring HOWL reports**: Running WOLF without reviewing HOWL is flying blind. The same mistakes repeat.
- **Acting on single-day anomalies**: One bad day doesn't mean the strategy is broken. Look at 5+ day trends via `hl howl history`.
- **Optimizing for win rate alone**: High win rate with low profit factor means you're taking small wins and large losses. Focus on PF.
- **Not tracking FDR**: Fees are invisible during trading but compound devastatingly. FDR is the single most important "hidden" metric.
- **Changing strategy after one HOWL report**: HOWL recommendations need 3+ consistent appearances before strategy changes.

## Error Recovery

| Error | Cause | Fix |
|-------|-------|-----|
| `No trades found` | No trading activity in period | Normal — nothing to review |
| `Cannot pair round trips` | Unmatched buys/sells | Open positions — HOWL pairs only closed trades |
| `trades.jsonl not found` | First run or wrong data dir | Run at least one trade first |
| `Report generation failed` | Disk full or permissions | Check `data/howl/` directory permissions |

## Composition

HOWL is the learning layer of the WOLF system. Run HOWL nightly after WOLF stops. Feed HOWL insights back into WOLF configuration (Radar thresholds, DSL presets, position sizing). Over time, HOWL recommendations should converge as the system improves.

## Cron Template

```bash
# Nightly HOWL review at 11:55 PM
55 23 * * * cd ~/agent-cli && source .venv/bin/activate && hl howl run >> logs/howl.log 2>&1
```
