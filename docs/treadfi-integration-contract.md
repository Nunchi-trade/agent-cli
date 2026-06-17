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
- The published docs include an `llms.txt` index with REST API reference pages,
  examples, WebSocket reference, Market Maker Bot docs, Hyperliquid connection
  docs, analytics pages, and self-hosted service docs.
- Examples and API reference cover accounts, balances, single orders,
  multi-orders, simple orders, algorithmic orders, analytics, miscellaneous,
  and risk/admin endpoints.
- Core order lifecycle endpoints include list, active, submit, get summary, get
  detail, get errors, cancel, pause, resume, amend, and cancel/pause/resume-all.
- Simple orders use `POST /api/orders/` with strategies `Market`, `Limit`,
  `IOC`, or `Iceberg`.
- Algorithmic orders use `POST /api/orders/` with strategies `TWAP`, `VWAP`,
  `POV`, `IS`, and `Target Time`.
- Hyperliquid account linking is performed through Tread.fi Key Management in
  the web app. Tread.fi supports Hyperliquid, including HIP-3, once the account
  is connected there.
- The Market Maker Bot docs describe the product flow but do not clearly expose
  a dedicated bot-management REST endpoint.

Important ambiguity: most API docs say `Authorization: Token <API_KEY>`, while a
few example snippets say `Authorization: Bearer <token>`. The client should
default to `Token` but make the auth scheme configurable until confirmed with
Tread.fi.

## Documented REST Surface

The first implementation should stick to these public endpoints:

### Accounts And Balances

| Capability | Endpoint | Notes |
| --- | --- | --- |
| Account balances | `GET /api/balances/` | Identity inferred by API token; optional `account_names` filter documented. |
| User accounts | `GET /api/accounts/` | Optional `include_archived=true`; response includes account `id`, `name`, `exchange`, `margin_mode`, `archived`, masked secrets. |
| Cached SOR balances | `GET /api/sor/get_cached_account_balance` | Query `account_names` as comma-separated display names; response has `balances` and `errors`. |
| Set leverage | `POST /api/set_leverage/` | Mutating endpoint; keep out of the read-only phase. |
| Create/update/archive account | `POST` / `PUT` / `DELETE /api/accounts/` | Key-management operations; keep out of the first integration. |

### Single Orders

| Capability | Endpoint | Notes |
| --- | --- | --- |
| List orders | `GET /api/orders/` | Supports `statuses`, `account_names`, `before`, `after`, `custom_order_ids`, `page`, `page_size`. |
| Active orders | `GET /api/active_orders/` | Active subset of orders. |
| Submit order | `POST /api/orders/` | One endpoint for simple and algorithmic strategies. |
| Order summary | `GET /api/order_summary/{id}` | Lighter read without full fill details. |
| Order detail | `GET /api/order/{id}` | Heavy read with configuration, placements/fills, analytics, market data, audit trail. |
| Order errors | `GET /api/order_errors/` | Error feed for orders. |
| Cancel order | `DELETE /api/order/{id}` | Mutating. |
| Pause order | `POST /api/pause_order/` | Mutating. |
| Resume order | `POST /api/resume_order/` | Mutating. |
| Amend order | `POST /api/amend_order/` | Mutating. |
| Cancel all orders | `POST /api/cancel_all_orders/` | Mutating, high blast radius. |
| Pause all orders | `POST /api/pause_all_orders/` | Mutating, high blast radius. |
| Resume all orders | `POST /api/resume_all_orders/` | Mutating, high blast radius. |

### Multi Orders

| Capability | Endpoint | Notes |
| --- | --- | --- |
| List multi-orders | `GET /api/multi_orders/` | Supports `include_child_orders`, `custom_order_ids`, `statuses`, `before`, `after`, `page_number`, `page_size`. |
| Multi-order detail | `GET /api/multi_order/{id}` | Optional `include_child_orders=true`. |
| Submit multi-order | `POST /api/multi_orders/` | Requires parent execution fields plus `child_orders`. |
| Cancel multi-orders | `POST /api/cancel_multi_orders/` | Mutating. |
| Pause multi-order | `POST /api/pause_multi_order/` | Mutating. |
| Resume multi-order | `POST /api/resume_multi_order/` | Mutating. |
| Placements | `GET /api/placements/` | Takes `order_id`; supports status/time/page filters. |

