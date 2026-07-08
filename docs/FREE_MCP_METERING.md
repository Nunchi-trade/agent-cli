# Free MCP metering (no inference subscription)

As of the 2026-07-07 product split, **MCP trading tools are free** and monetized via Hyperliquid builder code. An inference subscription is optional and only required for Nunchi/OpenRouter-backed `openrouter_chat`.

## Gateway behavior

- `mcp-gateway` calls web-auth `/api/metering/status` **only when**:
  - the connect payload includes `subscription_id` (inference subscriber), or
  - the tool is `openrouter_chat`.
- Free MCP tool calls (`trade`, `funding_hedge_execute`, market reads, etc.) proceed without subscription quota checks.

## agent-cli expectations

- `POST /api/mcp/connect` may omit `planId` / subscription fields for free MCP.
- When no inference subscription is active, web-auth mints gateway tokens with `plan_id: free-mcp` and no `subscription_id`.
- Usage upload (`POST /api/metering/usage`) still records control-plane and tool events for observability; it does not gate free MCP trading.
- BYO inference (user-supplied model keys) stays outside Nunchi metering unless the user purchases an inference plan.

## When to pass subscription context

Pass `subscription_id` and inference `plan_id` from web-auth connect only when the user has an active inference subscription. agent-cli should not fabricate subscription IDs for free MCP sessions.
