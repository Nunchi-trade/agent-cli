# Exhibit A Defects Log

| ID | Test | Severity | Status | Owner | Notes |
|----|------|----------|--------|-------|-------|
| D6-1 | A3.4 daily-loss limit | High | **BLOCKED** | web-auth | `dailyLossLimitUsdc` shown in `AgentWallets.tsx` / `SignConfirmModal.tsx` but **not enforced** in `api/pair.js` `sessionAllowsSign()`. Mark A3.4 BLOCKED until wired. |
| D6-2 | A3.1–A3.3 automated | Medium | Open | web-auth | No `sessionAllowsSign` unit tests — PR adds `session-policy-adversarial.test.mjs` |
| D6-3 | A2.9 apex_run | Low | **BLOCKED** | agent-cli | Substituted by A2.8 `cfi_hedge` per [`A2.8-strategy-def.md`](A2.8-strategy-def.md) |
| D6-4 | A2.6 schedule hosted | Low | **BLOCKED** | agent-cli | `schedule_cancel` blocked on hosted `entrypoint.py`; test via stdio MCP only |
| D6-5 | A2.10 funding_hedge_execute | High | **Fixed** | agent-cli | MCP tool added in `cli/mcp_server.py` (was in manifest but missing from runner) |

**Re-run rule:** Any A3 FAIL on live tests → fix D6 → re-run **entire A.3**.
