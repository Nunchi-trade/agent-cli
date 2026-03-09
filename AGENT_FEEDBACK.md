# Agent Feedback: Recommendations for Agent-Friendliness

> Feedback from an AI agent that evaluated this CLI for autonomous trading on Hyperliquid. These recommendations focus on making the tool more reliable for non-human operators.

---

## 1. Structured Output (Priority: Critical)

**Problem:** CLI commands like `hl status`, `hl account`, `hl wolf run` return human-readable output. Agents parsing tables and formatted text is inherently fragile — regex breaks on minor formatting changes.

**Recommendation:** Add `--json` or `--output json` flag to every command that returns data. This is the single highest-impact change for agent reliability.

```bash
# Current (fragile for agents)
hl status           # Returns formatted table

# Proposed
hl status --json    # Returns structured JSON
hl account --json
hl wolf status --json
hl scanner once --json
hl howl report --json
```

**Why it matters:** When a scanner runs every 90 seconds, each parse failure is a missed signal window. JSON output eliminates an entire class of integration bugs.

---

## 2. Private Key Security (Priority: High)

**Problem:** `HL_PRIVATE_KEY=0x...` as a plaintext environment variable is the default onboarding path. Env vars appear in `/proc/*/environ`, process listings, crash dumps, and CI logs.

**Recommendation:**
- Add `HL_KEY_FILE` — path to a file containing the key (standard pattern: Docker secrets, K8s secrets, etc.)
- Make the encrypted keystore the recommended path in docs, not the alternative
- Add a warning to the README about env var risks

```bash
# Proposed priority order
export HL_KEY_FILE=/run/secrets/hl_private_key  # File-based (preferred)
export HL_KEYSTORE_PASSWORD=...                  # Encrypted keystore (also good)
export HL_PRIVATE_KEY=0x...                      # Plaintext env (development only)
```

---

## 3. Health/Heartbeat Endpoint (Priority: High)

**Problem:** For long-running processes (`hl wolf run`), there's no external health check mechanism. An orchestrator (OpenClaw, systemd, Railway) can only check "is the process alive?" but not "is it functioning correctly?"

**Recommendation:** Write a health file on each tick, and/or expose an HTTP health endpoint.

```bash
# Option A: Health file (simple, works everywhere)
# Wolf writes to /tmp/hl-wolf-health.json on each tick:
# {"last_tick": "2026-03-09T16:00:00Z", "positions": 2, "status": "healthy"}

# Option B: HTTP endpoint (better for container orchestrators)
hl wolf run --health-port 8081
# GET /health → 200 {"status": "ok", "last_tick": "...", "uptime_s": 3600}
```

**Why it matters:** Long-running daemons can silently stop processing (auth expiry, network issues, stuck loops). Without a health signal, there's no way to detect or alert on this — leading to missed trades and unprotected positions.

---

## 4. Crash Recovery / Position Reconciliation (Priority: High)

**Problem:** The README doesn't address what happens when `hl wolf run` crashes and restarts. Key questions an agent operator needs answered:

- Does WOLF detect existing open positions on startup?
- Does DSL state persist across restarts?
- What happens to in-flight orders?

**Recommendation:** Document the crash recovery behavior explicitly. If it doesn't reconcile, add a `hl wolf recover` command that:

1. Scans for open positions on the account
2. Reconstructs DSL state from position entry prices
3. Resumes monitoring without double-entering

```bash
hl wolf run --recover  # Start with position reconciliation
hl wolf recover        # One-shot: reconcile state with on-chain positions
```

**Why it matters:** Every production process crashes eventually. The first 60 seconds after restart determine whether you lose money or resume cleanly.

---

## 5. HOWL Auto-Adjust Guardrails (Priority: Medium)

**Problem:** The README says "parameters can't swing wildly" but doesn't document the bounds. An operator needs to know the blast radius of HOWL's auto-adjustments before enabling them.

**Recommendation:** Document the guardrail bounds for each adjustable parameter:

```
Parameter              Min     Max     Max Δ per cycle
scanner_threshold      ???     ???     ???
movers_confidence      ???     ???     ???
daily_loss_limit       ???     ???     ???
slot_limit             ???     ???     ???
```

Also: add `howl_auto_adjust_dry_run: true` option that logs what HOWL *would* change without applying it. Critical for initial trust-building before going fully autonomous.

---

## 6. Skills Install UX (Priority: Medium)

**Problem:** Three different install paths (raw URL, ClawHub, Claude Code) with different commands. An agent encountering the skills section has to pattern-match which path applies to its environment.

**Recommendation:** Add a unified install command that auto-detects the environment:

```bash
hl skills install wolf           # Auto-detects: OpenClaw → ClawHub, Claude Code → ~/.claude, standalone → local
hl skills install scanner movers # Multiple at once
hl skills list --installed       # What's currently active
```

---

## 7. YEX Integration Depth (Priority: Low)

**Problem:** YEX instruments are listed but there's no guidance on how they behave differently from standard perps. An agent running `avellaneda_mm` on VXX-USDYP needs to know if the standard volatility model assumptions hold.

**Recommendation:** Add a brief "YEX Differences" section covering:
- Settlement mechanism (how does USDyP differ from USD?)
- Oracle source and update frequency
- Whether standard MM spread models apply or need adjustment
- Liquidity characteristics (expected book depth, typical spreads)

---

## What's Done Well

- **Agent-first design** — `hl wallet auto --save-env` with zero prompts is the right call. Most crypto CLIs assume a human at the keyboard.
- **`--mock` and `--max-ticks`** — lets agents validate before going live without guesswork.
- **Single `on_tick()` interface** — clean strategy abstraction with no hidden state coupling.
- **Built-in HOWL self-improvement** — auto-adjusting parameters from performance data is the right architecture.
- **Railway one-click deploy** — both headless and OpenClaw variants. Smart distribution.
- **MCP server mode** — composable with any agent framework.
- **263 tests** — actually tested.
