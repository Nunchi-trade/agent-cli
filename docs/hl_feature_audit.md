# Hyperliquid Feature Audit

**Date:** 2026-03-23
**Scope:** All 17 strategies in `strategies/`, adapter layer (`cli/hl_adapter.py`, `parent/hl_proxy.py`), and quoting engine integration (`strategies/_engine_base.py`).

---

## 1. HL-Specific Feature Inventory

### F1: ALO Orders (Add Liquidity Only / Post-Only)

- **What:** ALO (`tif="Alo"`) is Hyperliquid's post-only order type. If the order would cross the book (become a taker), HL rejects it. The adapter catches this rejection and falls back to GTC.
- **Where:** `cli/hl_adapter.py` lines 326-329 (`place_order` method). Fallback: `Alo` rejected -> retry with `Gtc`.
- **Why it matters:** Maker rebates on HL. Strategies using ALO pay zero or negative fees; without it, every order pays taker fees.

### F2: Builder Fee

- **What:** HL-native per-order fee mechanism (`BuilderInfo`). Every order sent through `DirectHLProxy.place_order()` attaches a builder fee (default: 10 bps to `0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0`). Fee field `f` is in tenths of basis points.
- **Where:** `cli/hl_adapter.py` lines 296-299 (enforcement point), `cli/builder_fee.py` (config). Also attempted on trigger orders (line 485).
- **Why it matters:** **Revenue-critical.** This is the sole monetization path for Nunchi on every order. All order paths flow through `place_order()`, making it the single enforcement point.

### F3: Trigger Orders (Exchange-Side Stop Losses)

- **What:** HL supports server-side trigger orders (`triggerPx`, `isMarket`, `tpsl="sl"`). These execute on the exchange when the trigger price is hit, without requiring the client to be online.
- **Where:** `cli/hl_adapter.py` lines 469-507 (`place_trigger_order`, `cancel_trigger_order`).
- **Why it matters:** Enables exchange-side risk management. Without it, stop losses must be software-side (client must be running).

### F4: YEX Market Mapping

- **What:** Nunchi HIP-3 yield perpetuals use a `yex:` prefixed coin namespace on HL (e.g., `VXX-USDYP` -> `yex:VXX`). The `_to_hl_coin()` function handles bidirectional mapping. Three YEX markets defined: `VXX-USDYP`, `US3M-USDYP`, `BTCSWP-USDYP`.
- **Where:** `cli/hl_adapter.py` lines 43-53 (`_to_hl_coin`), `cli/strategy_registry.py` lines 100-113 (`YEX_MARKETS`), `cli/hl_adapter.py` lines 121-144 (`_get_yex_snapshot`).
- **Why it matters:** YEX markets are HL-exclusive products. No other exchange has these instruments.

### F5: Price Rounding (5 Significant Figures)

- **What:** HL uses 5 significant figures for all prices. The tick size is price-dependent (e.g., BTC@60000 -> tick=1.0, ETH@3000 -> tick=0.1). All order prices must be rounded to this grid.
- **Where:** `cli/hl_adapter.py` lines 233-279 (`_get_price_tick`, `_round_price`). Constant `SIG_FIGS = 5` at line 24.
- **Why it matters:** Orders with non-conforming prices are rejected by HL. Every exchange has different tick rules.

### F6: szDecimals (Size Precision)

- **What:** Each HL asset has a `szDecimals` value from the meta API that defines the number of decimal places allowed for order sizes (e.g., BTC=3, ETH=4, DOGE=0). Sizes must be rounded to this precision.
- **Where:** `cli/hl_adapter.py` lines 262-274 (`_get_sz_decimals`), applied at line 304-305.
- **Why it matters:** Orders with non-conforming sizes are rejected. The meta API call is HL-specific.

### F7: L2 Orderbook Format

- **What:** HL returns L2 orderbook data in a specific JSON structure: `{"levels": [[bids...], [asks...]]}` where each level has `{"px": "...", "sz": "...", "n": ...}`. Both `HLProxy` and `DirectHLProxy` parse this format directly.
- **Where:** `parent/hl_proxy.py` lines 276-278 (`get_snapshot`), `cli/hl_adapter.py` lines 126-131 (`_get_yex_snapshot`).
- **Why it matters:** Every exchange has a different orderbook response format. This parsing is tightly coupled to HL's API.

