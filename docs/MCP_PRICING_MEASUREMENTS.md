# MCP Pricing Measurements

Measured on 2026-07-01 for the hosted MCP tools runner path.

## Harness

Run:

```bash
python3 scripts/pricing_measure.py --output tmp/pricing-measurement-local.json
railway run --service hosted-trading-mcp --environment production -- python3 scripts/pricing_measure.py --output tmp/pricing-measurement-runner-env.json
```

Use `--openrouter-live` only when spending OpenRouter credits is intended.

## Results

Production runner env dry-run:

- `RUN_MODE=mcp`, `HL_TESTNET=true`.
- `python.import_cli`: 58.6 ms.
- MCP `strategies`: 118.7 ms, 3,068 response bytes.
- MCP `funding_hedge_execute` without `confirmed=true`: 0.09 ms refusal, no order path.
- Railway resource metrics, last 1h: `<0.01 vCPU`, 15 MB memory, 0 MB network, 0 MB disk.

Local dry-run:

- `python.import_cli`: 62.4 ms.
- MCP `strategies`: 120.8 ms.
- MCP `funding_hedge_execute` without `confirmed=true`: 0.10 ms refusal.

## Economics

Mode 1, hosted MCP tools:

- `C_seat` is not computed yet. Railway metrics expose CPU/memory/network, but not monthly billing cost. Set `RAILWAY_SHARED_RUNTIME_MONTHLY_USD` or pass `--runtime-monthly-usd` once billing data is available.

Mode 2, hosted MCP tools plus Nunchi/OpenRouter inference:

- No live OpenRouter spend was measured because `OPENROUTER_API_KEY` is not present in the runner env and `--openrouter-live` was not run.
- Current inference budgets remain inputs only: Starter `$10`, Growth `$50`, Team `$250`.

Mode 3, clone/local plus builder economics:

- No funded-wallet fill measurement was run because the production runner has no `HL_PRIVATE_KEY`, `HL_KEYSTORE_PASSWORD`, or `~/.hl-agent/env`.
- Formulaic builder-fee economics at the default `BUILDER_FEE_TENTHS_BPS=100` are `$100` per `$100,000` notional and `$1,000` per `$1,000,000` notional.

## Blockers

- Missing Railway monthly billing cost input for Mode 1 `C_seat`.
- Missing `OPENROUTER_API_KEY` for Mode 2 inference spend.
- Missing funded-wallet/HL signing credentials for live fills and builder-fee realization.
