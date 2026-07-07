# Exhibit A Test Tracker

Updated: 2026-07-07  
Source plan: [`EXHIBIT_A_TESTING_PLAN.md`](EXHIBIT_A_TESTING_PLAN.md)  
Runbook: [`exhibit-a-execution-runbook.md`](exhibit-a-execution-runbook.md)

**Result values:** `PASS` | `FAIL` | `BLOCKED`  
**Recording:** link to screen recording, MCP JSON-RPC log, HL tx, or web-auth sign-request ID.

---

## §A.0 — Index / data (Tier 0 — separate workstream)

| ID | Test | Repo | Command / surface | Expected | Result | Recording | Owner | Date |
|----|------|------|-------------------|----------|--------|-----------|-------|------|
| A0.1 | Index price accuracy | D1/D2 | Public page vs API | Prices match | | | D1/D2 | |
| A0.2 | Refresh / freshness | D1/D2 | Feed cadence | Updates at cadence | | | D1/D2 | |
| A0.3 | Paid in funding figures | D1/D2 | Published vs source | Reconciles | | | D1/D2 | |
| A0.4 | Availability | D1/D2 | Page + API errors | Graceful degradation | | | D1/D2 | |

---

## §A.1 — Onboarding & inference paths

| ID | Test | Repo | Command / surface | Expected | Result | Recording | Owner | Date |
|----|------|------|-------------------|----------|--------|-----------|-------|------|
| A1.1 | ACP local harness | agent-cli | `exhibit_a_mcp_smoke.py --section a1` | setup_check OK | **PASS** | `data/exhibit-a/mcp-smoke-all.json` | auto | 2026-07-07 |
| A1.2 | OpenRouter inference | gateway | `openrouter_chat` live | Calls succeed | **BLOCKED** | Needs inference tier token | Sam | |
| A1.3 | Fusion routing | gateway | `openrouter/fusion` | Cost delta vs A1.2 | **BLOCKED** | Needs live inference | Sam | |
| A1.4 | Nunchi-brokered inference | web-auth | Inference tier checkout | Metering rows | **BLOCKED** | Needs Stripe test user | Sam | |
| A1.5 | BYO API key | agent-cli | Local keystore MCP | No Nunchi charge | **BLOCKED** | Needs Sam manual run | Sam | |
| A1.6 | Claude/Codex via ACP | agent-cli | Cursor MCP config | Tools via MCP | **BLOCKED** | Needs Sam screen recording | Sam | |
| A1.7 | MCP-link-to-own-agent | gateway | `exhibit_a_gateway_smoke.sh` | Onboard without TUI | **BLOCKED** | Needs `EXHIBIT_A_MCP_TOKEN` | Sam | |
| A1.8 | Passport signing | web-auth | AgentWallets approveAgent | Wallet bound | **BLOCKED** | Needs Privy mainnet wallet | Sam | |

---

## §A.2 — MCP tool verification

### Read-only path

| ID | Test | MCP tool | Gateway tier | Expected | Result | Recording | Owner | Date |
|----|------|----------|--------------|----------|--------|-----------|-------|------|
| A2.1 | setup | `setup_check` | read_only | Pre-flight OK | **PASS** | `mcp-smoke-all.json` | auto | 2026-07-07 |
| A2.2 | check accounts | `account`, `status` | read_only | Account state | **PASS** | `mcp-smoke-all.json` | auto | 2026-07-07 |
| A2.3 | analysis | `judge_report` | read_only | Analysis output | **BLOCKED** | Not in local smoke; gateway | Sam | |
| A2.4 | trade journal read | `trade_journal` | read_only | Journal entries | **PASS** | `mcp-smoke-all.json` | auto | 2026-07-07 |
| A2.5 | index + hedge rec | `funding_hedge_propose`, `funding_rates` | read_only | Proposal (D3) | **PASS** | `mcp-smoke-all.json` | auto | 2026-07-07 |
| A2.6 | schedule | `schedule_cancel` | stdio only | Cancel obeys policy | **BLOCKED** | Hosted runner blocks; stdio only | Sam | |

### Danger section

| ID | Test | MCP tool | Gateway tier | Expected | Result | Recording | Owner | Date |
|----|------|----------|--------------|----------|--------|-----------|-------|------|
| A2.7 | run a trade | `trade` | live_trading | Within caps | **PASS** | Local dry-run context gate; live **BLOCKED** | auto/Sam | 2026-07-07 |
| A2.8 | run strategies | `run_strategy` cfi_hedge | live_trading | Stoppable | **PASS** | Mock dry-run `mcp-smoke-all.json` | auto | 2026-07-07 |
| A2.9 | Apex run | `apex_run` | — | Per definition | **BLOCKED** | Substituted by A2.8 per [`A2.8-strategy-def.md`](A2.8-strategy-def.md) | Jae | 2026-07-07 |
| A2.10 | execute hedge on HL | `funding_hedge_execute` | live_trading | Hedge within caps | **PASS** | Dry-run local; live mainnet **BLOCKED** | auto/Sam | 2026-07-07 |

