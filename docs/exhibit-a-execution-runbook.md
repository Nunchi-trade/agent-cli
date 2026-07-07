# Exhibit A Execution Runbook

Scripts and docs to execute [`EXHIBIT_A_TESTING_PLAN.md`](EXHIBIT_A_TESTING_PLAN.md).

## Quick start

```bash
# 1. CI baseline (all three repos)
cd ~/agent-cli && .venv/bin/python -m pytest tests/test_web_auth_signer.py tests/test_mcp_gateway_context.py \
  tests/test_session_policy.py tests/test_mcp_annotations.py tests/test_hl_safety_read_tools.py \
  tests/test_hedge_margin_port.py tests/test_entrypoint.py -q
cd ~/web-auth && node --test tests/*.test.mjs
cd ~/mcp-gateway && npm test

# 2. Local MCP smoke (no wallet)
cd ~/agent-cli
EXHIBIT_A_ALLOW_WRITE=1 .venv/bin/python scripts/exhibit_a_mcp_smoke.py --section all

# 3. Gateway smoke (needs token from POST /api/mcp/connect)
export EXHIBIT_A_MCP_URL=https://agent.nunchi.trade/mcp/trading
export EXHIBIT_A_MCP_TOKEN=tok_...
./scripts/exhibit_a_gateway_smoke.sh read_only

# 4. Cost experiment pilot
.venv/bin/python scripts/mcp_workload_experiment.py --agents 5 --days 1 --quick --dry-run

# 5. Gate D dry run
export EXHIBIT_A_AUTH_URL=https://auth.nunchi.trade
export EXHIBIT_A_PAIR_TOKEN=...
./scripts/exhibit_a_gate_d_dry_run.sh
```

## Related docs

| Doc | Purpose |
|-----|---------|
| [`exhibit-a-preflight.md`](exhibit-a-preflight.md) | Environment checklist |
| [`exhibit-a-ci-baseline.md`](exhibit-a-ci-baseline.md) | CI results log |
| [`exhibit-a-defects.md`](exhibit-a-defects.md) | D6 defect tracker |
| [`A2.8-strategy-def.md`](A2.8-strategy-def.md) | Danger-section definitions |
| [`exhibit-a-tracker.md`](exhibit-a-tracker.md) | PASS/FAIL/BLOCKED matrix |

## Reports directory

Automated runs write JSON/JSONL to `data/exhibit-a/` (gitignored).
