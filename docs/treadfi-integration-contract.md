# TreadFi Agent/MCP Integration Contract

Status: blocked on TreadFi/Eng endpoint specs.

This document is the source-of-truth request for wiring TreadFi capabilities into
`agent-cli`. It intentionally does not define TreadFi URLs, payloads, tool names,
or auth headers. Those must come from the TreadFi/Eng contract before live client
or execution code is added.

## Existing Agent CLI Surfaces

`agent-cli` exposes two local integration paths:

- CLI: `hl`, registered by `pyproject.toml` and wired in `cli/main.py`.
- MCP: `hl mcp serve`, implemented by `cli/commands/mcp.py` and the explicit
  FastMCP tool catalog in `cli/mcp_server.py`.

Unlike newer dynamic command exporters, this MCP server does not automatically
publish every Typer command. Any TreadFi command added under `hl treadfi ...`
must also be explicitly registered as an MCP tool.

## Required TreadFi Contract

Eng/TreadFi must provide one source-of-truth contract before live wiring:

- Transport: native MCP server, REST API, WebSocket, or stdio runner.
- Environments: production, sandbox/testnet, base URLs, and health checks.
- Auth: header/query scheme, token scopes, secret names, rotation, and hosted
  agent handling for Railway/OpenClaw deployments.
- Discovery: how an agent lists supported TreadFi capabilities, markets, and
  actions.
- Market data: BTCSWP oracle/reference price, band definitions, market params,
  depth snapshots, update cadence, pagination, and sample responses.
- Campaign reporting: maker attribution, eligible fills, depth-time, leaderboard,
  cHIP/bounty status, and anti-wash flags if exposed.
- Execution, if supported: quote/order/cancel endpoints, idempotency keys,
  cloid/tagging/attribution fields, sandbox behavior, and error schema.
- Operations: rate limits, retry policy, timeout expectations, and expected
  failure modes.
- Fixtures: representative request/response payloads for every supported action.

## Initial Agent CLI Shape

Until the real contract exists, implementation should stay read-only:

- `hl treadfi spec-status`: report which contract fields are present or missing.
- `hl treadfi capabilities`: report known local capability placeholders and mark
  live discovery unavailable.
- `hl treadfi market-params`: report only confirmed, locally documented BTCSWP
  fields and mark unconfirmed values as missing.

These commands can be useful to agents because they make the blocked state
machine-readable without pretending a live TreadFi endpoint exists.

## MCP Tool Shape

After the CLI skeleton exists, mirror it in `cli/mcp_server.py` with explicit
read-only tools:

- `treadfi_spec_status`
- `treadfi_capabilities`
- `treadfi_market_params`

Each MCP tool should share the same underlying module as the CLI commands so the
CLI and agent behavior cannot drift.

## Live Wiring Gate

Do not add a live TreadFi client, credentials, trading adapter, or strategy
execution path until the contract above is delivered with fixtures. If execution
is included, add it in a separate PR behind dry-run/sandbox defaults and tests.
