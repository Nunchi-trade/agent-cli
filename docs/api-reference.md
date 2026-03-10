# API Reference — Pulling Data from Nunchi Agents

This guide covers every method for pulling data from a running Nunchi agent. Three access paths depending on your deployment and use case:

| Path | Protocol | Best For |
|------|----------|----------|
| HTTP REST API | HTTP/JSON | Dashboards, monitoring, external integrations |
| SSE Feed | Server-Sent Events | Live streaming to frontends |
| MCP Server | Model Context Protocol | AI agent orchestration (Claude, OpenClaw) |

> **Security:** All endpoints are unauthenticated. If your agent is publicly accessible, anyone with the URL can read its state. Plan your network security accordingly.

---

## Prerequisites

### Agent Deployment

Your agent must be running in one of two deployment modes:

**Self-hosted (Python entrypoint):**

```bash
# The entrypoint starts a health server on $PORT and the trading process
python scripts/entrypoint.py
```

The HTTP server binds to `0.0.0.0:$PORT` (default 8080).

**Railway / OpenClaw (Node.js entrypoint):**

```bash
# Express server with reverse proxy to OpenClaw gateway
node src/server.js
```

The Express server binds to `0.0.0.0:$PORT` (default 8080) and exposes the same API surface.

### Base URL

Throughout this document, `$AGENT_URL` refers to your agent's base URL:

```bash
# Local development
export AGENT_URL=http://localhost:8080

# Railway deployment
export AGENT_URL=https://your-agent.up.railway.app

# Verify connectivity
curl $AGENT_URL/health
```

---

## HTTP REST API

### `GET /health`

Health check endpoint. Use this to verify the agent is reachable and the trading process is alive.

```bash
curl $AGENT_URL/health
```

**Response:**

