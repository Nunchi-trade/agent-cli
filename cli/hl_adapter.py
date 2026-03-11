"""HLProxy adapter for direct trading — wraps HLProxy without modifying core.

Adds place_order(), cancel_order(), get_open_orders() on top of the existing
HLProxy / MockHLProxy from parent/hl_proxy.py.

Also handles YEX (Nunchi HIP-3) market symbol mapping.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Dict, List, Optional

from parent.hl_proxy import HLFill, HLProxy, MockHLProxy

from cli.strategy_registry import YEX_MARKETS

log = logging.getLogger("hl_adapter")


def _default_builder() -> Optional[dict]:
    """Return the default Nunchi builder fee. Always active unless overridden."""
    from cli.builder_fee import BuilderFeeConfig
    return BuilderFeeConfig().to_builder_info()
ZERO = Decimal("0")


def _to_hl_coin(instrument: str) -> str:
    """Map instrument name to HL coin for API calls.

    Standard perps:  ETH-PERP -> ETH
    YEX markets:     VXX-USDYP -> yex:VXX
                     US3M-USDYP -> yex:US3M
    """
    yex = YEX_MARKETS.get(instrument)
    if yex:
        return yex["hl_coin"]
    return instrument.replace("-PERP", "").replace("-perp", "")


class DirectHLProxy:
    """Adapter around HLProxy that adds direct order placement for the CLI.

    Does NOT modify the core HLProxy class.
    """

    def __init__(self, hl: HLProxy):
        self._hl = hl
        self._hl._ensure_client()

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
        """Delegate to underlying proxy, handling YEX coin mapping."""
        # For YEX markets we need to call l2_snapshot with the yex: prefix
        yex = YEX_MARKETS.get(instrument)
        if yex:
            return self._get_yex_snapshot(instrument, yex["hl_coin"])
        return self._hl.get_snapshot(instrument)

    def _get_yex_snapshot(self, instrument: str, hl_coin: str):
        """Fetch snapshot for a YEX market using its yex: prefixed coin."""
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
            log.error("Failed to get YEX snapshot for %s (%s): %s", instrument, hl_coin, e)
            return MarketSnapshot(instrument=instrument)

    def get_account_state(self) -> Dict:
        """Fetch account state directly from HL Info API.

        Handles SDK IndexError when spot metadata is present in the response.
        """
        try:
            state = self._info.user_state(self._address)
            margin_summary = state.get("marginSummary", {})
            return {
                "account_value": float(margin_summary.get("accountValue", 0)),
                "total_margin": float(margin_summary.get("totalMarginUsed", 0)),
                "withdrawable": float(state.get("withdrawable", 0)),
                "address": self._address,
                "positions": state.get("assetPositions", []),
            }
        except IndexError:
            # SDK bug: spot metadata parsing can trigger IndexError.
            # Fall back to clearinghouse state endpoint which skips spot parsing.
            log.warning("SDK IndexError in user_state (spot metadata); trying clearinghouse fallback")
            try:
                import requests
                base_url = self._hl._info.base_url
                resp = requests.post(
                    f"{base_url}/info",
                    json={"type": "clearinghouseState", "user": self._address},
                    timeout=10,
                )
                data = resp.json()
                margin_summary = data.get("marginSummary", {})
                return {
                    "account_value": float(margin_summary.get("accountValue", 0)),
                    "total_margin": float(margin_summary.get("totalMarginUsed", 0)),
                    "withdrawable": float(data.get("withdrawable", 0)),
                    "address": self._address,
                    "positions": data.get("assetPositions", []),
                }
            except Exception as e2:
                log.error("Clearinghouse fallback also failed: %s", e2)
                return {}
        except Exception as e:
            log.error("Failed to get account state: %s", e)
            return {}

    def _get_tick_size(self, coin: str) -> float:
        """Get the tick size for an asset from HL metadata."""
        try:
            meta = self._info.meta()
            for asset in meta.get("universe", []):
                if asset.get("name") == coin:
                    return float(asset.get("szDecimals", 1))
            return 0.1  # default
        except Exception:
            return 0.1

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
            except Exception:
                pass
        return self._sz_decimals_cache.get(coin, 1)

    @staticmethod
    def _round_price(price: float, tick: float = 0.1) -> float:
        """Round price to HL tick size."""
        return round(round(price / tick) * tick, 8)

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[dict] = None,
    ) -> Optional[HLFill]:
        """Place a single order directly on HL. Returns HLFill if filled.

        For ALO (tif="Alo"): if the order would cross the book (rejected),
        automatically falls back to Gtc with a warning log.
        """
        # Enforce builder fee — always attach Nunchi fee if not explicitly provided
        if builder is None:
            builder = _default_builder()

        coin = _to_hl_coin(instrument)
        is_buy = side.lower() == "buy"

        # Round size to instrument's szDecimals (e.g. BTC=3, DOGE=0, ETH=4)
        sz_dec = self._get_sz_decimals(coin)
        size = round(size, sz_dec)

        # Round price to HL tick size (0.1 for most assets)
        price = self._round_price(price)

        # For IOC orders, apply slippage to cross the spread and guarantee fill.
        # Strategy prices are often at fair value (inside the spread) which won't
        # match any resting orders. Push buys above ask, sells below bid.
        if tif == "Ioc":
            try:
                snap = self._hl.get_snapshot(instrument)
                if is_buy and snap.ask > 0:
                    price = max(price, self._round_price(snap.ask * 1.005))
                elif not is_buy and snap.bid > 0:
                    price = min(price, self._round_price(snap.bid * 0.995))
            except Exception:
                pass  # use original price if snapshot fails

        fill = self._send_order(coin, instrument, side, is_buy, size, price, tif, builder)

        # ALO fallback: if ALO was rejected (would cross), retry with Gtc
        if fill is None and tif == "Alo":
            log.warning("ALO rejected for %s %s %s @ %s — falling back to Gtc",
                        side, size, instrument, price)
            fill = self._send_order(coin, instrument, side, is_buy, size, price, "Gtc", builder)

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
    ) -> Optional[HLFill]:
        """Low-level order send with retry on rate-limit. Returns HLFill or None."""
        try:
            result = None
            for attempt in range(3):
                try:
                    result = self._exchange.order(
                        coin, is_buy, size, price,
                        {"limit": {"tif": tif}},
                        builder=builder,
                    )
                    break
                except Exception as rate_err:
                    if "429" in str(rate_err) and attempt < 2:
                        delay = (attempt + 1) * 2  # 2s, 4s
                        log.warning("Rate limited (429), retrying in %ds...", delay)
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
                log.info("Resting [%s]: %s %s %s @ %s (oid=%s)", tif, side, size, instrument, price, oid)
                return None
            elif "error" in status:
                log.info("No fill [%s]: %s %s %s @ %s -- %s", tif, side, size, instrument, price, status["error"])
                return None
            else:
                log.warning("Unknown status: %s", status)
                return None

        except Exception as e:
            log.error("Order failed: %s %s %s @ %s [%s] -- %s", side, size, instrument, price, tif, e)
            return None

    def cancel_order(self, instrument: str, oid: str) -> bool:
        """Cancel an open order by OID."""
        coin = _to_hl_coin(instrument)
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
                coin = _to_hl_coin(instrument)
                orders = [o for o in orders if o.get("coin") == coin]
            return orders
        except Exception as e:
            log.error("Failed to get open orders: %s", e)
            return []

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> list:
        """Fetch candle data from HL."""
        return self._hl.get_candles(coin, interval, lookback_ms)

    def get_all_markets(self) -> list:
        """Fetch metadata + asset contexts for all perps."""
        return self._hl.get_meta_and_asset_ctxs()

    def get_all_mids(self) -> Dict[str, str]:
        """Fetch mid prices for all assets."""
        return self._hl.get_all_mids()

    def _to_coin(self, instrument: str) -> str:
        """Map instrument to HL coin symbol."""
        return _to_hl_coin(instrument)

    def _round_size(self, coin: str, size: float) -> float:
        """Round size to instrument's szDecimals."""
        sz_dec = self._get_sz_decimals(coin)
        return round(size, sz_dec)

    def place_trigger_order(self, instrument: str, side: str, size: float, trigger_price: float) -> Optional[str]:
        """Place a trigger stop-loss order on the exchange. Returns order ID or None."""
        coin = self._to_coin(instrument)
        is_buy = side.lower() == "buy"
        sz = self._round_size(coin, size)
        try:
            result = self._exchange.order(
                coin, is_buy, sz, trigger_price,
                order_type={"trigger": {"triggerPx": str(trigger_price), "isMarket": True, "tpsl": "sl"}},
                reduce_only=True,
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
    ) -> Optional[HLFill]:
        self._last_tif = tif  # expose for testing
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
