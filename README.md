<p align="center">
  <img src="assets/logo.png" alt="Nunchi" width="480" />
</p>

<h3 align="center">Autonomous Trading Agent for Hyperliquid</h3>

<p align="center">
  14 strategies &bull; WOLF multi-slot orchestrator &bull; HOWL nightly review &bull; MCP server &bull; Agent Skills
</p>

<p align="center">
  <a href="https://docs.nunchi.trade"><strong>Docs</strong></a> &nbsp;&bull;&nbsp;
  <a href="https://yex.nunchi.trade"><strong>App</strong></a> &nbsp;&bull;&nbsp;
  <a href="https://research.nunchi.trade"><strong>Research</strong></a> &nbsp;&bull;&nbsp;
  <a href="https://discord.gg/nunchi"><strong>Discord</strong></a> &nbsp;&bull;&nbsp;
  <a href="https://x.com/nunchi"><strong>X</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/strategies-14-C9A84C" alt="Strategies" />
  <img src="https://img.shields.io/badge/tests-263%20passing-brightgreen" alt="Tests" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
  <img src="https://img.shields.io/badge/MCP-16%20tools-8A2BE2" alt="MCP" />
</p>

<p align="center">
  <a href="https://railway.com/new/template?template=https://github.com/Nunchi-trade/agent-cli&envs=HL_PRIVATE_KEY,HL_TESTNET,RUN_MODE,WOLF_PRESET&HL_TESTNETDefault=true&RUN_MODEDefault=wolf&WOLF_PRESETDefault=default">
    <img src="https://railway.com/button.svg" alt="Deploy on Railway" height="36" />
  </a>
</p>

---