### F8: Funding Rate Format

- **What:** HL provides funding rates as decimal values in the `funding` field of asset contexts (e.g., `"0.0001"` = 0.01% per 8h). The `MarketSnapshot.funding_rate` field carries this directly from HL.
- **Where:** `parent/hl_proxy.py` lines 63 (mock funding), `parent/hl_proxy.py` lines 181/187/193 (mock asset contexts with funding). Consumed by all strategies that read `snapshot.funding_rate`.
- **Why it matters:** Funding rate format/periodicity differs across exchanges (8h on HL, 1h on some others).

### F9: HyperliquidFundingRate (Quoting Engine Integration)

- **What:** Direct import of `HyperliquidFundingRate` from the quoting engine. This is an HL-specific funding rate adapter that feeds into `CrossVenueFundingRate` for composite rate calculation.
- **Where:** Imported and instantiated in `engine_mm.py` (line 22, 50), `funding_arb.py` (line 23, 58), `regime_mm.py` (line 22, 53), `liquidation_mm.py` (line 22, 60).
- **Why it matters:** Directly couples 4 strategies to the HL funding rate format via the quoting engine.

### F10: HL Meta API (Universe Metadata)

- **What:** `meta()` and `meta_and_asset_ctxs()` endpoints return HL's full asset universe with `szDecimals`, `funding`, `openInterest`, `dayNtlVlm`, `markPx`, `prevDayPx`.
- **Where:** `parent/hl_proxy.py` lines 407-410 (`get_meta_and_asset_ctxs`), `cli/hl_adapter.py` lines 267-268 (meta for szDecimals).
- **Why it matters:** The response schema is HL-specific. Used for market scanning and size precision.

### F11: Candle Format

- **What:** HL candles use `candles_snapshot(coin, interval, start, end)` returning `[{"t": ..., "o": "...", "h": "...", "c": "...", "l": "...", "v": "..."}]` with string-encoded OHLCV values.
- **Where:** `parent/hl_proxy.py` lines 400-405 (`get_candles`).
- **Why it matters:** String-encoded values and the API shape are HL-specific.

### F12: API Circuit Breaker

- **What:** `DirectHLProxy` tracks consecutive API failures and raises `APICircuitBreakerOpen` after 5 failures to prevent trading blind. Resets on successful snapshot with non-zero price.
- **Where:** `cli/hl_adapter.py` lines 25, 31-33, 91-119.
- **Why it matters:** Built specifically around HL's failure modes. Portable concept but tuned for HL's reliability patterns.

---

## 2. Strategy x Feature Matrix

| Strategy | F1: ALO | F2: Builder Fee | F3: Trigger | F4: YEX Map | F5: Price Round | F6: szDecimals | F7: L2 Format | F8: Funding Format | F9: HL Funding Rate | F10: Meta API | F11: Candle Format |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **simple_mm** | - | I | - | I | I | I | I | - | - | - | - |
| **avellaneda_mm** | - | I | - | I | I | I | I | - | - | - | - |
| **mean_reversion** | - | I | - | I | I | I | I | - | - | - | - |
| **hedge_agent** | - | I | - | I | I | I | I | - | - | - | - |
| **rfq_agent** | - | I | - | I | I | I | I | - | - | - | - |
| **aggressive_taker** | - | I | - | I | I | I | I | - | - | - | - |
| **claude_agent** | - | I | - | I | I | I | I | - | - | - | - |
| **engine_mm** | - | I | - | I | I | I | I | D | **D** | - | - |
| **funding_arb** | - | I | - | I | I | I | I | D | **D** | - | - |
| **regime_mm** | - | I | - | I | I | I | I | D | **D** | - | - |
| **liquidation_mm** | - | I | - | I | I | I | I | D | **D** | - | - |
| **momentum_breakout** | - | I | - | I | I | I | I | - | - | - | - |
| **grid_mm** | - | I | - | I | I | I | I | - | - | - | - |
| **basis_arb** | - | I | - | I | I | I | I | D | - | - | - |
| **simplified_ensemble** | - | I | - | I | I | I | I | - | - | - | - |
| **funding_momentum** | - | I | - | I | I | I | I | D | - | - | - |
| **oi_divergence** | - | I | - | I | I | I | I | - | - | - | - |
| **trend_follower** | - | I | - | I | I | I | I | - | - | - | - |

