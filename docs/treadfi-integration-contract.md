# Tread.fi Agent CLI Integration Plan

Status: public Tread.fi REST docs reviewed; implementation should proceed as a
REST client integration, not as a native MCP-server integration.

This document specifies how `agent-cli` should wire Tread.fi capabilities into
the existing `hl` CLI and FastMCP tool catalog. It focuses on the public REST API
surface documented at `docs.tread.fi` and keeps write operations behind explicit
safety gates.

## Source Docs

Public Tread.fi docs establish these integration facts:

- API access requires an API token requested from Tread.fi.
- The REST examples use a configurable `SERVER_URL` with an `/api/` prefix.
- Examples and API reference cover accounts, balances, single orders,
  multi-orders, simple orders, algorithmic orders, and risk/admin endpoints.
- Order lifecycle endpoints include list, active, submit, get, cancel, pause,
  resume, amend, and cancel/pause/resume-all.
- Simple orders use `POST /api/orders/` with strategies `Market`, `Limit`,
  `IOC`, or `Iceberg`.
- Algorithmic orders use `POST /api/orders/` with strategies such as `TWAP`,
  `VWAP`, `POV`, `IS`, and `Target Time`.
- Hyperliquid account linking is performed through Tread.fi Key Management in
  the web app. Tread.fi supports Hyperliquid, including HIP-3, once the account
  is connected there.
- The Market Maker Bot docs describe the product flow but do not clearly expose
  a dedicated bot-management REST endpoint.

Important ambiguity: most API docs say `Authorization: Token <API_KEY>`, while a
few example snippets say `Authorization: Bearer <token>`. The client should
default to `Token` but make the auth scheme configurable until confirmed with
Tread.fi.

## Existing Agent CLI Surfaces

`agent-cli` exposes two local integration paths:

- CLI: `hl`, registered by `pyproject.toml` and wired in `cli/main.py`.
- MCP: `hl mcp serve`, implemented by `cli/commands/mcp.py` and the explicit
  FastMCP tool catalog in `cli/mcp_server.py`.

Unlike dynamic command exporters, this MCP server does not automatically publish
every Typer command. Any Tread.fi command added under `hl treadfi ...` must also
be explicitly registered as an MCP tool.

## Configuration

Add a small Tread.fi config module that resolves:

- `TREADFI_API_BASE_URL`: required for live calls. No production URL should be
  hardcoded because the docs use a configurable server URL.
- `TREADFI_API_TOKEN`: required for live calls.
- `TREADFI_AUTH_SCHEME`: optional, defaults to `Token`.
- `TREADFI_TIMEOUT_SECONDS`: optional, defaults to a conservative short timeout.
- `TREADFI_DRY_RUN`: optional, defaults to true for write commands.

Secrets should be read from environment variables only. Do not reuse
`HL_PRIVATE_KEY`; Tread.fi auth is a platform API token, not a Hyperliquid
signing key.

## Client Layer

Add a typed REST wrapper, for example `modules/treadfi_client.py`, with:

- `TreadFiConfig.from_env()`
- `TreadFiClient`
- `TreadFiError`
- request helpers for JSON responses and structured error messages

Initial read methods:

- `list_accounts()` -> `GET /api/accounts/`
- `get_balances(account_names=None)` -> `GET /api/balances/`
- `list_orders(...)` -> `GET /api/orders/`
- `list_active_orders()` -> `GET /api/active_orders/`
- `get_order(order_id)` -> `GET /api/order/{id}`
- `get_order_summary(order_id)` -> `GET /api/order_summary/{id}`
- `list_multi_orders(...)` -> `GET /api/multi_orders/`
- `get_multi_order(order_id, include_child_orders=False)` ->
  `GET /api/multi_order/{id}`

Write methods should exist only after the read client is covered by tests:

- `submit_order(payload)` -> `POST /api/orders/`
- `cancel_order(order_id)` -> `DELETE /api/order/{id}`
- `pause_order(order_id)` -> `POST /api/pause_order/`
- `resume_order(order_id)` -> `POST /api/resume_order/`
- `amend_order(payload)` -> `POST /api/amend_order/`
- `cancel_all_orders(...)` -> `POST /api/cancel_all_orders/`

Do not implement admin/risk-limit endpoints in the first pass. Those require
staff/admin permissions and should live in a separate operator-only PR.

## CLI Shape

Add `cli/commands/treadfi.py` and register it in `cli/main.py`:

Read-only commands:

- `hl treadfi accounts ls`
- `hl treadfi balances [--account NAME]`
- `hl treadfi orders ls [--active] [--status STATUS] [--account NAME]`
- `hl treadfi orders show ORDER_ID [--summary]`
- `hl treadfi multi-orders ls`
- `hl treadfi multi-orders show ORDER_ID [--children]`

Write commands, all dry-run by default:

- `hl treadfi orders submit --payload FILE [--execute]`
- `hl treadfi orders cancel ORDER_ID [--execute]`
- `hl treadfi orders pause ORDER_ID [--execute]`
- `hl treadfi orders resume ORDER_ID [--execute]`
- `hl treadfi orders amend --payload FILE [--execute]`

The first write interface should accept a JSON payload file rather than many CLI
flags. Tread.fi order payloads have strategy-specific fields, and a payload file
keeps the CLI honest while tests lock down validation. Friendly typed flags can
be added later for common cases like TWAP and Limit.

## MCP Tool Shape

Mirror the safe CLI reads in `cli/mcp_server.py`:

- `treadfi_accounts`
- `treadfi_balances`
- `treadfi_orders`
- `treadfi_order`
- `treadfi_multi_orders`
- `treadfi_multi_order`

Write tools should not be exposed until we have Tread.fi sandbox credentials and
can prove dry-run and `execute=true` behavior. If exposed later, each tool should
require an explicit `execute: bool = False` argument and return the exact payload
that would be sent when dry-run is active.

## Safety Rules

- Default every order-mutating path to dry-run.
- Require `--execute` for CLI writes and `execute=true` for MCP writes.
- Require a caller-provided `custom_order_id` for write payloads so Tread.fi
  orders are traceable back to agent intent.
- Do not translate `agent-cli` strategy decisions into Tread.fi orders
  automatically in the first implementation.
- Do not implement a `VenueAdapter` first. Tread.fi is an OEMS/order router, not
  a direct venue data/execution adapter in the same shape as Hyperliquid.
- Do not assume the Tread.fi Market Maker Bot has a REST management endpoint.
  Use generic order APIs until Tread.fi confirms a bot API.

## PR Path

1. Docs-only PR: this integration plan.
2. Read-only client PR: config, REST client, account/balance/order reads, tests
   with mocked HTTP responses.
3. CLI read PR: `hl treadfi ...` read commands backed by the client.
4. MCP read PR: explicit FastMCP tools for the same read surfaces.
5. Write dry-run PR: payload-file submit/cancel/pause/resume/amend commands,
   dry-run by default, no live network mutation without `--execute`.
6. Write MCP PR: only after sandbox credentials and a verified test plan exist.
7. Optional higher-level order builders: TWAP, Limit, and BTCSWP-specific payload
   templates after Tread.fi confirms production pair naming and account setup.
