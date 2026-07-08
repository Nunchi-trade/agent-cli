# E2E Checklist — agent-cli (Phase 4)

See web-auth `docs/E2E_FREE_MCP_INFERENCE_CHECKLIST.md` for full stack flows.

## agent-cli specific

- [ ] MCP connect without subscription ID still uploads metering events
- [ ] `openrouter_chat` requires inference plan at gateway (not agent-cli local gate)
- [ ] Builder fee regression: `tests/test_builder_fee.py` unchanged pass
- [ ] Exhibit A harness documents free MCP vs inference paths (`docs/FREE_MCP_METERING.md`)
