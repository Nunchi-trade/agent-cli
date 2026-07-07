# Exhibit A Pre-flight Checklist

Completed: 2026-07-07

## Environment

| Repo | Status | Verify command |
|------|--------|----------------|
| agent-cli | Python 3.12.12 venv at `.venv/` | `.venv/bin/python -m cli.main setup check` |
| web-auth | `npm install` OK | `node --check api/pair.js` |
| mcp-gateway | `npm install` OK | `npm test` |

**Note:** Use `.venv/bin/python` — system Python 3.9 lacks dependencies.

## Staging URLs

| Service | URL | Check |
|---------|-----|-------|
| web-auth | https://auth.nunchi.trade | HTTP reachable |
| mcp-gateway | https://agent.nunchi.trade | HTTP reachable |
| MCP trading | `POST https://agent.nunchi.trade/mcp/trading` | Requires bearer token |

## Live test prerequisites (Sam)

- [ ] Privy master wallet on HL **mainnet** with minimal USDC
- [ ] Agent sub-wallet created and bound via AgentWallets
- [ ] Caps configured in AgentWallets (spend, position, expiry)
- [ ] Screen recorder + shared folder for MCP JSON-RPC logs
- [ ] Railway runner URL confirmed in gateway env (`NUNCHI_MCP_TOOLS_RUNNER_URL`)

## Network decision

**Mainnet-with-minimal-funds** per July PDF. Set `HL_TESTNET=false` / mainnet consent in gateway tier for danger-section tests.