**Legend:**
- **D** = Direct dependency (strategy code imports or explicitly uses the HL feature)
- **I** = Indirect/Implicit dependency (feature is in the adapter layer; strategy is affected but does not reference it)
- **-** = No dependency

**Notes on the matrix:**
- F1 (ALO), F3 (Trigger), F10 (Meta API), F11 (Candle Format) are adapter-level features not directly used by any strategy's `on_tick()`. They are available to the execution layer.
- F2 (Builder Fee), F4 (YEX Map), F5 (Price Round), F6 (szDecimals), F7 (L2 Format) affect **all 17 strategies** implicitly because every order flows through `DirectHLProxy.place_order()` and every snapshot flows through `get_snapshot()`.
- F8 (Funding Format) is consumed directly by 6 strategies that read `snapshot.funding_rate` for trading logic: `engine_mm`, `funding_arb`, `regime_mm`, `liquidation_mm`, `basis_arb`, `funding_momentum`.
- F9 (HyperliquidFundingRate) is a hard import in 4 quoting-engine strategies: `engine_mm`, `funding_arb`, `regime_mm`, `liquidation_mm`.

---

## 3. Criticality Rating

| Feature | Criticality | Rationale |
|---|---|---|
| **F1: ALO Orders** | **Degraded** | System works without it (falls back to GTC/IOC), but loses maker rebates. MM strategies become significantly less profitable paying taker fees on every order. |
| **F2: Builder Fee** | **Required** | Revenue stops completely without it. No builder fee = no Nunchi income. This is the business model. |
| **F3: Trigger Orders** | **Optional** | Convenience feature for server-side stop losses. Software-side stops work as a fallback (already the case for most strategies). |
| **F4: YEX Market Mapping** | **Required** (for YEX markets) | YEX instruments are HL-exclusive. Without mapping, these 3 markets are untradeable. For standard perps, this feature is unused. |
| **F5: Price Rounding** | **Required** | Orders rejected without correct tick-size rounding. Every exchange has equivalent logic but with different rules. |
| **F6: szDecimals** | **Required** | Orders rejected without correct size precision. Same as F5 -- required but exchange-specific. |
| **F7: L2 Orderbook Format** | **Required** | No market data without correct parsing. All snapshot data depends on this. |
| **F8: Funding Rate Format** | **Required** | 6 strategies use funding rates in trading logic. Wrong format = wrong signals = bad trades. |
| **F9: HyperliquidFundingRate** | **Required** | 4 quoting-engine strategies hard-import this class. They fail to instantiate without it. |
| **F10: Meta API** | **Required** | szDecimals lookup and market scanning depend on this. Without it, szDecimals defaults to 1 (degraded but not broken for most assets). |
| **F11: Candle Format** | **Degraded** | Only used by mock proxy and external tooling (Radar/Pulse). No strategy's `on_tick()` directly fetches candles. |
| **F12: Circuit Breaker** | **Optional** | Safety mechanism tuned for HL. Without it, strategies trade blind during API outages. Concept is portable. |

---

## 4. Abstraction Feasibility