Ship market-making, momentum, arbitrage, and LLM-powered strategies on [Hyperliquid](https://hyperliquid.xyz) perps and [YEX](https://yex.nunchi.trade) yield markets. Full autonomous stack: DSL trailing stops, opportunity radar, emerging movers detector, WOLF orchestrator, HOWL performance review. Works as a standalone CLI, a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill, an [OpenClaw](https://agentskills.io) AgentSkill, or an MCP server.

---

## Quick Start

```bash
git clone https://github.com/Nunchi-trade/agent-cli.git && cd agent-cli
bash scripts/bootstrap.sh        # Creates venv, installs, validates
```

### Agent-Friendly (Zero Prompts)

```bash
hl wallet auto --save-env        # Create wallet + save creds (no prompts)
hl setup claim-usdyp             # Claim testnet USDyP
hl builder approve               # Approve builder fee (one-time)
hl run avellaneda_mm --mock --max-ticks 3   # Validate
hl wolf run --mock --max-ticks 5            # Full pipeline test
```

### Manual Setup

```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=true           # default

hl setup check                   # Validate environment
hl builder approve               # Approve builder fee
hl run engine_mm -i ETH-PERP --tick 10
```

### Mainnet

```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=false

hl builder approve --mainnet
hl run engine_mm -i ETH-PERP --tick 10 --mainnet
hl wolf run --mainnet
```

---

## Strategies

14 built-in strategies across four categories. Every strategy extends `BaseStrategy` with a single `on_tick()` method — no shared state, no hidden coupling between strategies.

### Market Making

Provide two-sided liquidity and earn the spread. These strategies quote bids and asks around a fair value estimate, managing inventory risk through skew and sizing adjustments.

| Strategy | Description | Key Parameters | When to Use |
|----------|-------------|----------------|-------------|
| `engine_mm` | Production quoting engine — composite 4-signal fair value, dynamic spreads (fee + vol + toxicity + event), inventory skew, multi-level quote ladder. Auto-halts on oracle staleness. *Requires `quoting_engine` module.* | `base_size`, `num_levels` | Primary MM strategy. Handles all market conditions including volatile regimes and stale data. |
| `avellaneda_mm` | Avellaneda-Stoikov optimal market maker. Reservation price adjusts with inventory; optimal spread from risk aversion `gamma` and order flow intensity `k`. Vol-bin classifier + drawdown amplifier. | `gamma`, `k`, `base_size` | When you want theoretically grounded inventory-aware quoting with well-understood parameters. |
| `regime_mm` | Vol-regime adaptive — classifies market into 4 volatility regimes (quiet/normal/volatile/extreme), switches spread width, sizing, and aggressiveness per regime. *Requires `quoting_engine` module.* | `base_size` | Volatile markets where a single spread width doesn't work. Auto-adapts without manual tuning. |
| `simple_mm` | Symmetric bid/ask quoting at fixed spread around mid. No inventory adjustment. | `spread_bps`, `size` | Testnet validation, baseline benchmarking, or low-vol stable pairs. |
| `grid_mm` | Fixed-interval grid levels above and below mid. Places N orders at equal spacing. *Requires `quoting_engine` module.* | `grid_spacing_bps`, `num_levels`, `size_per_level` | Range-bound markets where you want to accumulate and distribute across a price band. |
| `liquidation_mm` | Provides liquidity during cascade/liquidation events. Detects OI drops and widens spreads to capture forced-seller flow. *Requires `quoting_engine` module.* | `oi_drop_threshold_pct`, `cascade_spread_mult` | Liquidation-heavy markets. Only active during cascade conditions — sits idle otherwise. |

### Arbitrage

Exploit pricing dislocations across venues, instruments, or time horizons.

| Strategy | Description | Key Parameters | When to Use |
|----------|-------------|----------------|-------------|
| `funding_arb` | Cross-venue funding rate arbitrage — captures funding divergence between HL and external venues. Quoting-engine powered with bias from funding delta. *Requires `quoting_engine` module.* | `divergence_threshold_bps`, `max_bias_bps` | When funding rates diverge between venues. Works well on high-funding instruments. |
| `basis_arb` | Trades implied basis from funding rate — enters when annualized basis (contango/backwardation) exceeds threshold. | `basis_threshold_bps`, `size` | Capturing contango/backwardation dislocations. Pairs well with funding_arb. |

### Signal / Directional

Enter positions based on technical signals or momentum indicators.

| Strategy | Description | Key Parameters | When to Use |
|----------|-------------|----------------|-------------|
| `momentum_breakout` | Enters on volume + price breakout above/below N-period range. Requires both price and volume confirmation. | `lookback`, `breakout_threshold_bps`, `size` | Trending markets with clear breakout patterns. |
| `mean_reversion` | Trades when price deviates from SMA beyond a threshold. | `window`, `threshold_bps`, `size` | Range-bound markets with predictable mean-reversion behavior. |
| `aggressive_taker` | Crosses the spread with directional bias. Sinusoidal amplitude modulation. | `size`, `bias_amplitude` | When you have strong directional conviction and want immediate fills. |

### Infrastructure / Risk

Supporting strategies for portfolio management, block liquidity, and autonomous decision-making.

| Strategy | Description | Key Parameters | When to Use |
|----------|-------------|----------------|-------------|
| `hedge_agent` | Reduces excess exposure per deterministic mandate. Fires when net notional exceeds threshold. | `notional_threshold` | Always-on risk overlay. Pairs with any MM or signal strategy. |
| `rfq_agent` | Block-size dark RFQ liquidity — quotes for large orders with wider spreads. | `min_size`, `spread_bps` | Institutional/block flow. Provides hidden liquidity for large counterparties. |
| `claude_agent` | Multi-model LLM trading agent. Sends market snapshot to an LLM (Gemini, Claude, or OpenAI), receives structured trade decisions. | `model`, `base_size` | Experimental/research. Autonomous decision-making using LLM reasoning. |

### Quoting Engine Pipeline

The engine-powered strategies (`engine_mm`, `funding_arb`, `regime_mm`, `liquidation_mm`) share a common pipeline:

```
Market Data -> Composite Fair Value -> Dynamic Spread -> Inventory Skew -> Multi-Level Ladder -> Orders
               (4-signal blend)       (fee+vol+tox)     (price+size)     (exponential decay)
```

### LLM Agent (Multi-Model)

| Provider | Models | Env Variable |
|----------|--------|-------------|
| Google Gemini | `gemini-2.0-flash` (default), `gemini-2.5-pro` | `GEMINI_API_KEY` |
| Anthropic Claude | `claude-haiku-4-5-20251001`, `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o`, `gpt-4o-mini`, `o3-mini` | `OPENAI_API_KEY` |

---

## Skills

Built on the open [Agent Skills](https://agentskills.io) standard. Each skill is self-contained with instructions, scripts, and references.

| Skill | What it does | Install |
|-------|-------------|---------|
| **[Onboard](#onboard)** | Step-by-step first-time setup — from zero to first trade. Decision trees, verification at each step, error recovery. | [`SKILL.md`](skills/onboard/SKILL.md) |
| **[WOLF Strategy](#wolf--autonomous-multi-slot-strategy)** | Fully autonomous 2-3 slot trading. Composes Radar + Movers + DSL. Proven on testnet: signal detection, entry, trailing stop, exit. | [`SKILL.md`](skills/wolf/SKILL.md) |
| **[Opportunity Radar](#radar--opportunity-radar)** | 4-stage funnel screening all HL perps. Scores 0-400 across market structure, technicals, funding, and BTC macro. | [`SKILL.md`](skills/radar/SKILL.md) |
| **[Emerging Movers](#movers--emerging-movers-detector)** | Detects sudden capital inflow via OI delta, volume surge, funding flips. IMMEDIATE signals at 100 confidence. | [`SKILL.md`](skills/movers/SKILL.md) |
| **[DSL (Dynamic Stop Loss)](#dsl--dynamic-stop-loss)** | 2-phase trailing stop with tiered profit-locking. ROE-based triggers that auto-account for leverage. | [`SKILL.md`](skills/dsl/SKILL.md) |
| **[HOWL](#howl--performance-review)** | Nightly self-improvement loop. Analyzes every trade, finds patterns, generates actionable recommendations. | [`SKILL.md`](skills/howl/SKILL.md) |

### Install a skill (agents)

Grab the raw URL and go:

```
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/onboard/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/wolf/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/radar/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/movers/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/dsl/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/howl/SKILL.md
```

### Install a skill (OpenClaw / ClawHub)

```bash
clawhub install nunchi-trade/yex-trader
```

### Install a skill (Claude Code)

```bash
git clone https://github.com/Nunchi-trade/agent-cli.git ~/agent-cli
cd ~/agent-cli && pip install -e .
mkdir -p ~/.claude/skills/yex-trader
cp ~/agent-cli/cli/skill.md ~/.claude/skills/yex-trader/SKILL.md
```

---

## Autonomous Trading Stack

### Onboard

First-time setup skill that walks an agent from zero to first trade in 9 steps. Decision trees at each step, verification commands, error recovery tables.

```bash
# The onboard skill automates this entire flow:
bash scripts/bootstrap.sh          # Step 1: Environment
hl wallet auto --save-env          # Step 2: Wallet
hl setup claim-usdyp               # Step 4: Fund account
hl builder approve                 # Step 5: Builder fee
hl run avellaneda_mm --mock --max-ticks 3  # Step 6: Validate
```

**[Download SKILL.md](skills/onboard/SKILL.md)**

---

### DSL — Dynamic Stop Loss

Trailing stop system with tiered profit-locking. Protects profits while letting winners run.

**Two phases:**
- **Phase 1 (Let it breathe)** — Wide retrace tolerance while position builds
- **Phase 2 (Lock the bag)** — Tiered profit floors that ratchet up as ROE grows

| Preset | Phase 1 Retrace | Tiers | Stagnation TP |
|--------|----------------|-------|---------------|
| `moderate` | 3% | 6 tiers (10-100% ROE) | No |
| `tight` | 5% | 4 tiers (10-75% ROE) | Yes (8% ROE, 1h) |

```bash
hl dsl run -i ETH-PERP --preset tight
```

**[Download SKILL.md](skills/dsl/SKILL.md)**

---

### Radar — Opportunity Radar

Multi-factor screening engine that evaluates all HL perps for trade setups. 4-stage funnel, scores 0-400.

| Pillar | Weight | Signals |
|--------|--------|---------|
| Market Structure | 35 | Volume, OI, liquidity |
| Technicals | 30 | RSI, EMA, patterns, hourly trend |
| Funding | 20 | Rate extremes, direction bias |
| BTC Macro | 15 | Trend alignment, regime filter |

```bash
hl radar once --mock    # Single scan
hl radar run --mock     # Continuous (every 15 min)
```

**[Download SKILL.md](skills/radar/SKILL.md)**

---

### Movers — Emerging Movers Detector

Detects assets with sudden capital inflow using OI, volume, funding, and price signals. Runs every 60 seconds.

| Signal | Trigger | Confidence |
|--------|---------|------------|
| `IMMEDIATE_MOVER` | OI +15% AND volume 5x surge | 100 |
| `VOLUME_SURGE` | 4h volume / average > 3x | 70 |
| `OI_BREAKOUT` | OI jumps 8%+ above baseline | 60 |
| `FUNDING_FLIP` | Funding rate reverses or accelerates 50%+ | 50 |

```bash
hl movers once --mock     # Single scan
hl movers run --mock      # Continuous (every 60s)
```

**[Download SKILL.md](skills/movers/SKILL.md)**

---

### WOLF — Autonomous Multi-Slot Strategy

The top-level orchestrator. Composes Radar + Movers + DSL into a single autonomous strategy managing 2-3 concurrent positions.

**Tick schedule** (60s base):
- Every tick: Fetch prices, update ROEs, check DSL, run movers, evaluate entry/exit
- Every 5 ticks: Watchdog health check
- Every 15 ticks: Run opportunity radar

**Entry priority:**

| Priority | Source | Condition |
|----------|--------|-----------|
| 1 | Movers IMMEDIATE | Auto-enter on compound OI + volume signal |
| 2 | Radar | Score > 170 |
| 3 | Movers signal | Confidence > 70 |

**Presets:**

| Preset | Slots | Leverage | Radar Threshold | Daily Loss Limit |
|--------|-------|----------|-------------------|------------------|
| `default` | 3 | 10x | 170 | $500 |
| `conservative` | 2 | 5x | 190 | $250 |
| `aggressive` | 3 | 15x | 150 | $1,000 |

```bash
hl wolf run --mock --max-ticks 10          # Mock test
hl wolf run                                 # Live testnet
hl wolf run --preset conservative --mainnet # Live mainnet
```

**[Download SKILL.md](skills/wolf/SKILL.md)**

---

### HOWL — Performance Review

Nightly self-improvement loop. Reads trade history, computes metrics, detects patterns, generates actionable recommendations.

| Metric | Description |
|--------|-------------|
| Win Rate | % of round trips with positive net PnL |
| FDR | Fee Drag Ratio — fees as % of gross wins |
| Direction Split | Long vs short win rates and PnL |
| Holding Periods | Bucketed by <5m, 5-15m, 15-60m, 1-4h, 4h+ |
| Monster Dependency | % of net PnL from best single trade |

```bash
hl howl run --since 2026-03-01
hl howl report
hl howl history -n 10
```

**[Download SKILL.md](skills/howl/SKILL.md)**

### HOWL Self-Improvement Loop

When running inside WOLF, HOWL executes automatically every 240 ticks (~4 hours) and at a configurable UTC hour (default 04:00). It reads the trade log, computes performance metrics, and **auto-adjusts WOLF parameters** based on findings:

| Finding | Automatic Adjustment |
|---------|---------------------|
| FDR > 30% (fees eating profits) | Raise radar threshold, disable immediate mover entries |
| Win rate < 40% | Tighten both radar and movers confidence thresholds |
| 5+ consecutive losses | Reduce daily loss limit by 20% |
| Direction imbalance (e.g. longs losing) | Limit same-direction slots |
| Fees exceed gross PnL | **Emergency mode**: disable auto-entries, raise all thresholds |
| Profitable + healthy | Slightly relax thresholds toward defaults |

All adjustments have guardrail bounds — parameters can't swing wildly. Disable with `howl_auto_adjust: false` in WOLF config.

**Scheduled tasks** (built into WOLF tick loop):
- **Daily PnL reset** at UTC midnight — clears daily loss tracking
- **HOWL comprehensive report** at UTC 04:00 — full performance review with markdown report saved to `data/wolf/howl/`

---

## Commands

```bash
# Core trading
hl run <strategy> [options]       # Start autonomous trading
hl status [--watch]               # Show positions, PnL, risk
hl trade <inst> <side> <size>     # Place a single order
hl account                        # Show HL account state
hl strategies                     # List all strategies
hl skills list                    # Discover installed skills

# Autonomous stack
hl wolf run [options]             # WOLF multi-slot orchestrator
hl radar run [options]            # Opportunity radar
hl movers run [options]           # Emerging movers detector
hl dsl run -i ETH-PERP [options] # DSL trailing stop
hl howl run [--since DATE]        # Performance review

# Infrastructure
hl builder approve [--mainnet]    # Approve builder fee
hl wallet auto [--save-env]       # Create wallet (agent-friendly)
hl setup check                    # Validate environment
hl setup bootstrap                # Auto-setup venv + install
hl setup claim-usdyp              # Claim testnet USDyP
hl mcp serve                      # Start MCP server
```

---

## MCP Server

Expose all trading tools via [Model Context Protocol](https://modelcontextprotocol.io) for AI agent integration.

```bash
hl mcp serve                      # stdio transport (default)
hl mcp serve --transport sse      # SSE transport
```

**16 tools exposed:** `account`, `status`, `trade`, `run_strategy`, `strategies`, `radar_run`, `wolf_status`, `wolf_run`, `howl_run`, `setup_check`, `builder_status`, `wallet_list`, `wallet_auto`, `agent_memory`, `trade_journal`, `judge_report`

Fast tools (strategies, builder, wallet, setup, memory, journal, judge) call Python directly — zero subprocess overhead.

### HTTP API & SSE

Every deployed agent also exposes an HTTP REST API and SSE real-time feed for dashboards, monitoring, and external integrations. A separate leaderboard microservice tracks agent PnL rankings.

**[Full API Reference →](docs/api-reference.md)**

---

## Deploy on Railway

Two deployment options: **headless** (WOLF runs strategies directly) or **OpenClaw agent** (conversational AI trading assistant with Telegram).

### Option A: Headless WOLF (Deterministic)

One-click deploy to run WOLF autonomously. No AI model needed — pure deterministic strategy execution.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https://github.com/Nunchi-trade/agent-cli&envs=HL_PRIVATE_KEY,HL_TESTNET,RUN_MODE,WOLF_PRESET&HL_TESTNETDefault=true&RUN_MODEDefault=wolf&WOLF_PRESETDefault=default)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HL_PRIVATE_KEY` | Yes | — | Your Hyperliquid private key |
| `HL_TESTNET` | No | `true` | `true` for testnet, `false` for mainnet |
| `RUN_MODE` | No | `wolf` | `wolf`, `strategy`, or `mcp` |
| `WOLF_PRESET` | No | `default` | `conservative`, `default`, or `aggressive` |

**Run modes:**
- **wolf** (default) — WOLF multi-slot orchestrator with autonomous entry, exit, DSL trailing stops, and HOWL self-improvement loop
- **strategy** — Single strategy loop (set `STRATEGY=engine_mm`, `avellaneda_mm`, etc.)
- **mcp** — MCP server for AI agent integration (SSE transport)

### Option B: OpenClaw Agent (Conversational AI)

One-click deploy of a full OpenClaw agent that uses our CLI as the tool backend. Talk to your trading bot via Telegram — it scans markets, enters trades, manages risk, and learns from its mistakes.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https://github.com/Nunchi-trade/agent-cli/tree/main/deploy/openclaw-railway&envs=HL_PRIVATE_KEY,AI_PROVIDER,AI_API_KEY,TELEGRAM_BOT_TOKEN,TELEGRAM_USERNAME,HL_TESTNET&HL_TESTNETDefault=true)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HL_PRIVATE_KEY` | Yes | — | Your Hyperliquid private key |
| `AI_PROVIDER` | Yes | — | `anthropic`, `openai`, `gemini`, or `openrouter` |
| `AI_API_KEY` | Yes | — | API key for the chosen AI provider |
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram bot token (from @BotFather) |
| `TELEGRAM_USERNAME` | Yes | — | Your Telegram @username |
| `HL_TESTNET` | No | `true` | `true` for testnet, `false` for mainnet |

**What you get:**
- OpenClaw gateway with web UI at `/openclaw`
- Telegram integration — chat with your bot to start/stop trading, run scans, check status
- Our 13 MCP trading tools as the agent's primary capabilities
- Persistent state across redeploys via `/data` volume
- Auto-onboard: bot sends "Agent ready" to Telegram on first deploy
- HOWL self-improvement: the agent analyzes its own trades and adjusts strategy parameters

**How it works:**
1. Deploy sets up OpenClaw + our `hl mcp serve` as the tool provider
2. Bot auto-configures Telegram and sends you a ready message
3. Tell it "start trading" → it runs WOLF with autonomous entry, exit, and risk management
4. Ask "how did we do?" → it runs HOWL and reports performance metrics
5. The agent reads workspace files (AGENTS.md, SOUL.md) that define its trading behavior

Both options persist state via Railway volume at `/data` — WOLF state, HOWL reports, radar history, and agent memory survive redeploys.

---

## YEX Yield Markets

[YEX](https://yex.nunchi.trade) (Nunchi HIP-3) yield perpetuals on Hyperliquid:

| Instrument | HL Coin | Description |
|------------|---------|-------------|
| VXX-USDYP | yex:VXX | Volatility index yield perp |
| US3M-USDYP | yex:US3M | US 3M Treasury rate yield perp |
| BTCSWP-USDYP | yex:BTCSWP | BTC interest rate swap yield perp — tracks the BTC-denominated swap curve |

```bash
hl run avellaneda_mm -i VXX-USDYP --tick 15
hl run funding_arb -i US3M-USDYP --tick 30
hl run engine_mm -i BTCSWP-USDYP --tick 10
```

---

## Architecture

```
cli/           CLI commands and trading engine
  commands/    Subcommand modules (run, wolf, radar, movers, dsl, howl, house, ...)
  mcp_server.py  MCP server (16 tools via FastMCP)
  hl_adapter.py  Direct HL API adapter (live + mock)
  builder_fee.py Builder fee config (HL native BuilderInfo)
  keystore.py    Encrypted keystore (geth-compatible)
  strategy_registry.py  Strategy + YEX market definitions
strategies/    14 trading strategy implementations
modules/       Pure logic modules (zero I/O)
  wolf_engine.py     WOLF decision engine
  radar_engine.py    Opportunity radar
  movers_engine.py   Emerging movers detector
  trailing_stop.py   DSL trailing stop
  howl_engine.py     Performance analysis
skills/        Agent Skills (SKILL.md + runners)
  onboard/     First-time setup guide
  wolf/        WOLF orchestrator
  radar/       Opportunity radar
  movers/      Emerging movers
  dsl/         Dynamic stop loss
  howl/        Performance review
sdk/           Strategy base class and model registry
parent/        HL API proxy, position tracking, risk management
tests/         Test suite (263 tests)
```

---

## Custom Strategies

Create a Python file that subclasses `BaseStrategy`:

```python
from sdk.strategy_sdk.base import BaseStrategy
from common.models import MarketSnapshot, StrategyDecision

class MyStrategy(BaseStrategy):
    def __init__(self, lookback=10, threshold=0.5, size=0.1, **kwargs):
        super().__init__(strategy_id="my_strategy")
        self.lookback, self.threshold, self.size = lookback, threshold, size
        self._prices = []

    def on_tick(self, snapshot, context=None):
        mid = snapshot.mid_price
        self._prices.append(mid)
        if len(self._prices) < self.lookback:
            return []

        pct = (mid - self._prices[-self.lookback]) / self._prices[-self.lookback] * 100
        if abs(pct) > self.threshold:
            return [StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="buy" if pct > 0 else "sell",
                size=self.size,
                limit_price=round(snapshot.ask if pct > 0 else snapshot.bid, 2),
            )]
        return []
```

```bash
hl run my_strategies.my_strategy:MyStrategy -i ETH-PERP --tick 10
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HL_PRIVATE_KEY` | Yes* | Hyperliquid private key |
| `HL_KEYSTORE_PASSWORD` | Alt* | Password for encrypted keystore |
| `HL_TESTNET` | No | `true` (default) or `false` for mainnet |
| `BUILDER_ADDRESS` | No | Override builder fee address |
| `BUILDER_FEE_TENTHS_BPS` | No | Override fee rate (default: 100 = 10 bps) |
| `ANTHROPIC_API_KEY` | No | For `claude_agent` with Claude |
| `GEMINI_API_KEY` | No | For `claude_agent` with Gemini |
| `OPENAI_API_KEY` | No | For `claude_agent` with OpenAI |

\* Either `HL_PRIVATE_KEY` or a keystore with `HL_KEYSTORE_PASSWORD` is required.

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v                  # 263 tests
```

## Attribution 

Inspired by openclaw, senpi, and claude code. 
---

## Links

- **Docs** — [docs.nunchi.trade](https://docs.nunchi.trade)
- **YEX App** — [yex.nunchi.trade](https://yex.nunchi.trade)
- **Research** — [research.nunchi.trade](https://research.nunchi.trade)
- **Discord** — [discord.gg/nunchi](https://discord.gg/nunchi)
- **X** — [@nunchi](https://x.com/nunchi)
- **GitHub** — [Nunchi-trade](https://github.com/Nunchi-trade)
- **Agent Skills Standard** — [agentskills.io](https://agentskills.io)

---

<p align="center">
  <sub>Built by <a href="https://nunchi.trade">Nunchi</a> &bull; MIT License</sub>
</p>
