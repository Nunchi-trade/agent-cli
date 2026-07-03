---
name: reflect-performance-review
version: 1.0.0
description: Reflect, Evaluate, Fine-tune, Learn, Evolve, Correct, Transform — nightly automated performance review
author: Nunchi Trade
tags: [reflect, performance, review, analytics, self-improvement]
cron: "55 23 * * *"
dependencies:
  - modules/reflect_engine.py
  - modules/reflect_reporter.py
---

# REFLECT — Reflect, Evaluate, Fine-tune, Learn, Evolve, Correct, Transform

Nightly automated performance review engine. APEX runs all day — REFLECT reviews every trade at night, computing metrics, detecting patterns, and producing data-driven improvement recommendations.

## Architecture

```
trades.jsonl → ReflectEngine.compute() → ReflectMetrics → ReflectReporter.generate() → report.md
```

1. **Load**: Read trade log from `data/cli/trades.jsonl`
2. **Pair**: FIFO round-trip matching (buys to sells per instrument)
3. **Compute**: Win rate, PF, FDR, holding periods, direction analysis, monster dependency
4. **Recommend**: Rule-based recommendations from metrics
5. **Report**: Full markdown report saved to `data/reflect/YYYY-MM-DD.md`
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
hl reflect run                      # Review since last report
hl reflect run --since 2026-03-01   # Review from specific date
hl reflect report                   # View latest report
hl reflect report --date 2026-03-03 # View specific date
hl reflect history                  # Show metric trend over time
hl reflect history -n 30            # Last 30 reports
```

## Agent Mandate

You are the REFLECT reviewer. Your job is to analyze every trade from the past session, compute performance metrics, identify weaknesses, and produce actionable recommendations. You run nightly — the APEX runs by day, you review at night.

RULES:
- Run REFLECT every night after trading stops — no exceptions
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
| Consec losses > 5 | WARNING | Add loss streak circuit breaker to APEX |
| Long PnL < 0, Short PnL > 0 | WARNING | Long entries are leaking — reduce long bias |
| Holding < 5 min dominates | WARNING | Over-trading — increase min hold time |

## Anti-Patterns

- **Ignoring REFLECT reports**: Running APEX without reviewing REFLECT is flying blind. The same mistakes repeat.
- **Acting on single-day anomalies**: One bad day doesn't mean the strategy is broken. Look at 5+ day trends via `hl reflect history`.
- **Optimizing for win rate alone**: High win rate with low profit factor means you're taking small wins and large losses. Focus on PF.
- **Not tracking FDR**: Fees are invisible during trading but compound devastatingly. FDR is the single most important "hidden" metric.
- **Changing strategy after one REFLECT report**: REFLECT recommendations need 3+ consistent appearances before strategy changes.

## Error Recovery

| Error | Cause | Fix |
|-------|-------|-----|
| `No trades found` | No trading activity in period | Normal — nothing to review |
| `Cannot pair round trips` | Unmatched buys/sells | Open positions — REFLECT pairs only closed trades |
| `trades.jsonl not found` | First run or wrong data dir | Run at least one trade first |
| `Report generation failed` | Disk full or permissions | Check `data/reflect/` directory permissions |

## Composition

REFLECT is the learning layer of the APEX system. Run REFLECT nightly after APEX stops. Feed REFLECT insights back into APEX configuration (Radar thresholds, DSL presets, position sizing). Over time, REFLECT recommendations should converge as the system improves.

## Cron Template

```bash
# Nightly REFLECT review at 11:55 PM
55 23 * * * cd ~/agent-cli && source .venv/bin/activate && hl reflect run >> logs/reflect.log 2>&1
```
