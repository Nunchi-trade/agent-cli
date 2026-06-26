---
name: btcswp-funding-hedge
version: 1.0.0
description: Calculate Nunchi BTCSWP funding hedge proposals through the existing MCP-exposed funding hedge calculator. Use when sizing BTC perp funding hedges, BTCSWP hedges, or Nunchi hedge proposals.
author: Nunchi Trade
tags: [btcswp, funding, hedge, btc, mcp, nunchi]
compatibility: Requires the yex-trader agent-cli MCP server with funding_hedge_info, funding_hedge_propose, and funding_hedge_backtest available.
metadata:
  platform: yex-trader
  exchange: hyperliquid
  category: funding-hedge
---

# BTCSWP Funding Hedge

Package the Nunchi BTCSWP funding hedge calculator for agents. This skill is a thin wrapper over the existing MCP tools:

- `funding_hedge_info`
- `funding_hedge_propose`
- `funding_hedge_backtest`

Do not reimplement, approximate, or edit the hedge math. The calculator lives in `modules/funding_hedge.py` and the MCP tools expose its output.

## Agent Mandate

You are sizing a read-only BTCSWP funding hedge for a BTC perp exposure. Your job is to collect the required inputs, call the Nunchi MCP tool, validate that it returned a proposal instead of an error, and present the returned hedge fields clearly.

RULES:
- Use `funding_hedge_info` when you need to discover deployed assets, supported inputs, or caveats.
- ALWAYS call `funding_hedge_propose` for a new hedge proposal.
- NEVER calculate hedge notional yourself.
- NEVER place orders from this skill. It returns sizing only.
- NEVER claim support for ETH, HYPE, SPCX, or other assets unless the MCP tool accepts them.
- ALWAYS provide either `funding_apr` or `funding_rate_8h`.
- Treat `hedge_hl_coin` as the Hyperliquid coin identifier to use in downstream execution flows.

## Proposal Inputs

Call `funding_hedge_propose` with:

```json
{
  "asset": "BTC",
  "perp_side": "long",
  "perp_notional_usd": 150000,
  "funding_apr": 42,
  "vol_multiplier": 15.0
}
```

Input rules:
- `asset`: `BTC` is deployed today.
- `perp_side`: `long` or `short`.
- `perp_notional_usd`: absolute BTC perp notional in USD.
- `funding_apr`: annualized funding APR. The tool accepts `0.42` or `42` for 42%.
- `funding_rate_8h`: optional alternative to `funding_apr`, as a decimal like `0.0003`.
- `vol_multiplier`: optional. Default is `15.0`.

## Proposal Output

The MCP tool returns JSON. If it returns `{"error": "..."}`, stop and report the error.

Key fields to present:
- `asset`
- `perp_side`
- `perp_notional_usd`
- `funding_apr`
- `hedge_market`
- `hedge_hl_coin`
- `hedge_side`
- `hedge_notional_usd`
- `vol_multiplier`
- `effective_hedged_notional_usd`
- `coverage_pct`
- `unhedged_funding_cashflow_usd_per_year`
- `target_hedge_cashflow_usd_per_year`
- `assumption`
- `disclaimer`

There is no separate hedge-card schema in this repo. If the user asks for a hedge card, format the returned MCP fields as a concise summary card without changing values:

```markdown
BTCSWP Hedge
Exposure: [perp_side] BTC perp $[perp_notional_usd]
Hedge: [hedge_side] $[hedge_notional_usd] [hedge_market] ([hedge_hl_coin])
Coverage: [coverage_pct]% via [vol_multiplier]x multiplier
Funding APR: [funding_apr as percent]
Unhedged funding: $[abs(unhedged_funding_cashflow_usd_per_year)]/yr [paying or receiving]
Target offset: $[target_hedge_cashflow_usd_per_year]/yr
Status: sizing only; no order placed
```

## Backtest

Use `funding_hedge_backtest` only when the user has a local CSV path visible to the MCP server process.

Call shape:

```json
{
  "csv_path": "/path/to/funding.csv",
  "asset": "BTC",
  "perp_side": "long",
  "perp_notional_usd": 150000,
  "vol_multiplier": 15.0
}
```

CSV requirements:
- Required funding column: `funding_rate_8h`, `perp_funding_rate_8h`, `funding_rate`, or `rate`.
- Optional BTCSWP realized hedge column: `hedge_rate_8h`, `btcswp_rate_8h`, or `btcswp_funding_rate_8h`.
- Optional timestamp column: `timestamp`, `time`, or `date`.

## CLI Fallback

Use the MCP tools when the user has a connected agent. If MCP is unavailable and the repo is installed locally, pure sizing mode is:

```bash
hl hedge propose --asset BTC --side long --perp-notional 150000 --funding-apr 42 --json
```

Passing `--perp-notional` keeps this in pure sizing mode. Without `--perp-notional`, the CLI may read account state and belongs to a separate connected-wallet flow.

## Verification Example

For `BTC`, `long`, `$150,000` perp notional, and `42%` APR, the MCP proposal should return:

- `hedge_market`: `BTCSWP-USDYP`
- `hedge_hl_coin`: `yex:BTCSWP`
- `hedge_side`: `long`
- `hedge_notional_usd`: `10000.0`
- `coverage_pct`: `100.0`
- `unhedged_funding_cashflow_usd_per_year`: `-63000.0`
- `target_hedge_cashflow_usd_per_year`: `63000.0`

