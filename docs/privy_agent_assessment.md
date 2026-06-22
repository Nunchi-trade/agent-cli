# Privy Agent Assessment

## Recommendation

Keep `web-auth` as the signing spine for `agent-cli`. Do not add the Privy Agent CLI or Privy MCP server as runtime dependencies in `agent-cli`.

The useful Privy concepts are policy-scoped signers and session metadata. Those fit best as payloads and templates that `web-auth` can apply through Privy's SDK or REST API, while `agent-cli` keeps producing Hyperliquid actions and explicit request scopes.

## What To Add

### Policy-scoped signers

Privy's signer and policy model maps cleanly to Hyperliquid's split between user-signed actions and agent/API-wallet L1 actions.

Useful policy templates:

- Deny `HyperliquidTransaction:Withdraw` by default.
- Deny `HyperliquidTransaction:SendAsset` by default.
- Allow `HyperliquidTransaction:ApproveAgent` when the user wants to authorize a local trading key.

The `hl privy policies` command prints these policy bodies so operators can create matching Privy policies with the SDK or REST API.

### Signer attach payloads

Privy's SDK can attach an additional signer or key quorum to a wallet with policy overrides. `hl privy signer-payload` generates the wallet update payload shape:

```json
{
  "additional_signers": [
    {
      "signer_id": "SIGNER_ID",
      "override_policy_ids": ["POLICY_ID"]
    }
  ]
}
```

### Session scope metadata

`web-auth` already accepts `scope` metadata on sign requests. `agent-cli` should include this metadata for any action that may run under a Privy/session-signer policy:

```json
{
  "method": "hl.approveAgent",
  "network": 42161,
  "notionalUsdc": 100
}
```

The `hl privy scope` command generates that payload for testing and documentation.

## What Not To Add

### `@privy-io/agent-wallet-cli`

The Agent CLI is useful for provisioning and funding a separate Privy sandbox wallet. That is not the `agent-cli` custody model. `agent-cli` already uses a local Hyperliquid agent key for trading and `web-auth` for user-approved master-wallet actions.

Adding the Agent CLI would introduce a second wallet session and funding UX without improving Hyperliquid withdraw/deposit/bridge support.

### `@privy-io/mcp-server`

Privy's MCP server README advertises `@privy-io/mcp-server`, but the npm registry returned `404` during research. Until the package is actually published and stable, do not wire it into `agent-cli`.

### Node SDK inside `agent-cli`

Privy's Node SDK belongs in `web-auth`, where Privy credentials and browser-mediated wallet state already live. `agent-cli` should stay Python-native and emit deterministic action, transaction, policy, and scope payloads.

## Future Upgrade Path

1. Keep per-request browser approval as the default for fund movement.
2. Add web-auth routes that create/apply Privy policies and additional signers.
3. Have `agent-cli` pass explicit `scope` metadata on every policy-gated signing request.
4. Only then allow selected low-risk or policy-bounded flows to run headlessly.