```json
{
  "status": "ok",
  "mode": "wolf",
  "uptime_s": 3842,
  "pid": 127,
  "alive": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `"ok"` if server is responding |
| `mode` | string | `RUN_MODE` env var: `"wolf"`, `"strategy"`, or `"mcp"` |
| `uptime_s` | int | Seconds since the entrypoint started |
| `pid` | int\|null | PID of the child trading process |
| `alive` | bool | Whether the child process is still running |

**Notes:**
- Used by Railway's health check system (`healthcheckPath` in `railway.toml`).
- Returns 200 even if the trading process has crashed — check `alive` to distinguish.

---

### `GET /api/status`

Primary status endpoint. Returns the agent's current state, positions, and PnL.

```bash
curl $AGENT_URL/api/status
```

**Response (WOLF mode):**

```json
{
  "status": "running",
  "engine": "wolf",
  "tick_count": 284,
  "daily_pnl": -12.50,
  "total_pnl": 342.00,
  "total_trades": 28,
  "max_slots": 3,
  "active_slots": [
    {
      "slot_id": 0,
      "instrument": "VXX-USDYP",
      "side": "long",
      "entry_size": 2.5,
      "entry_price": 30.552,
      "roe_pct": 4.2,
      "dsl_phase": 2,
      "status": "active"
    }
  ],
  "closed_slots": [],
  "positions": [
    {
      "slot": 0,
      "market": "VXX-USDYP",
      "side": "long",
      "size": 2.5,
      "entry": 30.552,
      "roe": 4.2,
      "phase": 2
    }
  ]
}
```

**Response (single-strategy mode):**

```json
{
  "status": "running",
  "engine": "engine_mm",
  "tick_count": 1042,
  "instrument": "ETH-PERP",
  "position_qty": 0.5,
  "unrealized_pnl": 3.42,
  "realized_pnl": 18.90,
  "total_orders": 312,
  "total_fills": 198
}
```

**Response (agent not running):**

```json
{
  "status": "stopped"
}
```

| Field | Mode | Type | Description |
|-------|------|------|-------------|
| `status` | Both | string | `"running"` or `"stopped"` |
| `engine` | Both | string | Strategy ID (`"wolf"`, `"engine_mm"`, etc.) |
| `tick_count` | Both | int | Number of evaluation cycles completed |
| `daily_pnl` | WOLF | float | Today's profit/loss in USD |
| `total_pnl` | WOLF | float | Cumulative profit/loss in USD |
| `total_trades` | WOLF | int | Number of executed trades |
| `max_slots` | WOLF | int | Maximum concurrent positions |
| `active_slots` | WOLF | array | Currently open position details (full slot objects) |
| `closed_slots` | WOLF | array | Last 5 closed positions |
| `positions` | WOLF | array | Simplified position array for UI consumption |
| `instrument` | Strategy | string | Trading instrument (e.g., `"ETH-PERP"`) |
| `position_qty` | Strategy | float | Net position quantity (positive = long) |
| `unrealized_pnl` | Strategy | float | Mark-to-market PnL |
| `realized_pnl` | Strategy | float | Locked PnL from closed trades |
| `total_orders` | Strategy | int | Orders placed this session |
| `total_fills` | Strategy | int | Orders that filled |

**Data source:** Reads from `$DATA_DIR/wolf/state.json` (WOLF mode) or `$DATA_DIR/cli/state.db` (strategy mode). These files are written by the trading engine after every tick.

**Fallback behavior:** If the Python status reader fails, the Node.js server reads `state.json` directly as a fallback. The Python entrypoint has no fallback.

---

### `GET /api/strategies`

Returns the full catalog of available trading strategies and YEX markets.

```bash
curl $AGENT_URL/api/strategies
```

**Response:**

```json
{
  "strategies": {
    "simple_mm": {
      "description": "Fixed-spread market maker",
      "params": { "spread_bps": 10, "base_size": 0.5 }
    },
    "avellaneda_mm": {
      "description": "Avellaneda-Stoikov inventory-aware market maker",
      "params": { "gamma": 0.1, "k": 1.5, "base_size": 0.5, "max_inventory": 5 }
    },
    "engine_mm": { "..." : "..." },
    "regime_mm": { "..." : "..." },
    "grid_mm": { "..." : "..." },
    "liquidation_mm": { "..." : "..." },
    "funding_arb": { "..." : "..." },
    "basis_arb": { "..." : "..." },
    "momentum_breakout": { "..." : "..." },
    "mean_reversion": { "..." : "..." },
    "aggressive_taker": { "..." : "..." },
    "hedge_agent": { "..." : "..." },
    "rfq_agent": { "..." : "..." },
    "claude_agent": { "..." : "..." }
  },
  "markets": {
    "VXX-USDYP": "Volatility index yield perpetual",
    "US3M-USDYP": "US 3-month Treasury rate yield perpetual",
    "BTCSWP-USDYP": "BTC interest rate swap yield perpetual"
  }
}
```

Each strategy includes its description, type, and `params` with default values. Use this to populate strategy selection UI or validate configuration inputs.

---

### `GET /status`

Human-readable plain-text status. Calls `hl wolf status` internally.

```bash
curl $AGENT_URL/status
```

**Response:**

```
WOLF Orchestrator — default preset
Tick: 284 | Daily PnL: -$12.50 | Slots: 1/3

Slot 0: VXX-USDYP LONG 2.5 @ 30.552 | ROE: +4.2% | Phase: Tier 2
Slot 1: (empty)
Slot 2: (empty)
```

Same output as running `hl wolf status` in a terminal. Useful for quick checks but not suitable for programmatic consumption — use `/api/status` instead.

---

### `POST /api/skill/install`

Verifies that the agent has the Nunchi trading CLI installed and returns the strategy count.

```bash
curl -X POST $AGENT_URL/api/skill/install \
  -H "Content-Type: application/json"