Admin/risk endpoints exist but require staff/admin privileges and should not be
part of the first agent-facing integration.

## Documented Types And Payload Rules

Order submit payloads should be treated as opaque JSON first, with light local
validation for fields the docs mark required:

- `pair`: string, e.g. `BTC-USD` or perp-style examples such as
  `BTC:PERP-USDT`.
- `side`: `buy` or `sell`.
- `accounts`: array of account display names chosen in Tread.fi Key Management.
- `strategy`: documented enum includes `TWAP`, `VWAP`, `IS`, `Target Time`,
  `Market`, `Limit`, `Iceberg`, and `IOC`.
- Quantity: exactly one of `base_asset_qty`, `quote_asset_qty`, or
  `sell_token_amount`.
- Algorithmic duration: `duration` is required unless `pov_target` is present.
- Common algo controls: `engine_passiveness` in `[0, 1]`,
  `schedule_discretion` in `[0.02, 0.5]`, `alpha_tilt` in `[-1, 1]`,
  `pov_limit` and `pov_target` in `(0, 1]`.
- Traceability: `custom_order_id` is optional in Tread.fi but should be required
  by `agent-cli` write commands. Tread.fi says exchange client order IDs begin
  with `TL` followed by the first 9 alphanumeric characters of
  `custom_order_id`.
- Strategy params: the type reference includes `passive_only`, `active_limit`,
  `reduce_only`, `ool_pause`, `strict_duration`, `dicy`, `spot_leverage`, and
  `max_clip_size`.

Status enums to model:

- Single orders: `SCHEDULED`, `ACTIVE`, `FINISHER`, `COMPLETE`, `CANCELED`,
  `PAUSED`.
- Multi-orders: `ACTIVE`, `COMPLETE`, `CANCELED`, `PAUSED`.
- Market types: `spot`, `perp`, `unified` for balances; `future` and `option`
  are documented as not yet available.
- Asset types: `token`, `position`.

Because the docs include both decimal strings and JSON numbers depending on
endpoint, the first client models should preserve raw JSON as `dict`/`list`
rather than coercing every value into a strict Pydantic model.

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
- `TREADFI_DEFAULT_ACCOUNTS`: optional comma-separated account display names for
  command defaults.

Secrets should be read from environment variables only. Do not reuse
`HL_PRIVATE_KEY`; Tread.fi auth is a platform API token, not a Hyperliquid
signing key.

Header construction:

```text
Authorization: ${TREADFI_AUTH_SCHEME:-Token} ${TREADFI_API_TOKEN}
Content-Type: application/json  # write requests only
```

## Client Layer

Add a typed REST wrapper, for example `modules/treadfi_client.py`, with:

- `TreadFiConfig.from_env()`
- `TreadFiClient`
- `TreadFiError`
- request helpers for JSON responses and structured error messages

Initial read methods:

- `list_accounts(include_archived=False)` -> `GET /api/accounts/`
- `get_balances(account_names=None)` -> `GET /api/balances/`
- `get_cached_balances(account_names=None)` ->
  `GET /api/sor/get_cached_account_balance`
- `list_orders(statuses=None, account_names=None, before=None, after=None,
  custom_order_ids=None, page=1, page_size=100)` -> `GET /api/orders/`
- `list_active_orders()` -> `GET /api/active_orders/`
- `get_order(order_id)` -> `GET /api/order/{id}`
- `get_order_summary(order_id)` -> `GET /api/order_summary/{id}`
- `list_order_errors(...)` -> `GET /api/order_errors/`
- `list_multi_orders(include_child_orders=False, custom_order_ids=None,
  statuses=None, before=None, after=None, page_number=1, page_size=100)` ->
  `GET /api/multi_orders/`
- `get_multi_order(order_id, include_child_orders=False)` ->
  `GET /api/multi_order/{id}`
- `list_placements(order_id, statuses=None, before=None, after=None,
  page_number=1, page_size=100)` -> `GET /api/placements/`

