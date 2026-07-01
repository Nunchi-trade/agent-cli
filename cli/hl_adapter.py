"""HLProxy adapter for direct trading — wraps HLProxy without modifying core.

Adds place_order(), cancel_order(), get_open_orders() on top of the existing
HLProxy / MockHLProxy from parent/hl_proxy.py.

Also handles YEX (Nunchi HIP-3) market symbol mapping.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.models import active_hip3_dex_ids, instrument_to_coin
from parent.hl_proxy import HLFill, HLProxy, MockHLProxy

log = logging.getLogger("hl_adapter")

# --- Constants ---
SLIPPAGE_FACTOR = 1.002       # IOC slippage multiplier to cross the spread (v3: 50bps→20bps; tighter to reduce entry cost on thin yex markets)
SIG_FIGS = 5                  # HL uses 5 significant figures for prices
CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive API failures before circuit opens
MAX_RATE_LIMIT_RETRIES = 3
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 8.0

# Shared file-system cache for HIP-3 dex data. Without this, every agent in
# the fleet hits HL's /info endpoint independently every tick to fetch yex
# markets/mids, and CloudFront 429s the bursts. With 14 agents on a 60s tick
# that's 28+ yex requests/minute from one IP — well over the limit. The
# cache lives in /tmp (or DEX_CACHE_DIR) and is keyed by (kind, dex). TTL
# is intentionally short so live data stays fresh; the win is that within
# any TTL window, only one agent actually hits HL.
SHARED_CACHE_DIR = os.environ.get("DEX_CACHE_DIR", os.path.join(tempfile.gettempdir(), "nunchi_dex_cache"))
DEX_CACHE_DIR = SHARED_CACHE_DIR  # backwards compat
DEX_CACHE_TTL_S = float(os.environ.get("DEX_CACHE_TTL_S", "30"))
# Candle data changes slowly — 60s TTL is safe and cuts API calls by 95%+
CANDLE_CACHE_TTL_S = float(os.environ.get("CANDLE_CACHE_TTL_S", "60"))
# Market meta/mids refresh every 30s (same as dex cache)
MARKET_CACHE_TTL_S = float(os.environ.get("MARKET_CACHE_TTL_S", "30"))


def _cache_path(key: str) -> Path:
    Path(SHARED_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    return Path(SHARED_CACHE_DIR) / f"{key}.json"


def _cache_read(key: str, ttl_s: float) -> Optional[Any]:
    """Return cached payload if fresh, else None. Best-effort, never raises."""
    p = _cache_path(key)
    try:
        st = p.stat()
    except FileNotFoundError:
        return None
    age = time.time() - st.st_mtime
    if age > ttl_s:
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _cache_write(key: str, payload: Any) -> None:
    """Atomic write so concurrent readers never see a partial file."""
    p = _cache_path(key)
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(p)
    except Exception as e:
        log.debug("cache write failed for %s: %s", key, e)


def _cache_read_stale(key: str) -> Optional[Any]:
    """Read cache ignoring TTL — used as fallback on API failure."""
    p = _cache_path(key)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


# Backwards-compat wrappers for existing dex cache callers
def _dex_cache_path(kind: str, dex: str) -> Path:
    return _cache_path(f"{kind}_{dex}")

def _dex_cache_read(kind: str, dex: str) -> Optional[Any]:
    return _cache_read(f"{kind}_{dex}", DEX_CACHE_TTL_S)

def _dex_cache_write(kind: str, dex: str, payload: Any) -> None:
    _cache_write(f"{kind}_{dex}", payload)


class APICircuitBreakerOpen(Exception):
    """Raised when the API circuit breaker is open due to persistent failures."""
    pass


def _default_builder() -> Optional[dict]:
    """Return the default Nunchi builder fee. Always active unless overridden."""
    from cli.builder_fee import BuilderFeeConfig
    return BuilderFeeConfig().to_builder_info()
ZERO = Decimal("0")


def _mainnet_from_info(info) -> bool:
    return "testnet" not in getattr(info, "base_url", "").lower()


def _assemble_account_state(info, address: str, *, mainnet: Optional[bool] = None) -> Dict:
    """Build the unified account-state dict from an HL Info client + address.

    Pure read path: only calls public Info endpoints (user_state /
    clearinghouseState / spotClearinghouseState). No key or signing involved,
    so this is reused by both the authenticated proxy (DirectHLProxy) and the
    keyless read-only path (read_only_account_state).

    Merges perps, HIP-3 DEX clearinghouses (e.g. YEX) and spot balances into a
    single dict with the same shape get_account_state has always returned.
    """
    try:
        state = info.user_state(address)
        margin_summary = state.get("marginSummary", {})
        result = {
            "account_value": float(margin_summary.get("accountValue", 0)),
            "total_margin": float(margin_summary.get("totalMarginUsed", 0)),
            "withdrawable": float(state.get("withdrawable", 0)),
            "address": address,
            "positions": state.get("assetPositions", []),
            "spot_balances": [],
        }
    except IndexError:
        # SDK bug: spot metadata parsing can trigger IndexError.
        log.warning("SDK IndexError in user_state (spot metadata); trying clearinghouse fallback")
        try:
            result = _fetch_perps_via_http(info, address)
        except Exception as e2:
            log.error("Clearinghouse fallback also failed: %s", e2)
            return {}
    except Exception as e:
        log.error("Failed to get account state: %s", e)
        return {}

    # Merge HIP-3 DEX state (asset positions + account value/margin/withdrawable).
    net_mainnet = mainnet if mainnet is not None else _mainnet_from_info(info)
    for dex_id in active_hip3_dex_ids(mainnet=net_mainnet):
        try:
            dex_state = info.post("/info", {
                "type": "clearinghouseState", "user": address, "dex": dex_id,
            })
            if not dex_state:
                continue
            if dex_state.get("assetPositions"):
                result["positions"].extend(dex_state["assetPositions"])
            dex_margin = dex_state.get("marginSummary", {}) or {}
            try:
                result["account_value"] += float(dex_margin.get("accountValue", 0) or 0)
                result["total_margin"] += float(dex_margin.get("totalMarginUsed", 0) or 0)
                result["withdrawable"] += float(dex_state.get("withdrawable", 0) or 0)
            except (TypeError, ValueError):
                pass
        except Exception as e:
            log.warning("Failed to fetch %s state: %s", dex_id, e)

    # Fetch spot balances (separate endpoint).
    spot_balances = _fetch_spot_balances(info, address)
    if spot_balances:
        result["spot_balances"] = spot_balances
        spot_total = sum(
            float(b.get("total", 0)) for b in spot_balances
            if b.get("coin") == "USDC"
        )
        result["spot_usdc"] = spot_total
    return result


def _fetch_perps_via_http(info, address: str) -> Dict:
    """Fallback: fetch perps state via direct HTTP POST (no key)."""
    import requests
    base_url = info.base_url
    resp = requests.post(
        f"{base_url}/info",
        json={"type": "clearinghouseState", "user": address},
        timeout=10,
    )
    data = resp.json()
    margin_summary = data.get("marginSummary", {})
    return {
        "account_value": float(margin_summary.get("accountValue", 0)),
        "total_margin": float(margin_summary.get("totalMarginUsed", 0)),
        "withdrawable": float(data.get("withdrawable", 0)),
        "address": address,
        "positions": data.get("assetPositions", []),
        "spot_balances": [],
    }


def _fetch_spot_balances(info, address: str) -> List[Dict]:
    """Fetch spot/unified balances from HL spotClearinghouseState (no key)."""
    try:
        import requests
        base_url = info.base_url
        resp = requests.post(
            f"{base_url}/info",
            json={"type": "spotClearinghouseState", "user": address},
            timeout=10,
        )
        data = resp.json()
        balances = data.get("balances", [])
        return [
            {
                "coin": b.get("coin", ""),
                "total": b.get("total", "0"),
                "hold": b.get("hold", "0"),
            }
            for b in balances
            if float(b.get("total", 0)) != 0
        ]
    except Exception as e:
        log.warning("Failed to fetch spot balances: %s", e)
        return []


def read_only_account_state(address: str, testnet: bool = True) -> Dict:
    """Fetch account state for ANY address using only public Info endpoints.

    No private key is loaded and nothing is signed — this is the read-only
    ``--address`` / ``HL_VIEW_AS_USER`` path. Returns the same dict shape as
    DirectHLProxy.get_account_state(), or {} on failure.
    """
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    from parent.hl_proxy import _retry_on_429
    from parent.sdk_patches import patch_spot_meta_indexing

    patch_spot_meta_indexing()
    base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
    # Construct with the default perp universe only (perp_dexs=[""]). We do NOT
    # pass HIP3_DEXS here: the Info constructor validates each requested dex
    # against the network's live perp_dexs() and KeyErrors on dexes that aren't
    # registered (e.g. a testnet-only HIP-3 dex when reading a mainnet address).
    # HIP-3 clearinghouse state is merged separately in _assemble_account_state
    # via explicit POSTs, which already tolerate absent dexes.
    info = _retry_on_429(
        Info, base_url, skip_ws=True, timeout=10,
    )
    return _assemble_account_state(info, address, mainnet=not testnet)


def _to_hl_coin(instrument: str, mainnet: Optional[bool] = None) -> str:
    """Map instrument name to HL coin for API calls."""
    return instrument_to_coin(instrument, mainnet=mainnet)


def _funding_rates_from_markets(data: Any, coin: Optional[str] = None) -> Dict[str, float]:
    """Extract {coin: hourly_funding_rate} from a meta_and_asset_ctxs payload.

    `data` is the [meta, asset_ctxs] pair returned by get_all_markets(); the
    nth asset context lines up with the nth universe entry. Best-effort: any
    malformed entry is treated as 0.0 funding rather than raising.
    """
    rates: Dict[str, float] = {}
    try:
        universe = (data[0] or {}).get("universe", []) if data else []
        ctxs = data[1] if data and len(data) > 1 else []
        for asset, ctx in zip(universe, ctxs):
            name = asset.get("name", "")
            if not name:
                continue
            try:
                rates[name] = float(ctx.get("funding", 0) or 0)
            except (TypeError, ValueError):
                rates[name] = 0.0
    except Exception as e:
        log.debug("funding parse failed: %s", e)
    if coin:
        c = _to_hl_coin(coin)
        return {c: rates.get(c, 0.0)}
    return rates


class DirectHLProxy:
    """Adapter around HLProxy that adds direct order placement for the CLI.

    Does NOT modify the core HLProxy class.
    """

    def __init__(self, hl: HLProxy):
        self._hl = hl
        self._hl._ensure_client()
        self._api_failure_count = 0
        self._api_consecutive_429s = 0

    def set_leverage(self, leverage: int, coin: str = "ETH", is_cross: bool = True):
        """Set leverage for a coin via the underlying proxy."""
        self._hl.set_leverage(leverage, coin, is_cross)

    @property
    def _info(self):
        return self._hl._info

    @property
    def _exchange(self):
        return self._hl._exchange

    @property
    def _address(self):
        return self._hl._address

    def get_snapshot(self, instrument: str = "ETH-PERP"):
        """Delegate to underlying proxy, handling YEX coin mapping.

        Tracks consecutive API failures and raises APICircuitBreakerOpen
        after CIRCUIT_BREAKER_THRESHOLD consecutive failures to force
        the engine into safe mode rather than trading blind.
        """
        if self._api_failure_count >= CIRCUIT_BREAKER_THRESHOLD:
            raise APICircuitBreakerOpen(
                f"API circuit breaker open: {self._api_failure_count} consecutive failures"
            )

        try:
            hl_coin = instrument_to_coin(instrument, mainnet=not self._hl.testnet)
            if ":" in hl_coin:
                snap = self._get_hip3_snapshot(instrument, hl_coin)
            else:
                snap = self._hl.get_snapshot(instrument)

            # Reset failure counter on success (non-zero price = real data)
            if snap.mid_price > 0:
                self._api_failure_count = 0
            return snap
        except APICircuitBreakerOpen:
            raise
        except Exception as e:
            self._api_failure_count += 1
            log.warning("API failure %d/%d for %s: %s",
                        self._api_failure_count, CIRCUIT_BREAKER_THRESHOLD,
                        instrument, e)
            if self._api_failure_count >= CIRCUIT_BREAKER_THRESHOLD:
                raise APICircuitBreakerOpen(
                    f"API circuit breaker open after {self._api_failure_count} consecutive failures"
                ) from e
            from common.models import MarketSnapshot
            return MarketSnapshot(instrument=instrument)

    def _get_hip3_snapshot(self, instrument: str, hl_coin: str):
        """Fetch snapshot for a HIP-3 DEX market via L2 book."""
        from common.models import MarketSnapshot
        try:
            book = self._info.l2_snapshot(hl_coin)
            bids = book.get("levels", [[]])[0] if book.get("levels") else []
            asks = book.get("levels", [[], []])[1] if len(book.get("levels", [])) > 1 else []

            best_bid = float(bids[0]["px"]) if bids else 0.0
            best_ask = float(asks[0]["px"]) if asks else 0.0
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
            spread = ((best_ask - best_bid) / mid * 10000) if mid > 0 else 0.0

            return MarketSnapshot(
                instrument=instrument,
                mid_price=round(mid, 4),
                bid=round(best_bid, 4),
                ask=round(best_ask, 4),
                spread_bps=round(spread, 2),
                timestamp_ms=int(time.time() * 1000),
            )
        except Exception as e:
            log.error("Failed to get HIP-3 snapshot for %s (%s): %s", instrument, hl_coin, e)
            return MarketSnapshot(instrument=instrument)

    def get_account_state(self) -> Dict:
        """Fetch account state directly from HL Info API.

        Fetches perps (clearinghouseState), HIP-3 DEX clearinghouses (e.g.
        YEX) and spot (spotClearinghouseState) balances so `hl account` shows
        the full unified balance. Delegates to the shared, key-free assembler
        so the read-only `--address` path produces identical output.

        The account-value merge matters: agents in PR 3 mode (dedicated agent
        wallets, funded by treasury into yex) would otherwise report
        "** NO FUNDS DETECTED **" at preflight even though they hold $1000
        USDYP in yex, because the universal clearinghouse query returns $0.
        """
        return _assemble_account_state(
            self._info, self._address, mainnet=not self._hl.testnet,
        )

    def _get_price_tick(self, coin: str, price: float) -> float:
        """Get the price tick size for an asset.

        Hyperliquid uses 5 significant figures for prices. The tick size
        depends on the price magnitude:
          BTC @ $60000 → tick = 1.0     (6e4, 5 sig figs → 1)
          ETH @ $3000  → tick = 0.1     (3e3, 5 sig figs → 0.1)
          SOL @ $150   → tick = 0.01    (1.5e2, 5 sig figs → 0.01)
          kPEPE @ 0.003 → tick = 0.0000001
        """
        if not hasattr(self, "_price_tick_cache"):
            self._price_tick_cache: Dict[str, float] = {}

        # Use cached value if price hasn't changed order of magnitude
        if coin in self._price_tick_cache:
            cached = self._price_tick_cache[coin]
            if cached > 0 and 0.1 <= price / (cached * 1e4) <= 10:
                return cached

        # Compute tick from significant figures (HL uses 5 sig figs for prices)
        sig_figs = SIG_FIGS
        if price <= 0:
            return 0.1
        import math
        magnitude = math.floor(math.log10(abs(price)))
        tick = 10.0 ** (magnitude - sig_figs + 1)
        self._price_tick_cache[coin] = tick
        return tick

    def _get_sz_decimals(self, coin: str) -> int:
        """Get szDecimals for an asset — number of decimal places for order sizes."""
        if not hasattr(self, "_sz_decimals_cache"):
            self._sz_decimals_cache: Dict[str, int] = {}
            try:
                meta = self._info.meta()
                for asset in meta.get("universe", []):
                    name = asset.get("name", "")
                    if name:
                        self._sz_decimals_cache[name] = int(asset.get("szDecimals", 1))
                # Include HIP-3 DEX assets
                for dex_id in active_hip3_dex_ids(mainnet=not self._hl.testnet):
                    try:
                        dex_meta = self._info.meta(dex=dex_id)
                        for asset in dex_meta.get("universe", []):
                            name = asset.get("name", "")
                            if name:
                                self._sz_decimals_cache[name] = int(asset.get("szDecimals", 1))
                    except Exception:
                        pass
            except Exception:
                pass
        return self._sz_decimals_cache.get(coin, 1)

    def _round_price(self, price: float, coin: str = "") -> float:
        """Round price to HL tick size (5 sig figs, price-dependent)."""
        tick = self._get_price_tick(coin, price) if coin and price > 0 else 0.1
        return round(round(price / tick) * tick, 8)

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[dict] = None,
        reduce_only: bool = False,
    ) -> Optional[HLFill]:
        """Place a single order directly on HL. Returns HLFill if filled.

        For ALO (tif="Alo"): if the order would cross the book (rejected),
        automatically falls back to Gtc with a warning log.

        When reduce_only is True the order can only shrink an existing
        position (used to safely close/reduce, never to open or flip).
        """
        # REVENUE-CRITICAL: Enforce builder fee on every order.
        # Default: 10 bps to Nunchi wallet (0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0).
        # This is the sole enforcement point — all order paths flow through here.
        if builder is None:
            builder = _default_builder()
        coin = self._to_coin(instrument)
        is_buy = side.lower() == "buy"

        # Round size to instrument's szDecimals (e.g. BTC=3, DOGE=0, ETH=4)
        sz_dec = self._get_sz_decimals(coin)
        size = round(size, sz_dec)

        # Round price to HL tick size (price-dependent, 5 sig figs)
        price = self._round_price(price, coin)

        # For IOC orders, apply slippage to cross the spread and guarantee fill.
        # Strategy prices are often at fair value (inside the spread) which won't
        # match any resting orders. Push buys above ask, sells below bid.
        if tif == "Ioc":
            try:
                snap = self._hl.get_snapshot(instrument)
                if is_buy and snap.ask > 0:
                    price = max(price, self._round_price(snap.ask * SLIPPAGE_FACTOR, coin))
                elif not is_buy and snap.bid > 0:
                    price = min(price, self._round_price(snap.bid * (2 - SLIPPAGE_FACTOR), coin))
            except Exception:
                pass  # use original price if snapshot fails

        fill = self._send_order(coin, instrument, side, is_buy, size, price, tif, builder, reduce_only)

        # ALO fallback: if ALO was rejected (would cross), retry with Gtc
        if fill is None and tif == "Alo":
            log.warning("ALO rejected for %s %s %s @ %s — falling back to Gtc",
                        side, size, instrument, price)
            fill = self._send_order(coin, instrument, side, is_buy, size, price, "Gtc", builder, reduce_only)

        return fill

    def _send_order(
        self,
        coin: str,
        instrument: str,
        side: str,
        is_buy: bool,
        size: float,
        price: float,
        tif: str,
        builder: Optional[dict],
        reduce_only: bool = False,
    ) -> Optional[HLFill]:
        """Low-level order send with retry on rate-limit. Returns HLFill or None."""
        try:
            import random as _rand
            result = None
            for attempt in range(MAX_RATE_LIMIT_RETRIES):
                try:
                    order_kwargs = {"builder": builder}
                    # Only pass reduce_only when set so the common path keeps the
                    # exact call signature existing callers/tests rely on.
                    if reduce_only:
                        order_kwargs["reduce_only"] = True
                    result = self._exchange.order(
                        coin, is_buy, size, price,
                        {"limit": {"tif": tif}},
                        **order_kwargs,
                    )
                    self._api_consecutive_429s = 0  # reset on success
                    break
                except Exception as rate_err:
                    if "429" in str(rate_err) and attempt < MAX_RATE_LIMIT_RETRIES - 1:
                        # Exponential backoff with jitter, capped at BACKOFF_MAX_S
                        base_delay = min(BACKOFF_BASE_S * (2 ** attempt), BACKOFF_MAX_S)
                        jitter = _rand.uniform(0, base_delay * 0.25)
                        delay = base_delay + jitter
                        self._api_consecutive_429s += 1
                        log.warning("Rate limited (429), attempt %d/%d, retrying in %.1fs...",
                                    attempt + 1, MAX_RATE_LIMIT_RETRIES, delay)
                        time.sleep(delay)
                    else:
                        raise

            if result is None:
                return None

            if result.get("status") == "err":
                log.warning("Order rejected: %s %s %s @ %s [%s] -- %s",
                            side, size, instrument, price, tif, result.get("response"))
                return None

            resp = result.get("response", {})
            if not isinstance(resp, dict):
                log.warning("Unexpected response: %s", resp)
                return None

            statuses = resp.get("data", {}).get("statuses", [])
            status = statuses[0] if statuses else {}

            if isinstance(status, str):
                log.warning("Order status string: %s", status)
                return None
            elif "filled" in status:
                info = status["filled"]
                fill = HLFill(
                    oid=info.get("oid", ""),
                    instrument=instrument,
                    side=side.lower(),
                    price=Decimal(str(info.get("avgPx", price))),
                    quantity=Decimal(str(info.get("totalSz", size))),
                    timestamp_ms=int(time.time() * 1000),
                )
                log.info("Filled [%s]: %s %s %s @ %s", tif, side, info.get("totalSz", size),
                         instrument, info.get("avgPx", price))
                return fill
            elif "resting" in status:
                oid = status["resting"].get("oid", "") if isinstance(status["resting"], dict) else ""
                log.info("Resting [%s]: %s %s %s @ %s (oid=%s) — cancelling",
                         tif, side, size, instrument, price, oid)
                if oid:
                    try:
                        self._exchange.cancel(coin, int(oid))
                    except Exception:
                        log.warning("Failed to cancel resting order %s for %s", oid, instrument)
                return None
            elif "error" in status:
                log.info("No fill [%s]: %s %s %s @ %s -- %s", tif, side, size, instrument, price, status["error"])
                return None
            else:
                log.warning("Unknown status: %s", status)
                return None

        except (ConnectionError, OSError) as e:
            log.error("Order network error: %s %s %s @ %s [%s] -- %s",
                       side, size, instrument, price, tif, e)
            return None
        except json.JSONDecodeError as e:
            log.error("Order response parse error: %s %s %s @ %s [%s] -- %s",
                       side, size, instrument, price, tif, e)
            return None
        except Exception as e:
            log.critical("Order unexpected failure: %s %s %s @ %s [%s] -- %s",
                          side, size, instrument, price, tif, e, exc_info=True)
            return None

    def cancel_order(self, instrument: str, oid: str) -> bool:
        """Cancel an open order by OID."""
        coin = self._to_coin(instrument)
        try:
            self._exchange.cancel(coin, oid)
            return True
        except Exception as e:
            log.error("Cancel failed for %s (oid=%s): %s", instrument, oid, e)
            return False

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        """Get all open orders, optionally filtered by instrument."""
        try:
            orders = self._info.open_orders(self._address)
            if instrument:
                coin = self._to_coin(instrument)
                orders = [o for o in orders if o.get("coin") == coin]
            return orders
        except Exception as e:
            log.error("Failed to get open orders: %s", e)
            return []

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> list:
        """Fetch candle data from HL (shared file cache, 60s TTL).

        28 agents × 3 timeframes × N markets = hundreds of candle calls per
        scan interval. The cache means only the first agent to request a
        (coin, interval) pair within the TTL window hits HL; the rest read
        from disk. This alone cuts candle API calls by ~95%.
        """
        key = f"candles_{coin}_{interval}"
        cached = _cache_read(key, CANDLE_CACHE_TTL_S)
        if cached is not None:
            return cached
        data = self._hl.get_candles(coin, interval, lookback_ms)
        _cache_write(key, data)
        return data

    def get_all_markets(self) -> list:
        """Fetch metadata + asset contexts for all perps (shared cache, 30s TTL)."""
        cached = _cache_read("all_markets", MARKET_CACHE_TTL_S)
        if cached is not None:
            return cached
        data = self._hl.get_meta_and_asset_ctxs()
        _cache_write("all_markets", data)
        return data

    def get_all_mids(self) -> Dict[str, str]:
        """Fetch mid prices for all assets (shared cache, 30s TTL)."""
        cached = _cache_read("all_mids", MARKET_CACHE_TTL_S)
        if cached is not None:
            return cached
        data = self._hl.get_all_mids()
        _cache_write("all_mids", data)
        return data

    def get_dex_markets(self, dex: str) -> list:
        """Fetch HIP-3 DEX metaAndAssetCtxs (file-cached, see DEX_CACHE_TTL_S).

        Without the cache, 14+ fleet agents independently hit HL's /info
        endpoint every tick and CloudFront 429s the bursts, which silently
        breaks pulse/radar's view of the yex universe (see _merge_hip3_markets
        in standalone_runner.py). The cache makes the second-through-Nth
        caller within the TTL window read from disk instead of HL.
        """
        cached = _dex_cache_read("markets", dex)
        if cached is not None:
            return cached
        try:
            data = self._hl.get_dex_markets(dex)
            _dex_cache_write("markets", dex, data)
            return data
        except Exception as e:
            # On failure, serve stale cache if any (better than empty universe).
            stale_path = _dex_cache_path("markets", dex)
            if stale_path.exists():
                try:
                    log.warning("get_dex_markets(%s) failed (%s) — serving stale cache", dex, e)
                    return json.loads(stale_path.read_text())
                except Exception:
                    pass
            raise

    def get_dex_mids(self, dex: str) -> Dict[str, str]:
        """Fetch HIP-3 DEX mid prices (file-cached, see DEX_CACHE_TTL_S)."""
        cached = _dex_cache_read("mids", dex)
        if cached is not None:
            return cached
        try:
            data = self._hl.get_dex_mids(dex)
            _dex_cache_write("mids", dex, data)
            return data
        except Exception as e:
            stale_path = _dex_cache_path("mids", dex)
            if stale_path.exists():
                try:
                    log.warning("get_dex_mids(%s) failed (%s) — serving stale cache", dex, e)
                    return json.loads(stale_path.read_text())
                except Exception:
                    pass
            raise

    # ─── Margin actions (SDK pass-throughs for `hl margin`) ─────────────────

    def usd_class_transfer(self, amount: float, to_perp: bool) -> Dict:
        """Move USDC between spot and main perp accounts.

        Delegates to `hyperliquid.exchange.Exchange.usd_class_transfer`.
        User-signed EIP-712 (no msgpack). Returns the raw exchange response.
        """
        return self._exchange.usd_class_transfer(amount=amount, to_perp=to_perp)

    def send_asset(
        self,
        destination: str,
        source_dex: str,
        destination_dex: str,
        token: str,
        amount: float,
    ) -> Dict:
        """Cross-DEX asset transfer (main perp ↔ HIP-3 sub-DEX e.g. yex).

        Use "" for the main perp dex name and "spot" for spot. Token must
        match the collateral token. User-signed EIP-712 envelope per the
        SDK's `sign_send_asset_action`.
        """
        return self._exchange.send_asset(
            destination=destination,
            source_dex=source_dex,
            destination_dex=destination_dex,
            token=token,
            amount=amount,
        )

    def update_isolated_margin(self, amount_usd: float, coin: str) -> Dict:
        """Add (positive) or remove (negative) isolated margin on a position.

        `coin` is the HL coin name as the user sees it (e.g. "BTC" for main
        perps or "yex:BTCSWP" for the YEX sub-DEX). SDK resolves the asset
        index from `info.name_to_asset` so HIP-3 markets work transparently.
        """
        return self._exchange.update_isolated_margin(amount=amount_usd, name=coin)

    def list_hip3_dexes(self) -> list:
        """List HIP-3 sub-DEX names exposed by HL (e.g. ['yex'])."""
        try:
            dexes = self._info.perp_dexs()
        except Exception as e:
            log.warning("perp_dexs() failed: %s", e)
            return []
        out: list = []
        for entry in dexes or []:
            if isinstance(entry, dict) and entry.get("name"):
                out.append(entry["name"])
        return out

    def _to_coin(self, instrument: str) -> str:
        """Map instrument to HL coin symbol."""
        return _to_hl_coin(instrument, mainnet=not self._hl.testnet)

    def _round_size(self, coin: str, size: float) -> float:
        """Round size to instrument's szDecimals."""
        sz_dec = self._get_sz_decimals(coin)
        return round(size, sz_dec)

    def place_trigger_order(self, instrument: str, side: str, size: float, trigger_price: float, builder: Optional[dict] = None) -> Optional[str]:
        """Place a trigger stop-loss order on the exchange. Returns order ID or None.

        Attempts to attach builder fee; falls back to no-builder if HL rejects it
        (trigger orders may not support builder fees on all exchange versions).
        """
        if builder is None:
            builder = _default_builder()
        coin = self._to_coin(instrument)
        is_buy = side.lower() == "buy"
        sz = self._round_size(coin, size)
        try:
            result = self._exchange.order(
                coin, is_buy, sz, trigger_price,
                order_type={"trigger": {"triggerPx": trigger_price, "isMarket": True, "tpsl": "sl"}},
                reduce_only=True,
                builder=builder,
            )
            # Parse OID from response
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            if statuses and "resting" in statuses[0]:
                return str(statuses[0]["resting"]["oid"])
            if statuses and "filled" in statuses[0]:
                return str(statuses[0]["filled"]["oid"])
            log.warning("Trigger order placed but no OID in response: %s", result)
            return None
        except Exception as e:
            log.warning("Failed to place trigger SL for %s: %s", instrument, e)
            return None

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        """Cancel a trigger order. Returns True if successful."""
        coin = self._to_coin(instrument)
        try:
            self._exchange.cancel(coin, int(oid))
            return True
        except Exception as e:
            log.warning("Failed to cancel trigger order %s: %s", oid, e)
            return False

    def schedule_cancel(self, time_ms: Optional[int]) -> bool:
        """Arm or clear Hyperliquid's dead-man's switch (scheduleCancel).

        Pass an epoch-ms timestamp to tell HL to cancel ALL of this account's
        open orders at that time unless refreshed before then; pass None to
        clear it. Returns True if HL accepted the request. Intended to be
        refreshed each tick by long-running agents so a crashed process never
        leaves resting orders unattended.
        """
        try:
            result = self._exchange.schedule_cancel(time_ms)
            ok = isinstance(result, dict) and result.get("status") == "ok"
            if not ok:
                log.warning("schedule_cancel rejected: %s", result)
            return ok
        except Exception as e:
            log.error("schedule_cancel failed: %s", e)
            return False

    def get_order_status(self, oid) -> Optional[Dict]:
        """Look up a single order by oid via HL Info. Returns the status dict or None."""
        try:
            return self._info.query_order_by_oid(self._address, int(oid))
        except Exception as e:
            log.error("get_order_status failed for oid=%s: %s", oid, e)
            return None

    def get_funding_rates(self, coin: Optional[str] = None) -> Dict[str, float]:
        """Return current hourly funding rate per coin (or one coin if given).

        Sourced from the cached meta+asset-contexts payload so it adds no extra
        API load beyond get_all_markets()'s shared cache.
        """
        try:
            return _funding_rates_from_markets(self.get_all_markets(), coin)
        except Exception as e:
            log.error("get_funding_rates failed: %s", e)
            return {}

    def emergency_close_all(self) -> Dict:
        """Cancel ALL open orders and market-close ALL positions (reduce-only).

        Safety kill-switch. Every step is best-effort and isolated so one
        failure never aborts the rest. No builder fee is attached to the
        closes — reliability is worth more than a few bps on a panic exit.
        Returns a summary: cancelled order count, per-position close results,
        and any errors encountered.
        """
        summary: Dict[str, Any] = {"cancelled_orders": 0, "closed_positions": [], "errors": []}

        # 1. Cancel every open order across all instruments.
        try:
            for o in self.get_open_orders():
                coin = o.get("coin", "")
                oid = o.get("oid", "")
                if not coin or oid == "":
                    continue
                try:
                    self._exchange.cancel(coin, int(oid))
                    summary["cancelled_orders"] += 1
                except Exception as e:
                    summary["errors"].append(f"cancel {coin}#{oid}: {e}")
        except Exception as e:
            summary["errors"].append(f"list_orders: {e}")

        # 2. Market-close every open position.
        try:
            state = self.get_account_state()
            positions = state.get("positions", []) if state else []
        except Exception as e:
            summary["errors"].append(f"account_state: {e}")
            positions = []

        for p in positions:
            pos = p.get("position", {}) if isinstance(p, dict) else {}
            coin = pos.get("coin", "")
            try:
                szi = float(pos.get("szi", 0) or 0)
            except (TypeError, ValueError):
                szi = 0.0
            if not coin or szi == 0:
                continue
            try:
                result = self._exchange.market_close(coin)
                ok = isinstance(result, dict) and result.get("status") == "ok"
            except Exception as e:
                ok = False
                summary["errors"].append(f"close {coin}: {e}")
            summary["closed_positions"].append({"coin": coin, "size": szi, "ok": ok})

        return summary


