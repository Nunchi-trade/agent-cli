# MCP Pricing Measurements

Measured on 2026-07-01 for the hosted MCP tools runner path.

## Harness

Run:

```bash
python3 scripts/pricing_measure.py --output tmp/pricing-measurement-local.json
railway run --service hosted-trading-mcp --environment production -- python3 scripts/pricing_measure.py --output tmp/pricing-measurement-runner-env.json
python3 scripts/pricing_measure.py --job-profile maker --job-profile taker --job-profile hedge --railway-metrics --output tmp/pricing-job-profiles.json
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
- Recommended beta free cap: about 20 hosted MCP discovery/read or
  safety-gated execution calls before subscription or upgrade nudges.
  `trade` and `funding_hedge_execute` remain safety-gated but are eligible to
  spend this 20-call clone/local quota before paid access is required.

## Job Profiles

`pricing_measure.py` now emits deterministic monthly usage estimates for the
three Sam-facing workflow profiles:

- `hedge`: 2 seats, about 8,700 MCP calls, 30 paid-compute calls, about $5
  inference, and about 93.5 credits; recommended placeholder plan is
  `hosted-mcp-inference-growth`.
- `maker`: 5 seats, about 45,150 MCP calls, 150 paid-compute calls, BYO
  inference, and about 459 credits; recommended placeholder plan is
  `hosted-mcp-tools-team`.
- `taker`: 2 seats, about 5,500 MCP calls, 500 paid-compute calls, about $15
  inference, and about 95 credits; recommended placeholder plan is
  `hosted-mcp-inference-growth`.

Credit rates mirror web-auth: 0.01 credits per hosted MCP call, 0.05 credits per
paid-compute call, and 1 credit per $1 of Nunchi/OpenRouter inference spend.
The working OpenRouter starter plan is 100 credits/month; MCP-only tiers stay
cheaper because BYO inference spend is outside Nunchi billing.

These are pricing simulations, not funded-fill proof. The funded maker/taker
fill path is still gated by wallet credentials and testnet funding.

## Economics

Mode 1, hosted MCP tools:

- `C_seat` can now be computed from live Railway resource metrics plus a
  configured rate card. `--railway-metrics` captures CPU/memory/network/disk
  summaries, and `pricing_measure.py` converts them into
  `estimatedRailwayCostUsd`, `monthlyRunRateUsd`, and per-profile margin
  outputs when these env vars are set:
  - `RAILWAY_VCPU_HOUR_USD`
  - `RAILWAY_MEMORY_GB_HOUR_USD`
  - `RAILWAY_NETWORK_GB_USD`
  - `RAILWAY_DISK_GB_HOUR_USD`
  - optional `RAILWAY_BASELINE_MONTHLY_USD`
- Invoice data is now a calibration input, not a runtime dependency. Use
  `RAILWAY_SHARED_RUNTIME_MONTHLY_USD` or `--runtime-monthly-usd` only when
  overriding the live estimate with a reconciled invoice number.
- Live metering: run the sampler with `--emit-railway-runtime-cost` plus
  `NUNCHI_METERING_USAGE_URL`, `NUNCHI_METERING_TOKEN`, and
  `NUNCHI_ACCOUNT_ID` to upload `metric_type=runtime_cost` rows into web-auth.
  Web-auth persists those rows in the MCP subscription metering record
  (Postgres JSONB when `SUBSCRIPTION_STORE=postgres`) and exposes rolling
  1h/24h/month-to-date cost, stale-sampler state, and a
  `/api/billing/subscription/margin-dashboard` rollup for Railway cost,
  OpenRouter spend, credit burn, and estimated margin by account/tier. Those
  dashboard endpoints are internal-only and require the web-auth
  `INTERNAL_COSTING_DASHBOARD_TOKEN`/`COSTING_DASHBOARD_TOKEN`.

Mode 2, hosted MCP tools plus Nunchi/OpenRouter inference:

- Live OpenRouter spend can be measured with `--openrouter-live`. The probe uses
  `max_tokens=16` because current OpenRouter providers reject smaller completion
  caps.
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
  produce builder-code/builder-fee economics. It now spends the clone/local
  20-call free quota together with `funding_hedge_execute`, then requires paid
  hosted MCP access after that allowance is exhausted.

## Blockers

- Missing Railway rate-card env vars for live estimated Mode 1 `C_seat` if
  `--railway-cost-strict` is enabled.
- Missing `OPENROUTER_API_KEY` only in environments where `--openrouter-live`
  should run.
- Missing funded-wallet/HL signing credentials for live fills and builder-fee realization.
