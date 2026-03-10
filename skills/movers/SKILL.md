---
name: emerging-movers
version: 1.0.0
description: Detects assets with sudden capital inflow via OI/volume/funding proxy signals
author: Nunchi Trade
tags: [movers, detector, smart-money, signals, hyperliquid]
---

# Emerging Movers Detector

Identifies assets accelerating in capital concentration before they become
crowded positions. Uses publicly available HL market data as proxy signals
for institutional flow detection.

## Signal Types

| Signal | Trigger | Confidence |
|--------|---------|------------|
| IMMEDIATE_MOVER | OI +15% AND volume 5x surge | 100 |
| VOLUME_SURGE | Recent 4h volume > 3x average | 70 |
| OI_BREAKOUT | OI jumps 8%+ above baseline | 60 |
| FUNDING_FLIP | Funding rate reversal or 50%+ acceleration | 50 |

## Direction Classification

Majority vote across available signals:
- Funding rate sign → directional bias
- Price breakout direction
- Volume surge + price momentum

## Quality Filters

1. Erratic detection (rank bouncing → filtered)
2. Minimum 24h volume ($500K default)
3. Minimum scan history for baseline (2 scans)

## Usage

```bash
hl movers once              # Single scan
hl movers run --tick 60     # Continuous (60s intervals)
hl movers once --json       # JSON output
hl movers once --mock       # Mock data
hl movers status            # Last scan results
hl movers presets           # List presets
```

## Agent Mandate

You are the emerging movers detector. Your job is to catch capital inflow signals BEFORE the crowd. You detect timing — Radar detects setups. Together they form the WOLF entry pipeline.

RULES:
- IMMEDIATE_MOVER is the only signal strong enough for standalone entry
- All other signals require Radar confirmation (score > 170)
- ALWAYS check baseline history — signals from assets with < 2 scans are unreliable
- NEVER act on erratic assets (rank bouncing between scans)
- Report direction with every signal — directionless signals are useless

## Decision Rules

| Signal Type | Confidence | Standalone Entry? | Action |
|------------|------------|-------------------|--------|
| IMMEDIATE_MOVER | 100 | YES | Enter immediately — rare, high-conviction |
| VOLUME_SURGE | 70 | NO | Check Radar score. Enter if > 170 |
| OI_BREAKOUT | 60 | NO | Check Radar score. Enter if > 200 |
| FUNDING_FLIP | 50 | NO | Informational only — do not enter on funding alone |
| Multiple signals same asset | Varies | YES if combined > 150 | Compound conviction — enter with caution |

| Baseline State | Action |
|---------------|--------|
| < 2 scans in history | Ignore all signals — insufficient baseline |
| Erratic rank changes | Ignore — likely data noise or manipulation |
| Consistent acceleration | High confidence — act on signal |
| Signal appeared then disappeared | Stale — do not enter |

## Anti-Patterns

- **Acting on FUNDING_FLIP alone**: Funding flips are noisy and low-conviction. Only useful as confirming signal.
- **Entering on VOLUME_SURGE without Radar**: Volume surges occur on news, liquidations, and wash trading. 30% are false positives without Radar confirmation.
- **Ignoring the erratic filter**: Assets with bouncing rankings are being manipulated or have unstable liquidity. The erratic filter exists for a reason.
- **Running too frequently**: Movers needs time between scans to build baselines. Running every 10s wastes API calls without improving signal quality. Use 60s minimum.

## Error Recovery

| Error | Cause | Fix |
|-------|-------|-----|
| `No movers detected` | Quiet market | Normal — no action needed |
| `Baseline too short` | First 2 scans | Wait for more scan history to accumulate |
| `API timeout on candle fetch` | Rate limit | Increase tick interval or reduce parallel fetches |
| `Stale signal (> 5 min old)` | Delayed processing | Re-scan before acting — signal may have decayed |

## Composition

Movers is a sub-component of WOLF (runs every tick). Pairs with Radar — Radar identifies high-quality setups, Movers detects the optimal entry timing via capital flow signals. When used standalone, always cross-check with `hl radar once`.

## Cron Template

```bash
# Standalone movers scan every 60s during trading hours
* 8-20 * * 1-5 cd ~/agent-cli && hl movers once --json >> data/movers/signals.jsonl 2>&1
```
