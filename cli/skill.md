---
name: yex-trader
description: CFI v2 funding-rate hedge on Hyperliquid BTCSWP — hl hedge propose/execute/status/auto/backtest, MCP funding_hedge_* tools, and cfi_hedge strategy loop.
user-invocable: true
argument-hint: "hedge [coin] | run cfi_hedge"
allowed-tools:
  - Bash
metadata:
  author: Nunchi Trade
  requires:
    env:
      - HL_PRIVATE_KEY
    bins:
      - python3
  primaryEnv: HL_PRIVATE_KEY
---

# YEX Trader — CFI funding-rate hedge

Autonomous Hyperliquid tooling focused on the **CFI v2 funding-cost hedge** (`cfi_hedge`). Legacy MM/signal strategies were archived 2026-07-02 under `strategies/_archive/`.

Primary surface: **`hl hedge`**. Strategy loop: **`hl run cfi_hedge`**. MCP: `funding_hedge_propose`, `funding_hedge_execute`, `funding_hedge_backtest`.

## Quick Start (Agent-Friendly)

```bash
cd ~/agent-cli
bash scripts/bootstrap.sh           # Creates venv, installs, validates
hl wallet auto --save-env             # Creates wallet, saves creds to ~/.hl-agent/env
hl setup claim-usdyp                 # Claim testnet USDyP (BTCSWP testnet)
hl builder approve                   # Approve builder fee (one-time)
hl hedge propose BTC --dry-run       # Preview hedge proposal
hl run cfi_hedge --mock --max-ticks 3
```

For full step-by-step onboarding, see `skills/onboard/SKILL.md`.

## Setup (Manual)

```bash
cd ~/agent-cli && pip install -e .
hl setup check  # Validate environment
```

### Getting Started — Testnet

1. Set your private key (or use `hl wallet auto`):
```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=true  # default
```

2. Claim testnet USDyP (required for YEX BTCSWP):
```bash
hl setup claim-usdyp
```

3. Approve builder fee (one-time):
```bash
hl builder approve
```

4. Hedge workflow:
```bash
hl hedge propose BTC                     # Show proposal
hl hedge execute BTC --dry-run           # Preview order (no signing)
hl hedge status --coin BTC               # Active hedges
hl hedge auto --coins BTC --dry-run      # Auto-open loop
hl run cfi_hedge -i BTCSWP-USDYP --mock --max-ticks 5
```

### Getting Started — Mainnet (para:BTCSWP)

```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=false
hl builder approve --mainnet
hl hedge propose BTC --mainnet
hl hedge execute BTC --mainnet --dry-run
hl run cfi_hedge -i BTCSWP-PARA --mainnet --mock --max-ticks 5
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HL_PRIVATE_KEY` | Yes* | Hyperliquid private key |
| `HL_KEYSTORE_PASSWORD` | Alt* | Password for encrypted keystore |
| `HL_TESTNET` | No | `true` (default) or `false` for mainnet |
| `BUILDER_ADDRESS` | No | Override builder fee address (default: hardcoded) |
| `BUILDER_FEE_TENTHS_BPS` | No | Override fee rate (default: 100 = 10 bps) |

\* Either `HL_PRIVATE_KEY` or a keystore with `HL_KEYSTORE_PASSWORD` is required.

## Commands

### Funding hedge (primary)

```bash
hl hedge propose [COIN] [--mainnet]
hl hedge execute [COIN] [--dry-run] [--mainnet]
hl hedge status [--coin C] [--watch]
hl hedge backtest --coin BTC [--days N]
hl hedge auto [--coins ...] [--dry-run] [--mainnet]
```

### Strategy loop

```bash
hl run cfi_hedge [-i BTCSWP-PARA|BTCSWP-USDYP|BTCSWP-OSRS] [-t TICK] [--mock] [--dry-run] [--mainnet]
hl strategies   # Registry: cfi_hedge only
```

### Supporting operator tools (legacy stack)

APEX, Radar, Pulse, Guard, and REFLECT remain available for local operators but are not part of the default product surface.

```bash
hl status [--watch]
hl account [--mainnet]
hl trade <instrument> <side> <size>
hl apex run [--mock]
hl radar once [--mock]
hl guard start ETH-PERP ...
hl reflect run
```

### MCP Server

```bash
hl mcp serve
```

Key tools: `funding_hedge_propose`, `funding_hedge_backtest`, `funding_hedge_execute`, `run_strategy` (cfi_hedge), `strategies`, `account`, `status`, `trade`.

## Strategy: cfi_hedge

| Name | Type | Description |
|------|------|-------------|
| cfi_hedge | Hedge | CFI-v2 funding-cost hedge — opens 1/L CFI v2 leg vs existing perp |

Archived strategies live in `strategies/_archive/README.md`.

## Instruments (BTCSWP)

| Network | Instrument | HL coin | Notes |
|---------|------------|---------|-------|
| Testnet (YEX) | `BTCSWP-USDYP` | `yex:BTCSWP` | YEX yield BTCSWP |
| Testnet (Paragon) | `BTCSWP-OSRS` | `osrs:BTCSWP` | Paragon swap perp |
| Mainnet | `BTCSWP-PARA` | `para:BTCSWP` | Paragon swap perp |

Shorthand `BTCSWP` resolves by network: testnet → `BTCSWP-USDYP`, mainnet → `BTCSWP-PARA`.

## Workflow

1. **Setup**: `hl setup check`
2. **Claim USDyP** (testnet): `hl setup claim-usdyp`
3. **Approve builder fee**: `hl builder approve`
4. **Preview**: `hl hedge propose BTC --dry-run`
5. **Execute** (after user approval): `hl hedge execute BTC`
6. **Monitor**: `hl hedge status --coin BTC --watch`

## Builder Fee Revenue

Set `BUILDER_ADDRESS` and `BUILDER_FEE_TENTHS_BPS` to collect fees on every trade. Users must approve once via `hl builder approve`.
