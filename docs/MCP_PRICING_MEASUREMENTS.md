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

Local Task 7 dry-run (`tmp/pricing-measurement-task7-local.json`):

- `python.import_cli`: 69.7 ms.
- MCP `tools/list`: 0.01 ms, 24 hosted runner tools surfaced by the JSON-RPC wrapper.
- MCP `setup_check`: 7.9 ms.
- MCP `strategies`: 166.8 ms, 3,068 response bytes.
- MCP `trade` without signing context: 0.09 ms refusal. This is the safe
  noninteractive confirmation/hang check; no subprocess order path was entered.
- MCP `funding_hedge_execute` without `confirmed=true`: 0.03 ms refusal.

The pricing harness now emits the full Task 7 classification:

- Free/read: 15 tools.
- Paid compute/inference cost centers: 5 tools.
- Safety-gated/fund-moving/wallet-write: 7 tools, or 6 if `wallet_auto` is
  excluded from the costable 26-tool surface because it is disabled on the
  hosted keyless runner.
- Recommended beta free cap: about 20 hosted MCP discovery/read calls before
  subscription or upgrade nudges.

## Economics

Mode 1, hosted MCP tools:

- `C_seat` is not computed yet. Railway metrics expose CPU/memory/network, but not monthly billing cost. Set `RAILWAY_SHARED_RUNTIME_MONTHLY_USD` or pass `--runtime-monthly-usd` once billing data is available.

Mode 2, hosted MCP tools plus Nunchi/OpenRouter inference:

- No live OpenRouter spend was measured because `OPENROUTER_API_KEY` is not present in the runner env and `--openrouter-live` was not run.
- Current inference budgets remain inputs only: Starter `$10`, Growth `$50`, Team `$250`.
- Anchor estimates from the Task 7 prompt:
  - `openai/gpt-4.1-mini` at about `$0.0002` per heartbeat gives about
    50,000 / 250,000 / 1,250,000 heartbeats for Starter / Growth / Team.
  - `openrouter/auto` at about `$0.0037` per heartbeat gives about
    2,703 / 13,514 / 67,568 heartbeats.
  - Fusion at about `$0.033` per capped run, provided as about 146x mini, gives
    about 303 / 1,515 / 7,576 runs.

Mode 3, clone/local plus builder economics:

- No funded-wallet fill measurement was run because the production runner has no `HL_PRIVATE_KEY`, `HL_KEYSTORE_PASSWORD`, or `~/.hl-agent/env`.
- Formulaic builder-fee economics at the default `BUILDER_FEE_TENTHS_BPS=100` are `$100` per `$100,000` notional and `$1,000` per `$1,000,000` notional.
- `trade` itself should remain free or low-friction from a pricing standpoint:
  it is safety-sensitive, not inference-heavy, and it is the path that can
  produce builder-code/builder-fee economics. Gate it with confirmation,
  builder-code validation, network consent, size limits, and signing context.

## Blockers

- Missing Railway monthly billing cost input for Mode 1 `C_seat`.
- Missing `OPENROUTER_API_KEY` for Mode 2 inference spend.
- Missing funded-wallet/HL signing credentials for live fills and builder-fee realization.