```

**Response:**

```json
{
  "installed": true,
  "strategies": 14,
  "tools": 13
}
```

**Error response:**

```json
{
  "installed": false,
  "error": "ModuleNotFoundError: No module named 'cli'"
}
```

Use this as a connectivity + capability check before wiring a UI to the agent.

---

### `POST /api/pause`

Pauses the trading process by sending `SIGSTOP` to the child process. The agent stops executing ticks but maintains all state. Positions remain open.

```bash
curl -X POST $AGENT_URL/api/pause
```

**Response:**

```json
{ "status": "paused" }
```

**Error (no process running):**

```json
{ "error": "No running agent to pause" }
```

> **Warning:** Pausing stops the DSL trailing stop from updating. If the market moves significantly while paused, positions will not be protected.

---

### `POST /api/resume`

Resumes a paused trading process by sending `SIGCONT`.

```bash
curl -X POST $AGENT_URL/api/resume
```

**Response:**

```json
{ "status": "resumed" }
```

---

### CORS

All `/api/*` endpoints return CORS headers:

```
Access-Control-Allow-Origin: * (or $CORS_ORIGIN env var)
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
```

`OPTIONS` requests to any path return `204` with these headers. Set the `CORS_ORIGIN` environment variable to restrict origins in production.

---

## SSE Real-Time Feed

### `GET /api/feed`

Server-Sent Events stream that pushes the agent's status every time the tick counter changes. The server polls the state file every 2 seconds and emits an event only when `tick_count` has advanced.

**JavaScript:**

```javascript
const source = new EventSource(`${AGENT_URL}/api/feed`);

source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`Tick ${data.tick_count}:`, data);
};

source.onerror = () => {
  console.log('SSE connection lost, will auto-reconnect');
};
```

**curl (for testing):**

```bash
curl -N $AGENT_URL/api/feed
```

**Event format:**

```
data: {"status":"running","engine":"wolf","tick_count":285,"daily_pnl":-11.20,...}

data: {"status":"running","engine":"wolf","tick_count":286,"daily_pnl":-10.80,...}
```

Each `data:` line contains the same JSON payload as `GET /api/status`. Events are separated by double newlines per the SSE specification.

**Headers returned:**

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

The `X-Accel-Buffering: no` header prevents Nginx reverse proxies from buffering the stream.

**Behavior:**
- Emits immediately on first connect (current state)
- Then emits only when `tick_count` changes (de-duplicated)
- WOLF ticks every 60 seconds by default, so events arrive roughly every 60s
- Single-strategy ticks are configurable (default 10s), so events arrive more frequently
- The connection stays open indefinitely until the client disconnects
- `EventSource` auto-reconnects on connection loss (browser default behavior)

**Python example:**

```python
import json
import requests

response = requests.get(f"{AGENT_URL}/api/feed", stream=True)
for line in response.iter_lines():
    if line:
        text = line.decode("utf-8")
        if text.startswith("data: "):
            payload = json.loads(text[6:])
            print(f"Tick {payload['tick_count']}: PnL ${payload.get('daily_pnl', 0):.2f}")
```

---

## Leaderboard API

The leaderboard runs as a **separate microservice** from the agent. It tracks registered wallet addresses and queries Hyperliquid directly for account values.

### Deployment

```bash
# From the cli-UI repo
cd deploy
docker build -t nunchi-leaderboard .
docker run -p 8090:8090 -v leaderboard-data:/data nunchi-leaderboard
```

Or deploy to Railway using the included `railway.toml`.

| Variable | Default | Description |
|----------|---------|-------------|
| `LEADERBOARD_PORT` | `8090` | HTTP server port |
| `LEADERBOARD_DB` | `/data/leaderboard.db` | SQLite database path |
| `CORS_ORIGIN` | `*` | Allowed CORS origin |

### `GET /health`

```bash
curl http://localhost:8090/health
```

```json
{ "status": "ok", "uptime_s": 1200 }
```

### `POST /api/register`

Register a wallet address for leaderboard tracking. The service queries Hyperliquid at registration time to capture the `initial_account_value` baseline.

```bash
curl -X POST http://localhost:8090/api/register \
  -H "Content-Type: application/json" \
  -d '{
    "address": "0x1234567890abcdef1234567890abcdef12345678",
    "network": "testnet",
    "display_name": "my-agent"
  }'
```

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `address` | string | Yes | Ethereum address (`0x` + 40 hex chars) |
| `network` | string | No | `"testnet"` (default) or `"mainnet"` |
| `display_name` | string | No | Display name (max 32 chars) |

**Response (new registration):**

```json
{
  "registered": true,
  "new": true,
  "address": "0x1234567890abcdef1234567890abcdef12345678",
  "network": "testnet",
  "initial_account_value": 10000.0
}
```

**Response (already registered):**

```json
{
  "registered": true,
  "new": false,
  "address": "0x1234567890abcdef1234567890abcdef12345678",
  "network": "testnet",
  "initial_account_value": 10000.0
}
```

**Error (invalid address):**

```json
{ "error": "Invalid Ethereum address (expected 0x + 40 hex chars)" }
```

**Error (HL query failed):**

```json
{ "error": "Failed to query Hyperliquid: ConnectionError(...)" }
```

> **How PnL is computed:** `PnL = current_account_value - initial_account_value`. The `initial_account_value` is captured once at registration and never updated. If the user deposits or withdraws funds after registration, the PnL figure will be incorrect. This is a known limitation.

### `GET /api/leaderboard`

Returns the ranked leaderboard for a given network.

```bash
curl "http://localhost:8090/api/leaderboard?network=testnet"
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `network` | string | `testnet` | `"testnet"` or `"mainnet"` |

**Response:**

```json
{
  "agents": [
    {
      "rank": 1,
      "address": "0xabcd...1234",
      "display_name": "alpha-wolf",
      "pnl": 842.50,
      "account_value": 10842.50,
      "positions_count": 2,
      "network": "testnet",
      "registered_at": 1709712000000
    },
    {
      "rank": 2,
      "address": "0xef01...5678",
      "display_name": "",
      "pnl": 321.00,
      "account_value": 10321.00,
      "positions_count": 1,
      "network": "testnet",
      "registered_at": 1709798400000
    }
  ],
  "total_agents": 2,
  "last_updated": 1709884800000
}
```

| Field | Type | Description |
|-------|------|-------------|
| `agents` | array | Agents sorted by PnL (descending) |
| `agents[].rank` | int | 1-indexed rank |
| `agents[].address` | string | Wallet address (lowercase) |
| `agents[].display_name` | string | User-chosen name (may be empty) |
| `agents[].pnl` | float | `current_value - initial_value` (rounded to 2 decimals) |
| `agents[].account_value` | float | Current HL account value |
| `agents[].positions_count` | int | Number of open positions on HL |
| `agents[].network` | string | `"testnet"` or `"mainnet"` |
| `agents[].registered_at` | int | Registration timestamp (Unix ms) |
| `agents[].stale` | bool | Present and `true` if the HL query failed for this agent |
| `total_agents` | int | Total registered agents on this network |
| `last_updated` | int | Cache refresh timestamp (Unix ms) |

**Caching:** The leaderboard is cached and refreshed by a background thread every 45 seconds. The `last_updated` field tells you how fresh the data is. The cache refreshes both testnet and mainnet every cycle.

**Frontend polling:**

```javascript
const LEADERBOARD_URL = 'https://leaderboard.nunchi.trade';

async function fetchLeaderboard(network = 'testnet') {
  const res = await fetch(`${LEADERBOARD_URL}/api/leaderboard?network=${network}`);
  const data = await res.json();
  return data.agents;
}

// Poll every 30 seconds
setInterval(() => fetchLeaderboard().then(renderTable), 30000);
```

**CLI usage:**

```bash
# Register an address
python leaderboard.py register 0x1234...abcd --name "my-agent" --network testnet

# List current rankings
python leaderboard.py list --network testnet

# Start the HTTP server
python leaderboard.py serve --port 8090
```

---

## MCP Server

The MCP server exposes 16 tools for AI agent orchestration via the [Model Context Protocol](https://modelcontextprotocol.io). This is the access path for Claude Code, OpenClaw, or any MCP-compatible client.

### Starting the Server

```bash
# stdio transport (for Claude Code / local AI agents)
hl mcp serve

# SSE transport (for remote connections)
hl mcp serve --transport sse
```

Or set `RUN_MODE=mcp` in your Railway deployment.

### Connecting from Claude Code

Add to your Claude Code MCP configuration:

```json
{
  "mcpServers": {
    "nunchi": {
      "command": "hl",
      "args": ["mcp", "serve"]
    }
  }
}
```

### Information Tools (Fast, sub-second)

These execute directly in Python with no subprocess overhead.

#### `strategies()`

List all 14 trading strategies with descriptions and default parameters.

```
Tool: strategies
Args: (none)
```

Returns JSON with strategy catalog and YEX markets. Same data as `GET /api/strategies`.

#### `builder_status()`

Get builder fee configuration.

```
Tool: builder_status
Args: (none)
```

Returns:

```json
{
  "enabled": true,
  "builder_address": "0x...",
  "fee_bps": 10,
  "fee_rate_tenths_bps": 100,
  "max_fee_rate_str": "0.1%"
}
```

#### `wallet_list()`

List saved encrypted keystores.

```
Tool: wallet_list
Args: (none)
```

Returns array of keystore file paths and addresses, or `"No keystores found."`.

#### `wallet_auto(save_env=true)`

Create a new Ethereum wallet non-interactively. Generates a random private key, encrypts it with a random password, and saves to `~/.hl-agent/keystore/`.

```
Tool: wallet_auto
Args: { "save_env": true }
```

Returns:

```json
{
  "address": "0x...",
  "password": "random-32-char-token",
  "keystore": "/home/user/.hl-agent/keystore/0x....json",
  "env_file": "/home/user/.hl-agent/env"
}
```

#### `setup_check()`

Validate that the environment is correctly configured for trading.

```
Tool: setup_check
Args: (none)
```

Returns:

```json
{
  "ok": [
    "hyperliquid-python-sdk installed",
    "HL_PRIVATE_KEY set",
    "Network: testnet",
    "Builder fee: 10 bps"
  ],
  "issues": [],
  "passed": true
}
```

### Action Tools (Subprocess, seconds to minutes)

These shell out to the CLI and may take significant time.

#### `account(mainnet=false)`

Get Hyperliquid account state (balances, margins, positions).

```
Tool: account
Args: { "mainnet": false }
```

Returns human-readable account summary from `hl account`.

#### `status()`

Show current positions, PnL, and risk state.

```
Tool: status
Args: (none)
```

Returns human-readable status from `hl status`.

#### `trade(instrument, side, size)`

Place a single manual order (IOC).

```
Tool: trade
Args: { "instrument": "ETH-PERP", "side": "buy", "size": 0.5 }
```

Executes `hl trade ETH-PERP buy 0.5`. Returns fill confirmation or error.

#### `run_strategy(strategy, instrument, tick, max_ticks, mock, dry_run, mainnet)`

Start autonomous trading with a named strategy.

```
Tool: run_strategy
Args: {
  "strategy": "avellaneda_mm",
  "instrument": "ETH-PERP",
  "tick": 10,
  "max_ticks": 100,
  "mock": false,
  "dry_run": false,
  "mainnet": false
}
```

This is a **long-running** call. If `max_ticks` is set, it returns after completion. Without `max_ticks`, it runs indefinitely (set a timeout on your MCP client).

#### `radar_run(mock=false)`

Run the opportunity radar once — screen all HL perps for trading setups.

```
Tool: radar_run
Args: { "mock": false }
```

Returns scored opportunities with market structure, technicals, and funding analysis.

#### `wolf_status()`

Get WOLF orchestrator status (slots, positions, daily PnL).

```
Tool: wolf_status
Args: (none)
```

#### `wolf_run(mock, max_ticks, preset, mainnet)`

Start the WOLF multi-slot orchestrator.

```
Tool: wolf_run
Args: {
  "preset": "default",
  "mock": true,
  "max_ticks": 50,
  "mainnet": false
}
```

Long-running. Timeout is computed as `max(120, max_ticks * 60 + 30)` seconds.

#### `howl_run(since=null)`

Run HOWL performance review — analyze trades, compute metrics, generate recommendations.

```
Tool: howl_run
Args: { "since": "2026-03-01" }
```

Returns detailed performance report with win rate, FDR, direction analysis, and parameter adjustment recommendations.

### Context Tools (Memory, Journal, Judge)

These access the agent's accumulated knowledge and trade records.

#### `agent_memory(query_type, limit, event_type)`

Read agent memory — learnings, parameter changes, market observations.

```
Tool: agent_memory
Args: { "query_type": "playbook", "limit": 20 }
```

| Arg | Values | Description |
|-----|--------|-------------|
| `query_type` | `"recent"`, `"playbook"` | Recent events or accumulated knowledge |
| `limit` | int | Max events (default 20) |
| `event_type` | `"param_change"`, `"howl_review"`, `"notable_trade"`, `"judge_finding"`, `"session_start"`, `"session_end"` | Filter by type |

#### `trade_journal(date, limit)`

Read trade journal — structured position records with entry/exit reasoning.

```
Tool: trade_journal
Args: { "date": "2026-03-06", "limit": 10 }
```

Returns journal entries with signal source, entry reasoning, exit reasoning, close reason, and quality rating.

#### `judge_report()`

Get latest signal quality evaluation — false positive rates, accuracy by instrument, config recommendations.

```
Tool: judge_report
Args: (none)
```

Returns the most recent Judge analysis, or `{"status": "no_reports"}` if WOLF hasn't run long enough to generate one.

#### `obsidian_context()`

Read trading context from Obsidian vault — watchlists, market theses, risk preferences.

```
Tool: obsidian_context
Args: (none)
```

Requires an Obsidian vault at `~/obsidian-vault`. Returns `{"status": "unavailable"}` if not found.

---

## Reading State Files Directly

If you have filesystem access to the agent (SSH, mounted volume, same container), you can read the state files directly without going through the API.

### WOLF State

```bash
cat $DATA_DIR/wolf/state.json | python -m json.tool
```

Contains: `tick_count`, `slots[]`, `daily_pnl`, `total_pnl`, `total_trades`, `preset`, `config`.

### Trade Log

```bash
# Last 10 trades
tail -10 $DATA_DIR/wolf/trades.jsonl
```

Each line is a JSON object:

```json
{"tick":42,"oid":"abc123","instrument":"VXX-USDYP","side":"buy","price":"30.55","quantity":"2.5","timestamp_ms":1709712000000,"fee":"0.0345","strategy":"engine_mm"}
```

Financial values are strings to preserve decimal precision.

### StateDB (SQLite)

```bash
sqlite3 $DATA_DIR/cli/state.db "SELECT key, value FROM kv"
```

Keys: `tick_count`, `positions`, `risk`, `start_time_ms`, `strategy_id`, `instrument`, `order_stats`.

### Radar Results

```bash
cat $DATA_DIR/radar/scan-history.json | python -m json.tool
```

### Movers Signals

```bash
cat $DATA_DIR/movers/scan-history.json | python -m json.tool
```

### HOWL Reports

```bash
ls $DATA_DIR/howl/

# Reports are saved as markdown files with timestamps
cat $DATA_DIR/howl/report-latest.md
```

### Journal Entries

```bash
tail -5 $DATA_DIR/journal/entries.jsonl
```

---

## Integration Patterns

### Polling Dashboard (Minimal)

```python
import time, requests

AGENT = "https://your-agent.up.railway.app"

while True:
    r = requests.get(f"{AGENT}/api/status")
    data = r.json()

    if data["status"] == "running":
        print(f"Tick {data['tick_count']} | PnL: ${data.get('daily_pnl', 0):.2f}")
        for pos in data.get("positions", []):
            print(f"  {pos['market']} {pos['side']} {pos['size']} @ {pos['entry']} → ROE {pos['roe']:.1f}%")
    else:
        print("Agent stopped")

    time.sleep(30)
```

### SSE Consumer (Real-Time)

```python
import json, requests

AGENT = "https://your-agent.up.railway.app"

with requests.get(f"{AGENT}/api/feed", stream=True) as r:
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            event = json.loads(line[6:])
            tick = event.get("tick_count", 0)
            pnl = event.get("daily_pnl", 0)
            positions = event.get("positions", [])
            print(f"[tick {tick}] PnL=${pnl:.2f} | {len(positions)} positions open")
```

### Multi-Agent Monitor

```python
import requests

agents = [
    {"name": "wolf-alpha", "url": "https://wolf-alpha.up.railway.app"},
    {"name": "wolf-beta", "url": "https://wolf-beta.up.railway.app"},
]

for agent in agents:
    try:
        r = requests.get(f"{agent['url']}/api/status", timeout=5)
        data = r.json()
        pnl = data.get("daily_pnl", data.get("realized_pnl", 0))
        print(f"{agent['name']}: {data['status']} | tick {data.get('tick_count', 0)} | PnL ${pnl:.2f}")
    except Exception as e:
        print(f"{agent['name']}: UNREACHABLE ({e})")
```

### Leaderboard + Agent Combo

```python
import requests

AGENT = "https://your-agent.up.railway.app"
LEADERBOARD = "https://leaderboard.nunchi.trade"
YOUR_ADDRESS = "0x1234...abcd"

# Get your agent status
status = requests.get(f"{AGENT}/api/status").json()

# Get leaderboard
lb = requests.get(f"{LEADERBOARD}/api/leaderboard?network=testnet").json()

# Find your rank
your_entry = next((a for a in lb["agents"] if a["address"] == YOUR_ADDRESS.lower()), None)

if your_entry:
    print(f"Rank #{your_entry['rank']} of {lb['total_agents']} | PnL: ${your_entry['pnl']:.2f}")
else:
    print("Not registered on leaderboard")
```

---

## Endpoint Summary

### Agent Endpoints (on every deployed agent)

| Method | Path | Body | Response | Latency |
|--------|------|------|----------|---------|
| `GET` | `/health` | None | JSON | <10ms |
| `GET` | `/api/status` | None | JSON | <100ms |
| `GET` | `/api/strategies` | None | JSON | <100ms |
| `GET` | `/api/feed` | None | SSE stream | Persistent |
| `GET` | `/status` | None | Plain text | <1s |
| `POST` | `/api/skill/install` | None | JSON | <2s |
| `POST` | `/api/pause` | None | JSON | <10ms |
| `POST` | `/api/resume` | None | JSON | <10ms |

### Leaderboard Endpoints (separate service)

| Method | Path | Body | Response | Latency |
|--------|------|------|----------|---------|
| `GET` | `/health` | None | JSON | <10ms |
| `GET` | `/api/leaderboard?network=` | None | JSON | <10ms (cached) |
| `POST` | `/api/register` | JSON | JSON | 1-5s (queries HL) |
| `OPTIONS` | `*` | None | 204 | <10ms |

### MCP Tools (via `hl mcp serve`)

| Tool | Type | Latency | Side Effects |
|------|------|---------|--------------|
| `strategies` | Fast | <100ms | None |
| `builder_status` | Fast | <100ms | None |
| `wallet_list` | Fast | <100ms | None |
| `wallet_auto` | Fast | <500ms | Creates keystore file |
| `setup_check` | Fast | <100ms | None |
| `account` | Subprocess | 1-5s | None |
| `status` | Subprocess | <1s | None |
| `trade` | Subprocess | 1-5s | Places order on HL |
| `run_strategy` | Subprocess | Minutes+ | Runs trading loop |
| `radar_run` | Subprocess | 10-30s | None |
| `wolf_status` | Subprocess | <1s | None |
| `wolf_run` | Subprocess | Minutes+ | Runs WOLF loop |
| `howl_run` | Subprocess | 5-15s | None |
| `agent_memory` | Fast | <100ms | None |
| `trade_journal` | Fast | <100ms | None |
| `judge_report` | Fast | <100ms | None |
| `obsidian_context` | Fast | <100ms | None |
