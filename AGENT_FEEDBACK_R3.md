# Agent Feedback Round 3: Critical Execution Layer Bug

> Found during live mainnet trading. This is the most severe issue discovered — it makes all market-making strategies unprofitable by design.

---

## 1. `hl_proxy.py` Sends IOC Market-Crossing Orders for All Strategies (Priority: Critical — P0)

**Problem:** `parent/hl_proxy.py` places all orders as IOC (Immediate-or-Cancel) at market-crossing prices. From the source:

```
Uses market-crossing IOC prices to guarantee execution:
buys at ask + 0.5% slippage, sells at bid - 0.5% slippage.
```

This means every order — including market-making bid/ask quotes — is a market order that crosses the spread and pays taker fees on both sides. The limit prices computed by the strategy layer are ignored.

**Impact:** All 6 MM strategies (simple_mm, avellaneda_mm, engine_mm, regime_mm, grid_mm, liquidation_mm) are architecturally broken. They cannot function as market makers because they can never rest passive orders on the book.

**Evidence from mainnet (avellaneda_mm, ETH-PERP, 0.02 ETH):**
```
T1: bought @ 2035.3, sold @ 2033.8 → bought HIGH, sold LOW = -$0.03
T2: bought @ 2042.6, sold @ 2042.7 → spread $0.002, fees ~$0.03 = net negative
```

Every tick lost money to fees. $99 account bled $1.22 in ~5 minutes before we killed it. The strategy's Avellaneda-Stoikov math correctly computes optimal bid/ask prices, but the execution layer discards them.

**What should happen:**

The strategy computes `bid_price` and `ask_price` via `StrategyDecision.limit_price`. These should be submitted as resting GTC or ALO (Add Liquidity Only) limit orders that sit on the book until filled by a counterparty. The MM earns the spread as a maker (zero or negative fees on HL).

**What actually happens:**

The execution layer ignores `limit_price`, fetches the current orderbook, and submits:
- Buy at `ask * 1.005` (IOC) — guarantees immediate fill as taker
- Sell at `bid * 0.995` (IOC) — guarantees immediate fill as taker

This is correct behavior for directional strategies (momentum_breakout, mean_reversion, aggressive_taker) where you want guaranteed execution. But it defeats the entire purpose of market-making strategies.

**Suggested fix:**

```python
# In HLProxy.place_orders():

# For MM strategies — use the strategy's computed limit price, rest as GTC
if decision.action == "place_order":
    order_type = {"limit": {"tif": "Gtc"}}  # or "Alo" for maker-only
    price = decision.limit_price  # use strategy's price, don't override

# For directional entries — keep IOC market-crossing (current behavior)
if decision.action == "market_entry":
    order_type = {"limit": {"tif": "Ioc"}}
    price = ask * 1.005 if side == "buy" else bid * 0.995
```

Alternatively, add an `order_type` field to `StrategyDecision` so each strategy can specify its preferred execution mode.

---

## 2. WOLF Entry Orders: Invalid Size and Rate Limiting (Priority: High)

**Problem:** WOLF detected valid signals on mainnet (DOGE IMMEDIATE_MOVER with OI +3367%, CRV with OI +18.9%) but failed to execute entries due to two issues:

### 2a. Order Size Formatting

```
No fill: buy 2666.4055 DOGE-PERP @ 0.1 -- Order has invalid size.
No fill: buy 997.557 CRV-PERP @ 0.3 -- Order has invalid size.
```

The size decimal precision doesn't match HL's `szDecimals` for these instruments. DOGE likely requires integer sizes, CRV likely requires 1 decimal. The order manager should query `metaAndAssetCtxs` for each instrument's `szDecimals` and round accordingly.

### 2b. API Rate Limiting (HTTP 429)

```
Entry failed for DOGE-PERP: (429, None, 'null', None, {...})
Entry failed for OP-PERP: (429, None, 'null', None, {...})
```

The movers scan queries 229 assets in rapid succession, exhausting the HL API rate limit. Subsequent order placement calls get 429'd. The scan needs request pacing (e.g., batched queries or delays between calls) and the order placement should retry with exponential backoff on 429 responses.

---

## 3. WOLF Budget Scaling (Priority: Medium)

**Problem:** WOLF defaults to $10,000 budget with 10x leverage. The `--budget` flag works, but at small budgets ($98 in our case) the per-slot margin ($49) produces order sizes that hit HL minimum order value thresholds for many instruments. WOLF doesn't filter instruments by minimum tradeable size relative to the available margin per slot.

**Recommendation:** Add a pre-filter that excludes instruments where `margin_per_slot / price < min_order_size` before the movers/scanner evaluation. This prevents wasting signal detection on instruments that can't be traded at the current budget.

---

## Summary

| Issue | Severity | Strategies Affected |
|-------|----------|-------------------|
| IOC execution for MM strategies | P0 — Critical | simple_mm, avellaneda_mm, engine_mm, regime_mm, grid_mm, liquidation_mm |
| Order size precision | High | All strategies via WOLF |
| API rate limiting | High | WOLF (movers scan) |
| Budget-aware instrument filtering | Medium | WOLF |

The IOC issue is the most important — it means no market-making strategy shipped in this CLI can be profitable. The strategy math is correct; the execution layer defeats it.

---

*Found during live mainnet trading with real capital. Account lost $1.22 in ~5 minutes before the issue was identified and the strategy was killed.*
