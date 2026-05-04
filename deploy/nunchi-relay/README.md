# Nunchi Agent Binding Relay

Short-lived public relay for Agent Studio binding sessions.

## Contract

Agents POST their first heartbeat:

```http
POST /agents/bind
content-type: application/json

{
  "agentId": "openclaw-agent",
  "accountId": "acct_123",
  "bindingSessionId": "ps_ABC",
  "bindingCode": "SESSION_SECRET",
  "authCode": "NUN-ABC123",
  "event": "heartbeat",
  "phase": "starting",
  "source": "railway-openclaw",
  "bindingExpiresAtMs": 1770000000000,
  "snapshot": { "ready": true }
}
```

Agent Studio polls the same session with the pairing code:

```http
GET /agents/bind/ps_ABC?agentId=openclaw-agent&accountId=acct_123&bindingCode=SESSION_SECRET
```

The relay stores only a SHA-256 hash of `bindingCode`. It echoes the supplied
code back only after the poll request verifies it, so the desktop can reuse its
local pairing validation.

## Environment

| Variable | Default | Purpose |
|---|---:|---|
| `PORT` | `8080` | HTTP bind port |
| `HOST` | `0.0.0.0` | HTTP bind host |
| `NUNCHI_RELAY_STATE_PATH` | `/data/nunchi-relay-sessions.json` | Optional persistent state file |
| `NUNCHI_RELAY_SESSION_TTL_MS` | `600000` | Fallback expiry when an event has no `bindingExpiresAtMs` |
| `CORS_ORIGIN` | `*` | Browser polling origin |