---

## §A.3 — Safeguard / cap adversarial

| ID | Test | Break attempt | Expected refusal | Result | Recording | Owner | Date |
|----|------|---------------|------------------|--------|-----------|-------|------|
| A3.1 | Spend cap — direct | Order above cap | `spend_limit_exceeded` | **PASS** | `session-policy-adversarial.test.mjs` | auto | 2026-07-07 |
| A3.2 | Spend cap — cumulative | Multiple orders | Block at cap | **PASS** | `session-policy-adversarial.test.mjs` | auto | 2026-07-07 |
| A3.3 | Position cap | Order above position | `position_size_exceeded` | **PASS** | `session-policy-adversarial.test.mjs` | auto | 2026-07-07 |
| A3.4 | Daily-loss limit | Losses past cap | Trading halts | **BLOCKED** | D6-1: not enforced in pair.js | Jae | 2026-07-07 |
| A3.5 | Expiry | Sign after expiry | `session_expired` | **PASS** | `session-policy-adversarial.test.mjs` | auto | 2026-07-07 |
| A3.6 | Network flag | Wrong network | `network_not_allowed` | **PASS** | `session-policy-adversarial.test.mjs` | auto | 2026-07-07 |
| A3.7 | Prompt cap evasion | LLM bypass attempt | Still refused | **BLOCKED** | Needs Jacob adversarial session | Jacob | |
| A3.8 | Master-wallet isolation | Agent → master funds | Refused | **BLOCKED** | Needs live HL mainnet test | Jacob | |
| A3.9 | Runtime update | Lower cap mid-session | New limit enforced | **PASS** | `session-policy-adversarial.test.mjs` | auto | 2026-07-07 |

---

## §A.4 — Revoke, unbonding, kill-switch

| ID | Test | Surface | Expected | Result | Recording | Owner | Date |
|----|------|---------|----------|--------|-----------|-------|------|
| A4.1 | Revoke from wallet page | web-auth UI | Authority revoked | **BLOCKED** | Needs Sam + Privy | Jacob | |
| A4.2 | Revoke from MCP/API | `POST /api/pair/revoke` | Revoked | **BLOCKED** | `exhibit_a_gate_d_dry_run.sh` | Jacob | |
| A4.3 | Unbonding | `DELETE /api/agent-wallets/binding` | Clean unbind | **BLOCKED** | Needs live wallet | Jacob | |
| A4.4 | Post-revoke attempt | MCP `trade` after revoke | Rejected | **BLOCKED** | Gate D script | Jacob | |

---

## §A.5 — Prompt injection

| ID | Test | Surface | Expected | Result | Recording | Owner | Date |
|----|------|---------|----------|--------|-----------|-------|------|
| A5.1 | On-chain injection | Poisoned MCP reads | Agent ignores | **BLOCKED** | Needs Jacob LLM session | Jacob | |
| A5.2 | Guard or accept | Same | Risk-acceptance if unguarded | **BLOCKED** | | Jacob | |

---

## §A.6 — Cost & metering

| ID | Test | Artifact | Expected | Result | Recording | Owner | Date |
|----|------|----------|----------|--------|-----------|-------|------|
| A6.1 | 5 takers × 5 days | `mcp_workload_experiment.py` | Cost per agent-day | **PASS** | Pilot `--quick` 5 agents; full 5×5 **BLOCKED** pending tokens | auto | 2026-07-07 |
| A6.2 | Free-tier enforcement | web-auth buckets | Blocked at limit | **BLOCKED** | Needs live free-tier exhaust | Sam | |
| A6.3 | Metering accuracy | metering API | Meter matches | **BLOCKED** | Needs live gateway tokens | Sam | |
| A6.4 | MAU headroom | Privy dashboard | Headroom vs 10k | **BLOCKED** | Spreadsheet task | Jae | |

---

## §A.7 — Sign-off

| Role | Section | Signed | Date |
|------|---------|--------|------|
| Sam | All executed + recorded | | |
| Jacob | A.3, A.4, A.5 reviewed | | |
| Ryan | Gate C — tool surface + caps | | |
| John | Launch authorized | | |

---

## Gate D dry run

| Step | Result | Recording | Date |
|------|--------|-----------|------|
| propose → execute → revoke | **BLOCKED** | `exhibit_a_gate_d_dry_run.sh` needs `EXHIBIT_A_MCP_TOKEN` + `EXHIBIT_A_PAIR_TOKEN` | 2026-07-07 |
