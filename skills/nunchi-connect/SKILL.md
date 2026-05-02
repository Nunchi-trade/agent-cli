---
name: nunchi-connect
version: 1.0.0
description: Bind an existing agent to Nunchi Agent Studio with a one-time auth code
author: Nunchi
dependencies: []
---

# Nunchi Connect

Bind this running agent to the user's Nunchi Agent Studio account. The user will provide a session-specific connection contract after clicking **Add agent** in Agent Studio.

## Agent Mandate

You are connecting yourself to Nunchi Agent Studio. Complete the binding once, show the user the auth code, then continue sending status events while you run.

Rules:
- Use only the session values provided by the user in the connection contract.
- Generate or reuse one short auth code and show it to the user before posting the first heartbeat.
- Never invent a Nunchi account id, agent id, session id, or pairing code.
- Do not ask the user for private keys or wallet secrets.
- Do not mark yourself connected until the relay accepts your heartbeat.

## Required Session Values

The connection contract must provide:

| Name | Environment variable | Required |
|---|---|---|
| Bind URL | `NUNCHI_BIND_URL` | yes |
| Nunchi account id | `NUNCHI_ACCOUNT_ID` | yes |
| Agent id | `NUNCHI_AGENT_ID` | yes |
| Pairing session id | `NUNCHI_BINDING_SESSION_ID` | yes |
| Pairing code | `NUNCHI_BINDING_CODE` | yes |
| Pairing expiry timestamp | `NUNCHI_BINDING_EXPIRES_AT_MS` | recommended |
| Auth code | `NUNCHI_AGENT_AUTH_CODE` | optional; generate one if absent |

## Binding Flow

1. Read the session values from the user's connection contract.
2. Generate an auth code if `NUNCHI_AGENT_AUTH_CODE` is not already set.
3. Tell the user: `My Nunchi auth code is: <code>`.
4. POST one heartbeat to `NUNCHI_BIND_URL`.
5. If the relay returns success, continue normal operation.
6. Send follow-up `heartbeat`, `log`, `error`, and `stop` events when state changes.

## Heartbeat Schema

POST JSON to the bind URL:

```json
{
  "agentId": "agent-id-from-contract",
  "accountId": "account-id-from-contract",
  "bindingSessionId": "session-id-from-contract",
  "bindingCode": "pairing-code-from-contract",
  "bindingExpiresAtMs": 1770000000000,
  "authCode": "NUN-ABC123",
  "event": "heartbeat",
  "phase": "running",
  "source": "nunchi-connect",
  "snapshot": {
    "ready": true
  }
}
```

## Shell Fallback

Use this when you can run shell commands:

```bash
if [ -z "$NUNCHI_AGENT_AUTH_CODE" ]; then
  export NUNCHI_AGENT_AUTH_CODE="NUN-$(python3 -c 'import secrets; print(secrets.token_hex(3).upper())')"
fi

echo "My Nunchi auth code is: $NUNCHI_AGENT_AUTH_CODE"

curl -sS -X POST "$NUNCHI_BIND_URL" \
  -H "content-type: application/json" \
  -d "{\"agentId\":\"$NUNCHI_AGENT_ID\",\"accountId\":\"$NUNCHI_ACCOUNT_ID\",\"bindingSessionId\":\"$NUNCHI_BINDING_SESSION_ID\",\"bindingCode\":\"$NUNCHI_BINDING_CODE\",\"bindingExpiresAtMs\":${NUNCHI_BINDING_EXPIRES_AT_MS:-0},\"authCode\":\"$NUNCHI_AGENT_AUTH_CODE\",\"event\":\"heartbeat\",\"phase\":\"running\",\"source\":\"nunchi-connect\",\"snapshot\":{\"ready\":true}}"
```

Expected success response:

```json
{
  "ok": true,
  "status": "code-received"
}
```

If the relay returns `401`, `409`, or `410`, tell the user the exact error and ask them to generate a fresh Add agent session.

## Python Snippet

Use this inside a Python agent process:

```python
import json
import os
import secrets
import urllib.request


def nunchi_auth_code() -> str:
    code = os.environ.get("NUNCHI_AGENT_AUTH_CODE")
    if code:
        return code
    code = f"NUN-{secrets.token_hex(3).upper()}"
    os.environ["NUNCHI_AGENT_AUTH_CODE"] = code
    return code


def nunchi_emit(event: str, **payload):
    body = {
        "agentId": os.environ["NUNCHI_AGENT_ID"],
        "accountId": os.environ["NUNCHI_ACCOUNT_ID"],
        "bindingSessionId": os.environ["NUNCHI_BINDING_SESSION_ID"],
        "bindingCode": os.environ["NUNCHI_BINDING_CODE"],
        "bindingExpiresAtMs": int(os.environ.get("NUNCHI_BINDING_EXPIRES_AT_MS", "0")),
        "authCode": nunchi_auth_code(),
        "source": "nunchi-connect-python",
        "event": event,
        **payload,
    }
    req = urllib.request.Request(
        os.environ["NUNCHI_BIND_URL"],
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=5).read()


print(f"My Nunchi auth code is: {nunchi_auth_code()}")
nunchi_emit("heartbeat", phase="running", snapshot={"ready": True})
```

## JavaScript Snippet

Use this inside a Node or TypeScript agent process:

```js
const crypto = require("crypto");

function nunchiAuthCode() {
  if (process.env.NUNCHI_AGENT_AUTH_CODE) return process.env.NUNCHI_AGENT_AUTH_CODE;
  const code = `NUN-${crypto.randomBytes(3).toString("hex").toUpperCase()}`;
  process.env.NUNCHI_AGENT_AUTH_CODE = code;
  return code;
}

async function nunchiEmit(event, payload = {}) {
  const body = {
    agentId: process.env.NUNCHI_AGENT_ID,
    accountId: process.env.NUNCHI_ACCOUNT_ID,
    bindingSessionId: process.env.NUNCHI_BINDING_SESSION_ID,
    bindingCode: process.env.NUNCHI_BINDING_CODE,
    bindingExpiresAtMs: Number(process.env.NUNCHI_BINDING_EXPIRES_AT_MS || "0"),
    authCode: nunchiAuthCode(),
    source: "nunchi-connect-js",
    event,
    ...payload,
  };

  const res = await fetch(process.env.NUNCHI_BIND_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Nunchi bind failed ${res.status}: ${await res.text()}`);
  return res.json();
}

console.log(`My Nunchi auth code is: ${nunchiAuthCode()}`);
await nunchiEmit("heartbeat", { phase: "running", snapshot: { ready: true } });
```

## Status Events

After the first heartbeat, send updates with the same session fields:

| Event | Required fields | Use |
|---|---|---|
| `heartbeat` | `phase`, `snapshot` | Liveness and current status |
| `log` | `stream`, `line` | User-visible runtime logs |
| `error` | `message`, `phase` | Failures that Agent Studio should show |
| `stop` | `phase` | Shutdown or offline transition |

## Recovery

| Relay response | Meaning | Action |
|---|---|---|
| `400` | Missing or invalid field | Check the connection contract and retry once |
| `401` | Pairing code rejected | Ask user for a new Add agent session |
| `409` | Account, agent, or session mismatch | Stop; do not retry with guessed values |
| `410` | Session expired | Ask user to create a fresh Add agent session |
| `5xx` | Relay unavailable | Retry with exponential backoff for up to 2 minutes |
