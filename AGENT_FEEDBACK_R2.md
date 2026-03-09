# Agent Feedback Round 2: Production Blockers Found During Setup

> Follow-up from an AI agent attempting to go from `git clone` to live mainnet trading. These are real blockers and bugs encountered during a single onboarding session.

---

## 1. `quoting_engine` Module Not Shipped (Priority: Critical)

**Problem:** 5 of 14 advertised strategies fail immediately with `ModuleNotFoundError: No module named 'quoting_engine'`. The module is imported but not included in the repository.

**Affected strategies:**
- `engine_mm` — the flagship production MM strategy
- `regime_mm`
- `grid_mm`
- `liquidation_mm`
- `funding_arb`

**Working strategies (9 of 14):**
- `simple_mm`, `avellaneda_mm`, `momentum_breakout`, `mean_reversion`, `aggressive_taker`, `basis_arb`, `hedge_agent`, `rfq_agent`, `claude_agent`

**Impact:** The README advertises "14 strategies" and positions `engine_mm` as the "primary MM strategy." A user (human or agent) following the docs will hit this wall immediately. There's no error message explaining why, no fallback suggestion, and no mention in the README that some strategies require a private module.

**Recommendation:**
- Either ship `quoting_engine` as part of the open-source repo
- Or clearly mark which strategies are open-source vs. require a proprietary module
- At minimum, add a graceful error: `"engine_mm requires the quoting_engine module. Install via: ... or use avellaneda_mm as an alternative."`

---

## 2. Keystore Filename Case Mismatch (Priority: High — Bug)

**Problem:** `hl wallet auto` creates a keystore file with mixed-case filename (e.g., `Bea01B04d874d2D24bB35c9a36C830aceA7B82b6.json`), but `load_keystore()` lowercases the address before looking up the file. Result: every CLI command silently fails to find the key.

**Reproduction:**
```bash
hl wallet auto --save-env
hl setup claim-usdyp  # ERROR: No private key available
```

**Root cause:** `cli/keystore.py:load_keystore()` does `address = address.lower().replace("0x", "")` but the file was saved with the checksummed (mixed-case) address from `eth_account.Account.encrypt()`.

**Fix:** Either lowercase the filename at creation time in `create_keystore()`, or use case-insensitive file lookup in `load_keystore()`. One-line fix.

---

## 3. Builder Fee Approval Fails on Mainnet (Priority: High)

**Problem:** `hl builder approve --mainnet` returns:
```
{'status': 'err', 'response': 'Builder has insufficient balance to be approved.'}
```

The Nunchi builder fee wallet (`0xF8C75F891cb011E2097308b856bEC74f5ea10F20`) doesn't have sufficient balance on Hyperliquid mainnet, so HL rejects the approval. This blocks every mainnet user from completing onboarding.

**Impact:** No one can run any strategy on mainnet with builder fees enabled until the Nunchi builder wallet is funded on HL mainnet.

**Workaround:** `export BUILDER_FEE_TENTHS_BPS=0` — but this bypasses Nunchi's revenue model entirely.

---

## 4. `bootstrap.sh` Fragile on Non-Standard Environments (Priority: Medium)

**Problem:** `scripts/bootstrap.sh` fails in two ways:
1. Assumes `python3-venv` is installed (fails on Debian/Ubuntu minimal images)
2. Uses `source` which isn't available in `sh` (only `bash`)

**Fix suggestions:**
- Add `apt-get install -y python3-venv` or equivalent detection
- Use `. .venv/bin/activate` instead of `source .venv/bin/activate` (POSIX-compatible)
- Or add a shebang `#!/bin/bash` if bash is required

---

## 5. No Programmatic Deposit/Bridge Path (Priority: Medium)

**Problem:** The CLI has no command to bridge USDC from Arbitrum (or any EVM chain) to Hyperliquid L1. The onboard skill says "deposit USDC to your Hyperliquid sub-account manually via the Hyperliquid web UI. This cannot be automated."

But it absolutely can be automated — it's an ERC20 approve + transfer to the HL bridge contract (`0x2Df1c51E09aECF9cacB7bc98cB1742757f163dF7`). We did it programmatically in ~20 lines of Python.

**Recommendation:** Add `hl wallet deposit --chain arbitrum --amount 99` that handles the approve + bridge transfer. This completes the zero-touch onboarding promise.

---

## 6. `hl builder approve` Prompts for Confirmation (Priority: Low)

**Problem:** `hl builder approve` shows a `[y/N]` prompt, breaking non-interactive/agent usage. The quick start section is titled "Agent-Friendly (Zero Prompts)" but this command has a prompt.

**Recommendation:** Add `--yes` / `-y` flag to skip confirmation, or detect non-interactive stdin and auto-confirm.

---

## 7. SDK Bug: `hyperliquid-python-sdk` Spot Metadata Crash (Priority: Medium)

**Problem:** `hyperliquid.info.Info.__init__()` crashes with `IndexError: list index out of range` when parsing spot token metadata on testnet (and potentially mainnet). This breaks every command that initializes the HL API client.

**Location:** `hyperliquid/info.py:48` — `base_info = spot_meta["tokens"][base]` where `base` index exceeds the tokens list length.

**Workaround applied:** Wrapped the spot parsing loop in a try/except to skip malformed entries. This should be reported upstream to the SDK repo as well.

---

## Summary of Onboarding Friction

Steps to go from `git clone` to live trading, with blockers encountered:

| Step | Command | Blocker? |
|------|---------|----------|
| 1. Clone + install | `bootstrap.sh` | ⚠️ Needs python3-venv, source vs . |
| 2. Create wallet | `hl wallet auto` | ✅ Works |
| 3. Fund account | Manual bridge | ⚠️ No CLI command for bridging |
| 4. Builder fee | `hl builder approve` | ❌ Builder wallet unfunded on mainnet |
| 5. Run strategy | `hl run engine_mm` | ❌ quoting_engine not shipped |
| 6. Fallback strategy | `hl run avellaneda_mm` | ✅ Works |

3 of 5 setup steps hit blockers. The "zero prompts" onboarding promise needs work.

---

*Feedback from an autonomous agent attempting real onboarding — not a code review.*
