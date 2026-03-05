---
name: Agent Onboarding
version: 1.0.0
description: First-time setup — from zero to first trade on Hyperliquid
author: YEX
dependencies: []
---

# Agent Onboarding

Complete first-time setup for the YEX autonomous trading system on Hyperliquid. Follow every step in order. Do not skip steps. Verify each step before proceeding to the next.

## Agent Mandate

You are onboarding onto the YEX trading system. Your goal is to reach a state where you can autonomously trade on Hyperliquid (testnet or mainnet). Every step has a verification command — if verification fails, fix the issue before continuing.

RULES:
- Follow steps sequentially — each depends on the previous
- NEVER skip builder fee approval — all orders will fail without it
- ALWAYS validate with a mock trade before going live
- NEVER start on mainnet without completing testnet validation first
- Save all credentials (passwords, addresses) — they cannot be recovered

---

## Step 1: Environment Bootstrap

Install Python dependencies and verify the CLI works.

**Option A — Shell script (recommended):**
```bash
cd ~/agent-cli
bash scripts/bootstrap.sh
```

**Option B — Manual:**
```bash
cd ~/agent-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

**Verify:**
```bash
hl setup check
```

**Expected:** All items show `OK`, no `!!` issues (except missing key — that's Step 2).

**If fails:**
| Error | Fix |
|-------|-----|
| `python3 not found` | Install Python 3.9+ via `brew install python` or system package manager |
| `pip install failed` | Ensure you're in a venv: `source .venv/bin/activate` |
| `ModuleNotFoundError: hyperliquid` | Run `pip install hyperliquid-python-sdk` |

---

## Step 2: Wallet Setup

Create or import a Hyperliquid wallet.

**Decision tree:**
- Do you have a private key?
  - **YES** → Import it:
    ```bash
    hl wallet import --key 0x<your_key>
    ```
  - **NO** → Create a new one (non-interactive, with auto-saved credentials):
    ```bash
    hl wallet auto --save-env --json
    ```
    This creates the wallet, saves `HL_KEYSTORE_PASSWORD` to `~/.hl-agent/env` (auto-detected by CLI), and outputs JSON: `{"address": "...", "password": "...", "keystore": "...", "env_file": "..."}`.

**If you used `--save-env`**, the CLI auto-detects the password from `~/.hl-agent/env` — no manual export needed.

**If you did NOT use `--save-env`**, set the keystore password manually:
```bash
export HL_KEYSTORE_PASSWORD=<password>
```

**Verify:**
```bash
hl wallet list
```

**Expected:** At least one address shown.

**If fails:**
| Error | Fix |
|-------|-----|
| `No keystores found` | Run `hl wallet auto` |
| `eth_account not installed` | Run `pip install eth-account>=0.10.0` |

---

## Step 3: Network Configuration

Choose testnet (default, recommended for first run) or mainnet.

**Testnet (default):**
```bash
export HL_TESTNET=true
```

**Mainnet:**
```bash
export HL_TESTNET=false
```

**Verify:**
```bash
hl setup check
```

**Expected:** `Network: testnet` or `Network: mainnet` shown.

---

## Step 4: Fund Account

### Testnet — Claim USDyP
```bash
hl setup claim-usdyp
```

**If you get "Wallet not eligible" error:**
New wallets must connect to Hyperliquid testnet once before claiming.
1. Visit https://app.hyperliquid-testnet.xyz
2. Connect the wallet address shown by `hl wallet list`
3. Re-run `hl setup claim-usdyp`

**Verify:**
```bash
hl account
```

**Expected:** USDyP balance > 0.

### Mainnet — Deposit USDC
Deposit USDC to your Hyperliquid sub-account manually via the Hyperliquid web UI. This cannot be automated.

**Verify:**
```bash
hl account --mainnet
```

**Expected:** USDC balance > 0.

---

## Step 5: Builder Fee Approval

Approve the builder fee so your orders include revenue collection. This is a one-time on-chain approval.

**Testnet:**
```bash
hl builder approve
```

**Mainnet:**
```bash
hl builder approve --mainnet
```

**Verify:**
```bash
hl builder status
```

**Expected:** `Builder fee: 10.0 bps -> 0xF8C75F89...`

**If fails:**
| Error | Fix |
|-------|-----|
| `No private key` | Complete Step 2 |
| `insufficient funds` | Complete Step 4 |

---

## Step 6: Validate with Mock Trade

Run a strategy in mock mode to verify the full pipeline without real orders.

```bash
hl run avellaneda_mm --mock --max-ticks 3
```

**Expected:** 3 ticks execute, strategy produces decisions, no errors.

**If fails:**
| Error | Fix |
|-------|-----|
| `ModuleNotFoundError` | Run `pip install -e .` again |
| Strategy crash | Check traceback — likely missing dependency |

---

## Step 7: First Live Trade (Testnet)

Run a real strategy on testnet with a short time limit.

```bash
hl run engine_mm -i ETH-PERP --tick 15 --max-ticks 5
```

**Verify:**
```bash
hl status
```

**Expected:** Shows position or recent fills. Trades logged to `data/cli/trades.jsonl`.

---

## Step 8: WOLF Multi-Slot (Optional)

After single-strategy validation, try the full WOLF orchestrator.

```bash
hl wolf run --mock --max-ticks 5
```

Then live:
```bash
hl wolf run --max-ticks 10
```

---

## Step 9: Mainnet (When Ready)

Only after completing Steps 1-8 on testnet:

1. `export HL_TESTNET=false`
2. Deposit USDC to HL sub-account
3. `hl builder approve --mainnet`
4. `hl run engine_mm -i ETH-PERP --tick 15 --max-ticks 5 --mainnet`
5. Verify: `hl status`

---

## Anti-Patterns

- **Skipping builder fee approval** → Every order fails silently. Always approve first.
- **Going mainnet without testnet validation** → Real money at risk with unverified setup.
- **Running WOLF before single-strategy test** → WOLF composes multiple systems — if any sub-component fails, debugging is harder.
- **Ignoring password save** → Keystore password cannot be recovered. Lose it = lose wallet access.
- **Not setting HL_KEYSTORE_PASSWORD** → CLI can't auto-unlock keystore. Every command will fail.

## Complete Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HL_KEYSTORE_PASSWORD` | Yes* | Password for encrypted keystore |
| `HL_PRIVATE_KEY` | Alt* | Raw private key (alternative to keystore) |
| `HL_TESTNET` | No | `true` (default) or `false` for mainnet |
| `BUILDER_ADDRESS` | No | Override builder fee address |
| `BUILDER_FEE_TENTHS_BPS` | No | Override fee rate (default: 100 = 10 bps) |
| `ANTHROPIC_API_KEY` | No | For `claude_agent` strategy |
| `GEMINI_API_KEY` | No | For `claude_agent` with Gemini |

\* Either keystore with `HL_KEYSTORE_PASSWORD` or `HL_PRIVATE_KEY` is required.

## Composition

This skill is the entry point for all other skills. After completing onboarding:
- **Trade**: `hl run <strategy>` — see strategies with `hl strategies`
- **WOLF**: `hl wolf run` — multi-slot orchestrator
- **HOWL**: `hl howl run` — nightly performance review
- **Scanner**: `hl scanner run` — find trading opportunities
- **DSL**: `hl dsl start <instrument>` — trailing stop protection