class DirectMockProxy:
    """Mock adapter for dry-run / testing — no real HL connection."""

    def __init__(self, mock: Optional[MockHLProxy] = None):
        self._mock = mock or MockHLProxy()
        self._open_orders: List[Dict] = []
        self._trigger_orders: Dict[str, Dict] = {}
        self._next_trigger_oid: int = 9000

    def get_snapshot(self, instrument: str = "ETH-PERP"):
        return self._mock.get_snapshot(instrument)

    def get_account_state(self) -> Dict:
        return {
            "account_value": 100000.0,
            "total_margin": 0.0,
            "withdrawable": 100000.0,
            "address": "0xMOCK",
        }

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[dict] = None,
        reduce_only: bool = False,
    ) -> Optional[HLFill]:
        self._last_tif = tif  # expose for testing
        self._last_reduce_only = reduce_only  # expose for testing
        fill = HLFill(
            oid=f"mock-{int(time.time()*1000)}",
            instrument=instrument,
            side=side.lower(),
            price=Decimal(str(price)),
            quantity=Decimal(str(size)),
            timestamp_ms=int(time.time() * 1000),
        )
        log.info("[MOCK] Filled [%s]: %s %s %s @ %s", tif, side, size, instrument, price)
        return fill

    def cancel_order(self, instrument: str, oid: str) -> bool:
        return True

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        return []

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> list:
        """Generate mock candle data."""
        return self._mock.get_candles(coin, interval, lookback_ms)

    def get_all_markets(self) -> list:
        """Return mock meta + asset contexts."""
        return self._mock.get_meta_and_asset_ctxs()

    def get_all_mids(self) -> Dict[str, str]:
        """Return mock mid prices."""
        return self._mock.get_all_mids()

    def get_dex_markets(self, dex: str) -> list:
        """Return mock HIP-3 DEX markets."""
        return self._mock.get_dex_markets(dex)

    def get_dex_mids(self, dex: str) -> Dict[str, str]:
        """Return mock HIP-3 DEX mids."""
        return self._mock.get_dex_mids(dex)

    def usd_class_transfer(self, amount: float, to_perp: bool) -> Dict:
        """Mock USDC perp↔spot transfer."""
        return {"status": "ok", "response": {"type": "usdClassTransfer", "amount": amount, "toPerp": to_perp}}

    def send_asset(self, destination: str, source_dex: str, destination_dex: str, token: str, amount: float) -> Dict:
        """Mock cross-dex asset send."""
        return {
            "status": "ok",
            "response": {
                "type": "sendAsset",
                "destination": destination,
                "sourceDex": source_dex,
                "destinationDex": destination_dex,
                "token": token,
                "amount": amount,
            },
        }

    def update_isolated_margin(self, amount_usd: float, coin: str) -> Dict:
        """Mock isolated margin update."""
        return {"status": "ok", "response": {"type": "updateIsolatedMargin", "coin": coin, "amount": amount_usd}}

    def list_hip3_dexes(self) -> list:
        """Return the mock HIP-3 dex names — matches the strategy registry."""
        return ["yex"]

    def place_trigger_order(self, instrument: str, side: str, size: float, trigger_price: float) -> Optional[str]:
        """Place a mock trigger stop-loss order. Returns OID."""
        oid = str(self._next_trigger_oid)
        self._next_trigger_oid += 1
        self._trigger_orders[oid] = {
            "instrument": instrument, "side": side, "size": size,
            "trigger_price": trigger_price,
        }
        return oid

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        """Cancel a mock trigger order. Returns True if found and removed."""
        return self._trigger_orders.pop(oid, None) is not None

    def schedule_cancel(self, time_ms: Optional[int]) -> bool:
        """Record a mock dead-man's switch. Always succeeds."""
        self._scheduled_cancel_ms = time_ms
        return True

    def get_order_status(self, oid) -> Optional[Dict]:
        """Return a mock order-status payload."""
        return {"oid": str(oid), "status": "mock"}

    def get_funding_rates(self, coin: Optional[str] = None) -> Dict[str, float]:
        """Derive mock funding rates from the mock market contexts."""
        return _funding_rates_from_markets(self.get_all_markets(), coin)

    def emergency_close_all(self) -> Dict:
        """Mock kill-switch — no real positions, returns an empty summary."""
        return {"cancelled_orders": 0, "closed_positions": [], "errors": []}
