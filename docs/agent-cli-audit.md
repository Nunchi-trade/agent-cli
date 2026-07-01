# Agent CLI Audit

Validated: 2026-06-23T15:33:05Z

## Scope

This audit checked the local `agent-cli` branch for:

- direct Railway one-click deploy links that bypass `auth.nunchi.trade`
- public HTTP/API control surfaces
- root, OpenClaw, and Hermes Railway deploy paths that could be used outside web-auth
- lightweight CLI/test health
- README drift against the current branch

## Working

- The local editable package is installed and imports under the active environment.
- `python3 -m cli.main strategies` renders the current strategy catalog: 18 strategies and 3 YEX markets.
- `python3 -m cli.main setup check` runs and confirms the Hyperliquid SDK, testnet config, builder fee config, and data directory.
- `python3 -m cli.main run avellaneda_mm --mock --max-ticks 1` completes successfully and places mock orders.
- `python3 -m cli.main apex run --mock --max-ticks 1` completes successfully after the startup logging format fix in this pass.
- All 18 registered strategies import cleanly and complete a one-tick isolated mock smoke run.
- Focused auth/web-auth tests passed: `tests/test_entrypoint.py`, `tests/test_config.py`, `tests/test_web_auth.py`, and `tests/test_pair_money_cli.py`.
- Full local suite passed under the available shell: `1317 passed, 3 warnings`.
- Hosted-agent runtime entrypoints compile without the removed public deploy templates.

## Broken Or Risky Before This Pass

- `README.md` published direct public Railway template links. These allowed one-click deployment straight from GitHub without going through `auth.nunchi.trade`, so subscription and lifecycle checks could be bypassed.
- `deploy/openclaw-railway/src/server.js` and `deploy/hermes-railway/src/server.js` exposed `/api/pause`, `/api/resume`, and `/api/configure` without auth. Those are mutating control endpoints.
- `scripts/entrypoint.py` had optional auth for the same mutating control endpoints: if `API_AUTH_TOKEN` was unset, the endpoints were open.
- `deploy/openclaw-railway/Dockerfile` and `deploy/hermes-railway/Dockerfile` used `COPY ../../ .` while their Railway configs did not pin `dockerfilePath`. That is fragile because Docker cannot copy files outside the build context.
- `docs/api-reference.md` stated all endpoints were unauthenticated and documented pause/resume without auth headers.
- `README.md` still claimed 14 strategies and 16/13 MCP tools while the current branch exposes 18 strategies and 23 MCP tools.
- `hl jobs` was registered as a live CLI group, but it was a design-only skeleton: most commands printed `Not yet implemented — design PR only`, and the backing engines/events/custody modules raised `NotImplementedError`.
- The active local `python3` is 3.9.6 while `pyproject.toml` requires `>=3.10`; release validation should use the hosted-agent runtime environment.

## Remediated

- `README.md` now routes managed launch through `https://auth.nunchi.trade` and no longer contains public `railway.com/new/template` links or Railway button badges.
- `scripts/entrypoint.py` now fails closed for mutating control endpoints unless `API_AUTH_TOKEN` is set.
- Node and Python CORS allow `X-API-Token` as an alternate token header.
- `docs/api-reference.md` now distinguishes unauthenticated read endpoints from token-required mutating control endpoints.
- Public Docker/Railway deployment files were removed: root `Dockerfile`, root `railway.toml`, and the `deploy/openclaw-railway` / `deploy/hermes-railway` templates.
- `tests/test_deploy_policy.py` prevents direct Railway template links and public Docker/Railway deployment configs from returning.
- `tests/test_entrypoint.py` covers fail-closed control auth and `X-API-Token`.
- `tests/test_logging_config.py` covers the startup currency formatting that previously emitted logging errors.
- `README.md` now reflects the observed branch counts: 18 strategies, 23 MCP tools, and 1,317 passing tests.
- The nonfunctional `hl jobs` skeleton and its stale design spec were removed so the CLI no longer advertises commands that cannot run.

## Remaining Follow-Ups

- Re-run the full suite under Python 3.10+ or the hosted-agent runtime before release. The local shell only exposed Python 3.9.6, even though all tests passed there.
- If `auth.nunchi.trade` has a more specific hosted-agent route than the root URL, update the README launch link to that canonical URL.
