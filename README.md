<p align="center">
  <img src="assets/logo.png" alt="Nunchi" width="480" />
</p>

<h3 align="center">Autonomous Trading Agent for Hyperliquid</h3>

<p align="center">
  CFI funding-rate hedge &bull; MCP server &bull; Agent Skills
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
  <img src="https://img.shields.io/badge/strategy-cfi_hedge-C9A84C" alt="Strategy" />
  <img src="https://img.shields.io/badge/tests-1300%2B%20passing-brightgreen" alt="Tests" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
  <img src="https://img.shields.io/badge/MCP-20%20tools-8A2BE2" alt="MCP" />
</p>

<p align="center">
  <a href="https://railway.com/new/template?template=https://github.com/Nunchi-trade/agent-cli&envs=HL_TESTNET,RUN_MODE,DATA_DIR&HL_TESTNETDefault=true&RUN_MODEDefault=mcp&DATA_DIRDefault=/data">
    <img src="https://railway.com/button.svg" alt="Deploy on Railway" height="36" />
  </a>
</p>

---

Ship CFI v2 funding-rate hedging on [Hyperliquid](https://hyperliquid.xyz) perps and Paragon HIP-3 BTCSWP swap markets (`para:BTCSWP` mainnet, `osrs:BTCSWP` / `yex:BTCSWP` testnet). Primary surface: `hl hedge propose|execute|status|auto|backtest`. K2 inputs come from HL funding history only (no SEDA). Works as a standalone CLI, a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill, or a standalone MCP server.

Legacy operator stack (APEX, Radar, Pulse, Guard, Reflect, quoting engine, SEDA oracle) was archived 2026-07-02 under `_archive/` — see `_archive/README.md`. Hermes/OpenClaw deploy templates were removed.

---

## Quick Start

**Testnet is the default.** You do not need `--mainnet` or `HL_TESTNET=false` for local development. The CLI refuses to start if `--mainnet` and `HL_TESTNET=true` disagree.

```bash
git clone https://github.com/Nunchi-trade/agent-cli.git && cd agent-cli
bash scripts/bootstrap.sh        # Creates venv, installs, validates
```

### Agent-Friendly (Zero Prompts)

```bash
hl wallet auto --save-env        # Create wallet + save creds (no prompts)
hl setup claim-usdyp             # Claim testnet USDyP
hl builder approve               # Approve builder fee (one-time, testnet)
hl hedge propose BTC --dry-run       # Preview CFI v2 hedge proposal
hl hedge status --coin BTC           # Active hedges + drift
hl run cfi_hedge --mock --max-ticks 3   # Strategy-loop hedge agent (mock)
```

### Manual Setup (testnet)

```bash
export HL_PRIVATE_KEY=0x...
# HL_TESTNET defaults to true — omit or set explicitly

hl setup check
hl builder approve
hl hedge propose BTC
hl run cfi_hedge -i BTCSWP-PARA --mock --max-ticks 3
```

### Mainnet

Set the environment **and** pass `--mainnet` on trading commands:

```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=false

hl builder approve --mainnet
hl hedge propose BTC
hl run cfi_hedge -i BTCSWP-PARA --mock --max-ticks 3 --mainnet
```

See [Markets & Instruments](#markets--instruments) for native perps, Paragon BTCSWP, and YEX symbols.

---

## Markets & Instruments

Three instrument families — do not conflate them.

### Native Hyperliquid Perps

Standard HL perpetuals work on both networks. Examples: `ETH-PERP`, `BTC-PERP`, `SOL-PERP`. Mainnet lists hundreds of markets; no whitelist required.

```bash
# Testnet (default)
hl hedge propose BTC
hl run cfi_hedge -i BTCSWP-PARA --mock --max-ticks 3

# Mainnet
hl hedge propose BTC --mainnet
hl run cfi_hedge -i BTCSWP-PARA --mainnet --mock --max-ticks 3
```

### Paragon BTCSWP Swap Perps (HIP-3)

BTC interest-rate swap perps on separate HIP-3 dexes — **not** YEX yield markets.

| Network | Instrument | HL coin | Notes |
|---------|------------|---------|-------|
| Testnet | `BTCSWP-OSRS` | `osrs:BTCSWP` | Paragon swap perp on the `osrs` dex |
| Mainnet | `BTCSWP-PARA` | `para:BTCSWP` | Paragon swap perp on the `para` dex (when live) |

Shorthand `BTCSWP` resolves by network: testnet → `BTCSWP-USDYP` (YEX), mainnet → `BTCSWP-PARA`. Use `BTCSWP-OSRS` or `osrs:BTCSWP` when you explicitly want the Paragon **swap** perp on testnet.

```bash
# Paragon swap perp (testnet osrs dex)
hl hedge propose BTC --dry-run

# Paragon swap perp (mainnet)
export HL_TESTNET=false
hl builder approve --mainnet
hl hedge execute BTC --mainnet --dry-run
```

### YEX Yield Markets (testnet)

[Nunchi YEX](https://yex.nunchi.trade) HIP-3 yield perpetuals — volatility, rates, and YEX-denominated BTCSWP. Testnet only today.

| Instrument | HL coin | Description |
|------------|---------|-------------|
| `VXX-USDYP` | `yex:VXX` | Volatility index yield perp |
| `US3M-USDYP` | `yex:US3M` | US 3M Treasury rate yield perp |
| `BTCSWP-USDYP` | `yex:BTCSWP` | YEX yield perp (distinct from `BTCSWP-OSRS` / `BTCSWP-PARA`) |

```bash
hl hedge backtest --coin BTC --days 30
hl run cfi_hedge -i BTCSWP-USDYP --mock --max-ticks 3   # YEX yield BTCSWP
```

---

## Strategy: CFI funding-rate hedge

The product ships **one** registered strategy: **`cfi_hedge`**. Legacy MM/signal strategies were archived on 2026-07-02 under `strategies/_archive/` (see README there).

| Surface | Command | Purpose |
|---------|---------|---------|
| **Primary** | `hl hedge propose\|execute\|status\|auto\|backtest` | CFI v2 funding-cost hedge on BTCSWP |
| **Strategy loop** | `hl run cfi_hedge -i BTCSWP-PARA` | Same hedge logic inside the standard tick loop |

Math lives in `strategies/cfi_hedge.py` (pure functions). The agent wrapper is `strategies/cfi_hedge_agent.py` (`CfiHedgeAgent`). Opening a hedge sizes a **1/L** CFI v2 leg against an existing perp to neutralize floating funding while leaving the fixed K2 leg exposed.

```bash
hl hedge propose BTC                     # Show proposal (no execute)
hl hedge execute BTC --dry-run           # Preview order without signing
hl hedge status --coin BTC --watch       # Live drift + savings
hl hedge auto --coins BTC --dry-run      # Agent-controlled auto-open loop
hl hedge backtest --coin BTC --days 30   # Historical simulation
hl run cfi_hedge -i BTCSWP-PARA --mock --max-ticks 5
hl strategies                            # Registry (cfi_hedge only)
```

MCP tools: `funding_hedge_propose`, `funding_hedge_execute`, `funding_hedge_backtest`, plus `run_strategy` with `strategy=cfi_hedge`.

---

## Skills

Built on the open [Agent Skills](https://agentskills.io) standard. The active tree ships the onboarding skill for first-time local setup. The legacy operator skills were archived with their commands and modules under `_archive/skills/`.

| Skill | What it does | Install |
|-------|-------------|---------|
| **[Onboard](#onboard)** | Step-by-step first-time setup — from zero to first trade. Decision trees, verification at each step, error recovery. | [`SKILL.md`](skills/onboard/SKILL.md) |

### Install the active skill

Grab the raw URL and go:

```
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/onboard/SKILL.md
```

### Install a skill (Claude Code)

```bash
git clone https://github.com/Nunchi-trade/agent-cli.git ~/agent-cli
cd ~/agent-cli && pip install -e .
mkdir -p ~/.claude/skills/yex-trader
cp ~/agent-cli/cli/skill.md ~/.claude/skills/yex-trader/SKILL.md
```

---

## Active Operator Surface

### Onboard

First-time setup skill that walks an agent from zero to first trade in 9 steps. Decision trees at each step, verification commands, error recovery tables.

```bash
# The onboard skill automates this entire flow:
bash scripts/bootstrap.sh          # Step 1: Environment
hl wallet auto --save-env          # Step 2: Wallet
hl setup claim-usdyp               # Step 4: Fund account
hl builder approve                 # Step 5: Builder fee
hl hedge propose BTC --dry-run       # Preview CFI v2 hedge proposal
```

**[Download SKILL.md](skills/onboard/SKILL.md)**

---

### CFI hedge commands

The active trading path is CFI v2 funding-rate hedging. Use `hl hedge ...` for proposals, execution, status, automation, and backtests, or `hl run cfi_hedge ...` to run the same logic through the standard strategy loop.

```bash
hl hedge propose BTC --dry-run
hl hedge execute BTC --dry-run
hl hedge status --coin BTC --watch
hl hedge auto --coins BTC --dry-run
hl hedge backtest --coin BTC --days 30
hl run cfi_hedge -i BTCSWP-PARA --mock --max-ticks 5
```

### Archived legacy stack

APEX, Radar, Pulse, Guard, Reflect, the quoting engine, SEDA oracle, and Hermes/OpenClaw deploy templates are archived under `_archive/`. Their historical docs, commands, modules, skills, scripts, and tests remain there for reference, but those commands are not registered by `cli/main.py` and their tools are not exposed by the active MCP server.

---

## Commands

```bash
# Core trading
hl hedge propose|execute|status|auto|backtest   # CFI v2 funding hedge (primary)
hl run cfi_hedge [options]        # Hedge via strategy loop
hl status [--watch]               # Show positions, PnL, risk
hl trade <inst> <side> <size>     # Place a single order
hl account                        # Show HL account state
hl strategies                     # List all strategies
hl skills list                    # Discover installed skills

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

**20 MCP tools** for account state, trading, CFI funding hedges, wallet/setup, safety actions (`schedule_cancel`, `emergency_close_all`), order/funding reads, and agent memory/journal helpers. Run `hl mcp serve` to expose them to any MCP host.

Active tools: `strategies`, `builder_status`, `wallet_list`, `wallet_auto`, `setup_check`, `account`, `status`, `trade`, `run_strategy`, `schedule_cancel`, `emergency_close_all`, `order_status`, `funding_rates`, `funding_hedge_propose`, `funding_hedge_backtest`, `funding_hedge_execute`, `agent_memory`, `trade_journal`, `judge_report`, `obsidian_context`.

Fast tools (strategies, builder, wallet, setup, memory, journal, judge) call Python directly — zero subprocess overhead.

### Use from any agent harness

`hl mcp serve` is **harness-neutral** — [Claude Code](https://docs.anthropic.com/en/docs/claude-code), Cursor, Codex, and any MCP-compatible client consume the exact same tools. Register the server in your MCP host config:

```yaml
# Any MCP harness (e.g. ~/.cursor/mcp.json, Claude Code settings)
mcp_servers:
  nunchi_trading:
    command: python3
    args: ["-m", "cli.main", "mcp", "serve"]
    # cwd: /path/to/agent-cli   # if not launched from the repo root
```

### HTTP API & SSE

Every deployed agent also exposes an HTTP REST API and SSE real-time feed for dashboards, monitoring, and external integrations. A separate leaderboard microservice tracks agent PnL rankings.

**[Full API Reference →](docs/api-reference.md)**

---

## Deploy on Railway

The subscription product uses Railway as a **shared MCP tools runtime**, not as
a per-user autonomous-agent host. The top-level Railway template
defaults to `RUN_MODE=mcp`; Nunchi operates this runner pool behind
`mcp-gateway`, while users run their own Cursor, Claude, Codex, or local
`agent-cli` clients.

### Shared MCP Tools Runtime

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https://github.com/Nunchi-trade/agent-cli&envs=HL_TESTNET,RUN_MODE,DATA_DIR&HL_TESTNETDefault=true&RUN_MODEDefault=mcp&DATA_DIRDefault=/data)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HL_TESTNET` | No | `true` | `true` for testnet, `false` for mainnet |
| `RUN_MODE` | No | `mcp` | `mcp` for the hosted tools runtime; `strategy` for local `cfi_hedge` loops. Legacy `apex` falls back to `strategy`. |
| `DATA_DIR` | No | `/data` | Persistent ledgers, wallet state, and metering data |
| `NUNCHI_METERING_URL` | No | — | Generic web-auth metering upload endpoint when the runner reports usage |
| `NUNCHI_METERING_TOKEN` | No | — | Metering bearer token issued by web-auth/gateway config |

**Run modes:**
- **mcp** (default) — SSE MCP server for the shared Railway tools runtime.
- **strategy** — local/self-hosted `cfi_hedge` loop, not the Nunchi subscription product path. Legacy `apex`/`wolf` values are deprecated and fall back to `strategy`.

---

## Architecture

```
cli/           CLI commands and trading engine
  commands/    Active subcommands (run, hedge, wallet, setup, mcp, skills, journal, keys, margin, trading, house, policy, ...)
  mcp_server.py  MCP server (20 tools via FastMCP)
  hl_adapter.py  Direct HL API adapter (live + mock)
  builder_fee.py Builder fee config (HL native BuilderInfo)
  keystore.py    Encrypted keystore (geth-compatible)
  strategy_registry.py  Strategy + HIP-3 market definitions (YEX, OSRS, PARA)
strategies/    cfi_hedge + archived legacy strategies (_archive/)
modules/       Active shared logic modules; archived APEX/Radar/Pulse/Guard/Reflect modules live under _archive/modules/
skills/        Agent Skills (SKILL.md + runners)
  onboard/     First-time setup guide
_archive/      Legacy operator stack, skills, scripts, docs, and tests
sdk/           Strategy base class and model registry
parent/        HL API proxy, position tracking, risk management
scripts/       Backtest harness, bootstrap
tests/         Test suite (1300+ tests)
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
pytest tests/ -v                  # 1300+ tests
```

## Attribution 

Inspired by senpi and claude code.
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
