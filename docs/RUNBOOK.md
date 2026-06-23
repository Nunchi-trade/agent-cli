# APEX Operational Runbook

## Starting / Stopping

### Start (Hosted Agent)

Hosted agents are deployed only through web-auth after billing entitlement and wallet binding:

```text
https://auth.nunchi.trade
```

The hosted-agent provisioner creates the Nunchi-owned runtime, injects secrets, and starts the runner. Users should not deploy this repository directly.

### Start (Local)
```bash
hl apex run --preset default --budget 1000 --data-dir data/apex
```

### Stop
Send `SIGTERM` — the runner will:
1. Stop accepting new positions
2. Leave exchange-level stop-losses in place (safety net)
3. Persist state to `data/apex/state.json`
4. Exit cleanly

**Never use `kill -9`** — this skips state persistence and can leave orphaned positions.

### Verify State After Stop
```bash
hl apex status
hl apex reconcile
```

---

## Common Alerts

### `CRITICAL: API circuit breaker open`
**Cause**: 5+ consecutive API failures (HL API down or network issue).
**Impact**: Engine enters safe mode — no new entries, only guard exits.
**Action**: Check HL API status. If HL is up, check network connectivity from the hosted-agent runtime. The circuit breaker resets automatically on the next successful API call.

### `CRITICAL: N consecutive tick timeouts`
**Cause**: Tick execution exceeded 30s three times in a row.
**Impact**: Engine enters safe mode.
**Action**: Check if HL API is slow (high latency). Reduce the number of instruments scanned by Pulse. Check `/metrics` for `tick_latency_ms` trends.

### `WARNING: Rate limited (429)`
**Cause**: Too many API calls to HL within their rate window.
**Impact**: Automatic exponential backoff (2s → 4s → 8s).
**Action**: Usually self-resolving. If persistent across many ticks, reduce `radar_interval_ticks` or `pulse` scan frequency.

### `WARNING: Tick N took Xs (Y% of interval)`
**Cause**: A tick took more than 80% of the configured interval.
**Impact**: Ticks may start overlapping.
**Action**: Check which phase of the tick is slow (Pulse candle fetching is often the bottleneck). Consider increasing `tick_interval`.

---

## Safe Mode Recovery

When the engine enters safe mode:
1. No new entries are taken
2. Existing guards continue to function (exits still work)
3. Check `/metrics` or `hl apex status` to confirm safe mode

**To recover**:
1. Diagnose the root cause (API issues, timeouts)
2. If the issue is resolved, restart the runner — safe mode resets on restart
3. Alternatively, use the API: `POST /api/configure` with `{"params": {"safe_mode": false}}`

---

## Manual Reconciliation

```bash
hl apex reconcile          # Check for discrepancies
hl apex reconcile --fix    # Auto-fix orphaned positions/slots
```

**When to run**: After crashes, network outages, or any unclean shutdown.

---

## Emergency Position Close

```bash
# Close a specific slot
hl apex close <slot_id>

# Close all positions (nuclear option)
hl apex close --all
```

If the CLI is unavailable, use the Hyperliquid web UI directly.

---

## Log Analysis

### JSON logs

Use the hosted-agent operator logs for the provisioned service.

### Key log patterns to watch
- `CRITICAL` — circuit breaker, tick timeouts, unexpected errors
- `Guard close` — trailing stop triggered
- `Rate limited` — API throttling
- `Reconciliation` — state vs exchange mismatches

---

## Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `RUN_MODE` | `apex` | `apex`, `strategy`, `mcp` |
| `APEX_PRESET` | `default` | `conservative`, `default`, `aggressive` |
| `APEX_BUDGET` | auto | Total trading capital |
| `APEX_SLOTS` | `3` | Max concurrent positions |
| `HL_TESTNET` | `true` | `false` for mainnet |
| `API_AUTH_TOKEN` | unset | Bearer token for control endpoints |
| `DATA_DIR` | `/data` | Persistent state directory |
| `TICK_INTERVAL` | varies | Seconds between ticks |

---

## Hosted Deployment Checklist

- [ ] User has active hosted-agent entitlement in web-auth
- [ ] Agent wallet is bound on the wallet binding page
- [ ] Hosted deploy is requested from web-auth, not from this repository
- [ ] Provisioner `/health` reports `railwayConfigured: true`
- [ ] Provisioner `/health` reports `callbackConfigured: true`
- [ ] Hosted endpoint `/health` responds with 200
- [ ] Hosted endpoint `/mcp/trading` responds
- [ ] Web-auth hosted-agent status refresh shows the provisioned endpoint
- [ ] Monitor `/metrics` endpoint for tick latency and error counts
