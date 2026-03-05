---
name: yex-trader
description: Autonomous Hyperliquid trading — 14 strategies (MM, momentum, arbitrage, LLM) with WOLF multi-slot orchestrator, HOWL performance review, DSL trailing stops, and builder fee revenue collection.
user-invocable: true
argument-hint: "<strategy> [options]"
allowed-tools:
  - Bash
metadata:
  openclaw:
    requires:
      env:
        - HL_PRIVATE_KEY
      bins:
        - python3
    primaryEnv: HL_PRIVATE_KEY
---

# YEX Trader

Autonomous Hyperliquid trading via agent-cli. 14 strategies across market making, momentum, arbitrage, and LLM-powered trading. WOLF multi-slot orchestrator. HOWL nightly performance review. Builder fee revenue collection.

## Quick Start (Agent-Friendly)

```bash
cd ~/agent-cli
bash scripts/bootstrap.sh           # Creates venv, installs, validates
hl wallet auto --save-env             # Creates wallet, saves creds to ~/.hl-agent/env
hl setup claim-usdyp                 # Claim testnet USDyP
hl builder approve                   # Approve builder fee (one-time)
hl run avellaneda_mm --mock --max-ticks 3  # Validate
hl run engine_mm -i ETH-PERP --tick 15 --max-ticks 5  # First live trade
```

For full step-by-step onboarding, see `skills/onboard/SKILL.md`.

## Setup (Manual)

```bash
cd ~/agent-cli && pip install -e .
hl setup check  # Validate environment
```

### Getting Started — YEX Testnet

1. Set your private key (or use `hl wallet auto`):
```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=true  # default
```

2. Claim testnet USDyP (required for YEX markets):
```bash
hl setup claim-usdyp
```

3. Approve builder fee (one-time):
```bash
hl builder approve
```

4. Start trading:
```bash
hl run avellaneda_mm -i VXX-USDYP --tick 15          # YEX yield market
hl run engine_mm -i ETH-PERP --tick 10                # Standard perp
hl wolf run --mock --max-ticks 5                       # WOLF multi-slot
```

### Getting Started — Mainnet

1. Set your private key and network:
```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=false
```

2. Approve builder fee (one-time):
```bash
hl builder approve --mainnet
```

3. Start trading:
```bash
hl run engine_mm -i ETH-PERP --tick 10 --mainnet      # ETH perp
hl run avellaneda_mm -i BTC-PERP --tick 10 --mainnet   # BTC perp
hl wolf run --mainnet                                   # WOLF multi-slot
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HL_PRIVATE_KEY` | Yes* | Hyperliquid private key |
| `HL_KEYSTORE_PASSWORD` | Alt* | Password for encrypted keystore |
| `HL_TESTNET` | No | `true` (default) or `false` for mainnet |
| `BUILDER_ADDRESS` | No | Override builder fee address (default: hardcoded) |
| `BUILDER_FEE_TENTHS_BPS` | No | Override fee rate (default: 100 = 10 bps) |
| `ANTHROPIC_API_KEY` | No | For `claude_agent` strategy |
| `GEMINI_API_KEY` | No | For `claude_agent` with Gemini |

\* Either `HL_PRIVATE_KEY` or a keystore with `HL_KEYSTORE_PASSWORD` is required.

## Commands

### Core Trading

```bash
# Start autonomous trading
hl run <strategy> [-i INSTRUMENT] [-t TICK] [--config FILE] [--mainnet] [--dry-run] [--mock] [--max-ticks N]

# Single manual order
hl trade <instrument> <side> <size>

# Account info
hl account [--mainnet]

# Check positions and PnL
hl status [--watch] [--interval 5]

# List all strategies
hl strategies
```

### WOLF Multi-Slot Orchestrator

```bash
hl wolf run [-t 60] [--preset conservative|default|aggressive] [--mock] [--budget 1000] [--slots 5]
hl wolf once [--mock]
hl wolf status
hl wolf presets
```

### HOWL Performance Review

