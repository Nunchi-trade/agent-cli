---
name: opportunity-radar
version: 1.0.0
description: Screens all Hyperliquid perps and surfaces top trading setups
author: Nunchi Trade
tags: [radar, screener, technicals, opportunities, hyperliquid]
---

# Opportunity Radar

Screens all Hyperliquid perpetual markets through a 4-stage funnel to surface
the highest-conviction trading setups.

## Architecture

```
Stage 0: BTC Macro Context (EMA 5/13 on 4h, 1h momentum)
Stage 1: Bulk Screen (~500 assets → ~70 by volume)
Stage 2: Top-N Selection (by composite liquidity score)
Stage 3: Deep Dive (multi-TF technicals + 3-pillar scoring)
Stage 4: Momentum + Final Ranking
```

## 3-Pillar Scoring (0-400 scale)

| Pillar | Weight | Signals |
|--------|--------|---------|
| Market Structure | 35% | Volume tiers, surge, OI, OI/Vol health |
| Technicals | 40% | 4h trend, hourly trend, RSI, patterns, volume |
| Funding | 25% | Neutral (+40), favorable, unfavorable penalties |

## Hard Disqualifiers

1. Counter-trend on hourly structure
2. Extreme RSI (>80 for LONG, <20 for SHORT)
3. Strong 4h counter-trend (strength > 50)
4. Volume dying on both timeframes
5. Heavy unfavorable funding (>50% annualized)
6. BTC macro headwind (modifier < -30)

## Usage

### CLI
```bash
hl radar once              # Single scan
hl radar run --tick 900    # Continuous (15 min intervals)
hl radar once --json       # JSON output
hl radar once --mock       # With mock data (no HL connection)
hl radar status            # Show last scan results
hl radar presets            # List presets
```

### Standalone
```python
from skills.radar.scripts.standalone_runner import RadarRunner
from cli.hl_adapter import DirectHLProxy

runner = RadarRunner(hl=hl, tick_interval=900)
runner.run()
```

## Configuration

Via YAML config or CLI flags:
- `--min-volume`: Minimum 24h volume to qualify (default: $500K)
- `--top-n`: Assets to deep dive (default: 20)
- `--preset`: "default" or "aggressive"
- `--score-threshold`: Minimum final score (default: 150)

## Agent Mandate

You are the opportunity radar. Your job is to screen the entire Hyperliquid perps universe and rank assets by trading conviction. You do NOT place trades — you surface setups for WOLF or the human operator.

RULES:
- ALWAYS check BTC macro context first — if headwind modifier < -30, all scores are suppressed
- NEVER recommend a disqualified asset — hard disqualifiers are absolute
- Present results sorted by score, highest first
- Include direction (LONG/SHORT) and risk factors for each candidate
- Run at minimum every 15 minutes during active trading

## Decision Rules

| Score Range | Interpretation | Action |
|-------------|---------------|--------|
| 250-400 | Elite setup — rare, strong multi-pillar confluence | Immediate entry candidate for WOLF |
| 170-250 | Good setup — solid edge | Standard entry if Movers confirms |
| 140-170 | Marginal — needs confirmation | Queue only, wait for Movers signal |
| 100-140 | Weak — one pillar carrying | Skip — insufficient edge |
| 0-100 | No edge | Ignore completely |

| BTC Macro | Effect | Action |
|-----------|--------|--------|
| Strong uptrend (mod > +20) | Tailwind for longs | Score longs normally, penalize shorts |
| Neutral (mod -10 to +10) | No macro effect | Score normally |
| Downtrend (mod < -20) | Headwind | Raise entry threshold to 200+ |
| Crash (mod < -40) | Major headwind | Skip all entries, wait for stabilization |

## Anti-Patterns

- **Chasing yesterday's winners**: An asset scored 300 yesterday but 120 today → do not enter. Scores are point-in-time.
- **Ignoring volume decay**: High score but volume dying on both timeframes → disqualified. The setup is stale.
- **Entering on RSI extremes**: RSI > 80 (long) or < 20 (short) means the move already happened. Radar correctly disqualifies these.
- **Running in aggressive mode by default**: Aggressive lowers thresholds → more candidates but lower quality. Use default unless you have excess budget.

## Error Recovery

| Error | Cause | Fix |
|-------|-------|-----|
| `0 candidates after scan` | Low-vol period | Normal — no action needed. Don't force trades. |
| `Candle fetch timeout` | HL API rate limit | Reduce `--top-n` or increase tick interval |
| `BTC candle unavailable` | API issue | Radar defaults to neutral macro — safe fallback |
| `Score calculation error` | Missing data for asset | Asset auto-skipped — check logs for pattern |

## Composition

Radar is a sub-component of WOLF (runs every 15 ticks). Can also be used standalone for manual trade selection. Pairs with Movers for confirmation — Radar finds setups, Movers detects timing.

## Cron Template

```bash
# Standalone radar every 15 min during trading hours
*/15 8-20 * * 1-5 cd ~/agent-cli && hl radar once --json >> data/radar/scans.jsonl 2>&1
```
