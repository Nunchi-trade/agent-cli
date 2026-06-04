# Tools

## MCP Server: nunchi_trading

The primary tool provider. Reach it through the **mcporter** skill — it is registered
as an mcporter stdio server (not a native gateway tool). Usage:

```bash
mcporter list nunchi_trading --schema      # list the server's tools + input schemas
mcporter call nunchi_trading.account       # call a tool (no args)
mcporter call nunchi_trading.trade instrument=ETH-PERP side=buy size=0.1
```

Exposes 13 trading tools via Model Context Protocol:

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

## Shell

Available: `python3`, `node`, `git`, `rg` (ripgrep), `curl`
Not available: `jq` (use `python3 -c "import json; ..."` instead)

## Cron / Scheduling

APEX has built-in scheduling:
- Daily PnL reset at UTC midnight
- REFLECT performance review every 4 hours
- Auto-parameter adjustment based on REFLECT findings

For custom schedules, use the gateway's cron system.
