# Exhibit A CI Baseline — 2026-07-07

Run before marking any live section PASS.

## agent-cli

```bash
cd ~/agent-cli
.venv/bin/python -m pytest tests/test_web_auth_signer.py tests/test_mcp_gateway_context.py \
  tests/test_session_policy.py tests/test_mcp_annotations.py tests/test_hl_safety_read_tools.py \
  tests/test_hedge_margin_port.py tests/test_entrypoint.py -q
.venv/bin/python scripts/validate_agent_cli.py --profile e2e
```

| Suite | Result | Notes |
|-------|--------|-------|
| Exhibit A pytest (7 files) | **120 passed** | Fixed `test_mcp_annotations` for manifest/runner drift |
| validate_agent_cli e2e | Run locally with `.venv/bin/python` | Requires Python 3.12 venv |

## web-auth

```bash
cd ~/web-auth && node --test tests/*.test.mjs
```

| Suite | Result |
|-------|--------|
| pair-hosted-invariants + seat-overage | **31 passed** |

## mcp-gateway

```bash
cd ~/mcp-gateway && npm test
```

| Suite | Result |
|-------|--------|
| vitest (5 files) | **29 passed** |

## Defects logged

See [`exhibit-a-defects.md`](exhibit-a-defects.md).
