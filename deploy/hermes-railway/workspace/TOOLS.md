# Tools

## MCP Server: nunchi_trading

The primary tool provider, registered in `config.yaml` under `mcp_servers.nunchi_trading`. Exposes 13 trading tools via Model Context Protocol:

- `account` — Show HL account state (balance, margin, positions)
- `status` — Current positions, PnL, and risk state
- `trade` — Place a single order (instrument, side, size)
- `run_strategy` — Start autonomous strategy trading
- `strategies` — List all 14 available strategies
- `radar_run` — Run opportunity radar across all HL perps
- `apex_status` — Show APEX orchestrator state
- `apex_run` — Start APEX autonomous multi-slot trading
- `reflect_run` — Run REFLECT performance review
- `setup_check` — Validate environment configuration
- `builder_status` — Check builder fee approval status
- `wallet_list` — List available wallets
- `wallet_auto` — Create wallet automatically

## CLI: hl

All MCP tools are also available as CLI commands. Use the CLI for operations not exposed via MCP:

```bash
hl apex run [--preset default|conservative|aggressive] [--mainnet]
hl radar once [--mock]
hl movers once [--mock]
hl dsl run -i ETH-PERP [--preset tight]
hl reflect run [--since DATE]
hl house join <strategy> [--url URL]
```

## Hermes Built-in Toolsets

The trading MCP runs alongside Hermes's bundled toolsets. Useful ones:
- `terminal` — run shell commands (e.g. `hl ...`)
- `file` — read/write files in the workspace
- `web` — search and extract live market context
- `cronjob` — schedule recurring tasks
- `skills` — list and view installed skills (e.g. trading playbooks)

Toggle via `platform_toolsets` in `config.yaml`. The Telegram channel ships with the `hermes-telegram` preset by default.

## Shell

Available: `python3`, `node`, `git`, `rg` (ripgrep), `curl`
Not available: `jq` (use `python3 -c "import json; ..."` instead)

## Cron / Scheduling

APEX has built-in scheduling:
- Daily PnL reset at UTC midnight
- REFLECT performance review every 4 hours
- Auto-parameter adjustment based on REFLECT findings

For custom schedules, use Hermes's `cronjob` toolset (`cronjob create ...`) or the gateway's cron system.