| Feature | Feasibility | Effort | Notes |
|---|---|---|---|
| **F1: ALO Orders** | **Easy** | Low | Already behind `place_order()` interface. Map to equivalent post-only on other exchanges (Binance: `timeInForce=GTX`, Bybit: `PostOnly`). |
| **F2: Builder Fee** | **Medium** | Medium | Conceptually HL-only (no other exchange has builder fees). For multi-exchange, this becomes a no-op on non-HL venues. Need to separate "Nunchi revenue fee" from "exchange-native builder fee". |
| **F3: Trigger Orders** | **Easy** | Low | Already behind `place_trigger_order()` interface. Most exchanges support conditional/trigger orders with similar semantics. |
| **F4: YEX Market Mapping** | **Hard** | High | Deeply coupled to HL's `yex:` namespace. YEX markets don't exist on other exchanges. For multi-exchange support, this is an HL-only code path that needs to be isolated. |
| **F5: Price Rounding** | **Medium** | Medium | The concept (tick-size rounding) is universal, but the rule (5 sig-figs) is HL-specific. Need an exchange-agnostic `TickSizeProvider` interface. Each exchange adapter provides its own implementation. Currently computed inline in `_get_price_tick`. |
| **F6: szDecimals** | **Medium** | Medium | Same pattern as F5. Need a `SizePrecisionProvider` that each exchange adapter implements. Currently fetched from HL meta API and cached. |
| **F7: L2 Orderbook Format** | **Medium** | Medium | `MarketSnapshot` is already exchange-agnostic. The parsing in `get_snapshot()` is the only HL-specific part. A new exchange adapter just needs to produce the same `MarketSnapshot` from its own L2 format. |
| **F8: Funding Rate Format** | **Easy** | Low | `MarketSnapshot.funding_rate` is already a plain float. Just need each exchange adapter to normalize its funding rate to the same units (decimal, per-8h or annualized -- pick one convention). |
| **F9: HyperliquidFundingRate** | **Hard** | High | 4 strategies directly import `HyperliquidFundingRate` from the quoting engine. This class would need to be replaced with an exchange-agnostic `FundingRateSource` interface, and the quoting engine itself would need refactoring. |
| **F10: Meta API** | **Medium** | Medium | Used for szDecimals and market scanning. Need a `MarketMetadataProvider` interface. Each exchange has equivalent metadata endpoints but different schemas. |
| **F11: Candle Format** | **Easy** | Low | Already behind `get_candles()`. Normalize to common OHLCV dict format. String-to-float conversion is trivial. |
| **F12: Circuit Breaker** | **Easy** | Low | Exchange-agnostic concept. Already isolated in `DirectHLProxy`. Any adapter can implement the same pattern. |

---

## 5. Summary

### Strategy Groupings by HL Coupling

**Loosely coupled (adapter-only dependency, 11 strategies):**
`simple_mm`, `avellaneda_mm`, `mean_reversion`, `hedge_agent`, `rfq_agent`, `aggressive_taker`, `claude_agent`, `momentum_breakout`, `grid_mm`, `simplified_ensemble`, `trend_follower`

These strategies have zero HL-specific imports. They only depend on HL through the adapter layer (`DirectHLProxy` / `MarketSnapshot`). Porting to another exchange requires only writing a new adapter that produces `MarketSnapshot` and accepts orders.

**Moderately coupled (funding rate consumers, 2 strategies):**
`basis_arb`, `funding_momentum`

These read `snapshot.funding_rate` in their trading logic but don't import any HL-specific classes. They work on any exchange that provides funding rates, as long as the rate is normalized to the same units.

**Tightly coupled (quoting engine + HyperliquidFundingRate, 4 strategies):**
`engine_mm`, `funding_arb`, `regime_mm`, `liquidation_mm`

These directly import `HyperliquidFundingRate` from the quoting engine and require `_engine_base.py` (path hack to `~/Tee-work-/quoting_engine`). Porting requires refactoring the quoting engine's funding rate interface.

### Highest-Priority Abstractions for Multi-Exchange Support

1. **Exchange adapter interface** -- already ~80% done via `DirectHLProxy` / `DirectMockProxy` pattern. Define a `BaseExchangeProxy` ABC.
2. **Tick size / size precision providers** -- extract from `_get_price_tick` and `_get_sz_decimals` into exchange-specific implementations.
3. **Funding rate normalization** -- standardize units across exchanges in `MarketSnapshot`.
4. **Quoting engine funding source abstraction** -- replace `HyperliquidFundingRate` hard imports with a generic `FundingRateSource` interface.