Write methods should exist only after the read client is covered by tests:

- `submit_order(payload)` -> `POST /api/orders/`
- `cancel_order(order_id)` -> `DELETE /api/order/{id}`
- `pause_order(order_id)` -> `POST /api/pause_order/`
- `resume_order(order_id)` -> `POST /api/resume_order/`
- `amend_order(payload)` -> `POST /api/amend_order/`
- `cancel_all_orders(...)` -> `POST /api/cancel_all_orders/`
- `submit_multi_order(payload)` -> `POST /api/multi_orders/`
- `cancel_multi_orders(payload)` -> `POST /api/cancel_multi_orders/`
- `pause_multi_order(order_id)` -> `POST /api/pause_multi_order/`
- `resume_multi_order(order_id)` -> `POST /api/resume_multi_order/`

Do not implement admin/risk-limit endpoints in the first pass. Those require
staff/admin permissions and should live in a separate operator-only PR.

## CLI Shape

Add `cli/commands/treadfi.py` and register it in `cli/main.py`:

Read-only commands:

- `hl treadfi accounts ls`
- `hl treadfi balances [--account NAME] [--cached]`
- `hl treadfi orders ls [--active] [--status STATUS] [--account NAME] [--custom-order-id ID] [--page N] [--page-size N]`
- `hl treadfi orders show ORDER_ID [--summary]`
- `hl treadfi orders errors`
- `hl treadfi multi-orders ls [--children] [--status STATUS] [--page N] [--page-size N]`
- `hl treadfi multi-orders show ORDER_ID [--children]`
- `hl treadfi placements ls --order-id ORDER_ID`

Write commands, all dry-run by default:

- `hl treadfi orders submit --payload FILE [--execute]`
- `hl treadfi orders cancel ORDER_ID [--execute]`
- `hl treadfi orders pause ORDER_ID [--execute]`
- `hl treadfi orders resume ORDER_ID [--execute]`
- `hl treadfi orders amend --payload FILE [--execute]`
- `hl treadfi multi-orders submit --payload FILE [--execute]`
- `hl treadfi multi-orders cancel --payload FILE [--execute]`
- `hl treadfi multi-orders pause ORDER_ID [--execute]`
- `hl treadfi multi-orders resume ORDER_ID [--execute]`

The first write interface should accept a JSON payload file rather than many CLI
flags. Tread.fi order payloads have strategy-specific fields, and a payload file
keeps the CLI honest while tests lock down validation. Friendly typed flags can
be added later for common cases like TWAP and Limit.

Minimum local validation for write payload files:

- require `custom_order_id`
- require `pair`, `side`, `accounts`, `strategy`
- require exactly one quantity field
- require `duration` unless `pov_target` is present for algo orders
- reject `cancel_all`, `pause_all`, or `resume_all` commands unless we add a
  separate high-risk operator mode

## MCP Tool Shape

Mirror the safe CLI reads in `cli/mcp_server.py`:

- `treadfi_accounts`
- `treadfi_balances`
- `treadfi_orders`
- `treadfi_order`
- `treadfi_order_errors`
- `treadfi_multi_orders`
- `treadfi_multi_order`
- `treadfi_placements`

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
- Keep `set_leverage`, account creation/update/archive, admin/risk limits, and
  cancel/pause/resume-all out of the default agent surface.
- Log or return the exact outbound payload for every dry-run write command.

## PR Path

1. Docs-only PR: this integration plan.
2. Read-only client PR: config, REST client, account/balance/order reads, tests
   with mocked HTTP responses.
3. Order payload validation PR: pure helpers that enforce local safety rules
   without network calls.
4. CLI read PR: `hl treadfi ...` read commands backed by the client.
5. MCP read PR: explicit FastMCP tools for the same read surfaces.
6. Write dry-run PR: payload-file submit/cancel/pause/resume/amend commands,
   dry-run by default, no live network mutation without `--execute`.
7. Write MCP PR: only after sandbox credentials and a verified test plan exist.
8. Optional higher-level order builders: TWAP, Limit, and BTCSWP-specific payload
   templates after Tread.fi confirms production pair naming and account setup.