```bash
hl howl run [--since 2026-03-01] [--data-dir data/cli]
hl howl report [--date 2026-03-03]
hl howl history [-n 10]
```

### Dynamic Stop Loss (DSL)

```bash
hl dsl start <instrument> [--entry-price 2500] [--direction long] [--preset tight|standard|wide]
hl dsl check <instrument>
hl dsl status
hl dsl presets
```

### Scanner & Movers

```bash
hl scanner run [--top 10] [--min-score 7.0]
hl scanner history [-n 5]
hl movers run [--top 10]
```

### Builder Fee

```bash
hl builder status
hl builder approve [--mainnet]
```

### Wallet (Encrypted Keystore)

```bash
hl wallet auto                       # Non-interactive wallet creation (agent-friendly)
hl wallet create                     # Interactive wallet creation
hl wallet import --key <hex>
hl wallet list
hl wallet export [--address 0x...]
```

### Environment Setup

```bash
hl setup check                       # Validate environment
hl setup bootstrap                   # Auto-create venv and install
hl setup claim-usdyp                 # Claim testnet USDyP tokens
```

### MCP Server

```bash
hl mcp serve                         # Start MCP server (stdio transport)
hl mcp serve --transport sse         # Start MCP server (SSE transport)
```

### TEE Clearing House

```bash
hl house join [--mainnet]
hl house status
```

## Strategies (14)

| Name | Type | Description |
|------|------|-------------|
| simple_mm | MM | Symmetric bid/ask quoting around mid |
| avellaneda_mm | MM | Inventory-aware Avellaneda-Stoikov model |
| engine_mm | MM | Production quoting engine — composite FV, dynamic spreads, multi-level ladder |
| regime_mm | MM | Vol-regime adaptive — switches behavior by volatility regime (calm/normal/volatile/extreme) |
| grid_mm | MM | Fixed-interval grid levels above and below mid |
| liquidation_mm | MM | Provides liquidity during cascade/liquidation events |
| funding_arb | Arb | Cross-venue funding rate arbitrage |
| basis_arb | Arb | Trades implied basis from funding rate (contango/backwardation) |
| mean_reversion | Signal | Trades when price deviates from SMA |
| momentum_breakout | Signal | Enters on volume + price breakout above/below N-period range |
| aggressive_taker | Taker | Directional spread crossing with bias |
| hedge_agent | Risk | Reduces excess exposure per deterministic mandate |
| rfq_agent | RFQ | Block-size dark RFQ liquidity |
| claude_agent | LLM | Claude/Gemini-powered autonomous trading agent |

## Instruments

- **Standard perps**: ETH-PERP, BTC-PERP, SOL-PERP, etc.
- **YEX yield markets**: VXX-USDYP (yex:VXX), US3M-USDYP (yex:US3M)

## Workflow

1. **Setup**: `hl setup check`
2. **Claim USDyP** (testnet only): `hl setup claim-usdyp`
3. **Approve builder fee**: `hl builder approve` (testnet) or `hl builder approve --mainnet`
4. **Mock test**: `hl run avellaneda_mm --mock --max-ticks 5`
5. **Dry run**: `hl run engine_mm --dry-run --max-ticks 10`
6. **Live testnet**: `hl run engine_mm -i ETH-PERP --tick 10`
7. **Live mainnet**: `hl run engine_mm -i ETH-PERP --tick 10 --mainnet`
8. **WOLF mode**: `hl wolf run --mainnet` or `hl wolf run --mock --max-ticks 5`
9. **Monitor**: `hl status --watch`
10. **Review**: `hl howl run`

## Builder Fee Revenue

Set `BUILDER_ADDRESS` and `BUILDER_FEE_TENTHS_BPS` to collect fees on every trade. Users must approve once via `hl builder approve`. Fee is collected natively by Hyperliquid — no extra gas, no contract calls.

## HOWL Self-Improvement

Run `hl howl run` after a trading session. HOWL computes win rate, fee drag ratio (FDR), direction analysis, holding period buckets, monster trade dependency, and generates actionable recommendations. Reports saved to `data/howl/`.
