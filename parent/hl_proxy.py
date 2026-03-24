"""Hyperliquid API proxy — market data + order placement.

MockHLProxy for development; HLProxy for real HL testnet/mainnet.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from common.models import MarketSnapshot

log = logging.getLogger("hl_proxy")

ZERO = Decimal("0")

from parent.sdk_patches import patch_spot_meta_indexing as _patch_spot_meta_indexing


@dataclass
class HLFill:
    """A fill received from Hyperliquid."""
    oid: str
    instrument: str
    side: str
    price: Decimal
    quantity: Decimal
    timestamp_ms: int
    fee: Decimal = ZERO


class MockHLProxy:
    """Fake HL proxy for local development — simulates market data and fills."""

    def __init__(self, base_price: float = 2500.0, spread_bps: float = 2.0):
        self.base_price = base_price
        self.spread_bps = spread_bps
        self.placed_orders: List[Dict] = []
        self.fills: List[HLFill] = []
        self._tick = 0
        self._oi_history: Dict[str, float] = {}  # track OI across ticks
        self._vol_history: Dict[str, float] = {}  # track volume across ticks

    def get_snapshot(self, instrument: str = "ETH-PERP") -> MarketSnapshot:
        """Generate a mock market data snapshot."""
        import random
        drift = random.uniform(-5, 5)
        mid = self.base_price + drift
        half_spread = mid * (self.spread_bps / 10000 / 2)

        self._tick += 1
        return MarketSnapshot(
            instrument=instrument,
            mid_price=round(mid, 2),
            bid=round(mid - half_spread, 2),
            ask=round(mid + half_spread, 2),
            spread_bps=round(self.spread_bps, 2),
            timestamp_ms=int(time.time() * 1000),
            volume_24h=round(random.uniform(1e6, 5e6), 0),
            funding_rate=round(random.uniform(-0.001, 0.001), 6),
            open_interest=round(random.uniform(1e5, 5e5), 0),
        )

    def place_orders_from_clearing(self, fills: List[Dict]) -> List[Dict]:
        """Convert clearing fills into HL orders.

        In mock mode, just records them and generates fake fills.
        Returns list of placed order records.
        """
        placed = []
        for f in fills:
            qty = Decimal(str(f.get("quantity_filled", "0")))
            if qty <= ZERO:
                continue

            order = {
                "instrument": f["instrument"],
                "side": f["side"],
                "price": str(f["fill_price"]),
                "quantity": str(qty),
                "agent_id": f["agent_id"],
                "type": "limit",
                "time_in_force": "IOC",
                "timestamp_ms": int(time.time() * 1000),
            }
            placed.append(order)
            self.placed_orders.append(order)

            # In mock mode, all orders are immediately filled
            self.fills.append(HLFill(
                oid=f"mock-{len(self.fills)}",
                instrument=f["instrument"],
                side=f["side"],
                price=Decimal(str(f["fill_price"])),
                quantity=qty,
                timestamp_ms=int(time.time() * 1000),
            ))

        log.info("Placed %d orders (%d cumulative fills)", len(placed), len(self.fills))
        return placed

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        """Generate mock candle data with realistic patterns.

        BTC and ETH get an uptrend with volume surge in recent candles,
        making them detectable by Radar and Pulse.
        """
        import random
        now = int(time.time() * 1000)
        interval_ms = {"1h": 3_600_000, "4h": 14_400_000, "15m": 900_000}.get(interval, 3_600_000)
        n_candles = min(lookback_ms // interval_ms, 200)
        candles = []

        # Signal coins get a clear uptrend + volume spike
        is_signal_coin = coin in ("ETH", "SOL", "LINK")
        base = {"BTC": 50000, "ETH": 2500, "SOL": 100}.get(coin, self.base_price)
        price = base * 0.97  # start slightly below base

        for i in range(n_candles):
            t = now - (n_candles - i) * interval_ms
            o = price

            if is_signal_coin:
                # Steady uptrend: +0.3% per candle, accelerating in last 5
                pct = 0.003 if i < n_candles - 5 else 0.008
                c = o * (1 + pct + random.uniform(0, 0.002))
                h = max(o, c) * (1 + random.uniform(0, 0.005))
                l = min(o, c) * (1 - random.uniform(0, 0.002))
                # dayNtlVlm=1M → avg_4h=166K. Base ~100K, spike ~1M+ → ratio ~6x+
                v = random.uniform(80_000, 120_000) if i < n_candles - 5 else random.uniform(900_000, 1_500_000)
            else:
                # Other coins: normal random walk
                c = o * (1 + random.uniform(-0.01, 0.01))
                h = o * (1 + random.uniform(0, 0.02))
                l = o * (1 - random.uniform(0, 0.02))
                v = random.uniform(100_000, 500_000)

            candles.append({"t": t, "o": str(round(o, 2)), "h": str(round(h, 2)),
                            "l": str(round(l, 2)), "c": str(round(c, 2)), "v": str(round(v, 2))})
            price = c
        return candles

    def get_meta_and_asset_ctxs(self) -> Any:
        """Generate mock meta + asset contexts with persistent state.

        Signal coins (ETH, SOL, LINK) get OI growth and volume spikes
        across ticks so Pulse can detect them.
        """
        import random

        assets = ["BTC", "ETH", "SOL", "DOGE", "ARB", "OP", "AVAX", "MATIC",
                  "LINK", "UNI", "AAVE", "CRV", "MKR", "SNX", "COMP"]
        base_prices = {"BTC": 50000, "ETH": 2500, "SOL": 100, "DOGE": 0.15,
                       "ARB": 1.2, "OP": 2.5, "AVAX": 35, "MATIC": 0.8,
                       "LINK": 15, "UNI": 7, "AAVE": 100, "CRV": 0.5,
                       "MKR": 1500, "SNX": 3, "COMP": 50}
        signal_coins = {"ETH", "SOL", "LINK"}

        universe = []
        asset_ctxs = []
        for name in assets:
            universe.append({"name": name, "szDecimals": 3 if name == "BTC" else 1})

            bp = base_prices.get(name, 10.0)
            if name in signal_coins:
                prev_oi = self._oi_history.get(name, random.uniform(5e6, 5e7))
                prev_vol = self._vol_history.get(name, 1_000_000.0)
            else:
                prev_oi = self._oi_history.get(name, random.uniform(5e6, 5e7))
                prev_vol = self._vol_history.get(name, random.uniform(1e7, 1e8))

            if name in signal_coins:
                # Signal coins: grow OI 10-15% per tick
                # dayNtlVlm ~1M (passes 500K filter). avg_4h=166K.
                # Candle spike ~1.2M → ratio = 1.2M/166K ≈ 7x (exceeds 5x IMMEDIATE_MOVER threshold)
                oi = prev_oi * (1 + random.uniform(0.10, 0.15))
                vol = 1_000_000.0  # ~1M 24h volume → avg_4h = 166K
                funding = round(random.uniform(0.0001, 0.0003), 6)  # favorable
                mark_px = bp * (1 + self._tick * 0.02)  # trending up
            elif name == "BTC":
                # BTC: stable uptrend (good macro context)
                oi = prev_oi * (1 + random.uniform(0.01, 0.03))
                vol = prev_vol * random.uniform(0.9, 1.2) if prev_vol < 1e8 else random.uniform(5e7, 1e8)
                funding = round(random.uniform(-0.0001, 0.0002), 6)
                mark_px = bp * (1 + self._tick * 0.005)
            else:
                # Others: flat/random
                oi = prev_oi * (1 + random.uniform(-0.02, 0.02))
                vol = prev_vol * random.uniform(0.8, 1.2) if prev_vol < 1e8 else random.uniform(1e7, 5e7)
                funding = round(random.uniform(-0.0005, 0.0005), 6)
                mark_px = bp * (1 + random.uniform(-0.02, 0.02))

            self._oi_history[name] = oi
            self._vol_history[name] = vol

            asset_ctxs.append({
                "funding": str(funding),
                "openInterest": str(round(oi, 2)),
                "prevDayPx": str(round(bp * 0.98, 2)),
                "dayNtlVlm": str(round(vol, 2)),
                "markPx": str(round(mark_px, 2)),
            })
        return [{"universe": universe}, asset_ctxs]

    def get_all_mids(self) -> Dict[str, str]:
        """Return mock mid prices for all assets."""
        return {
            "BTC": "50000.0", "ETH": "2500.0", "SOL": "100.0", "DOGE": "0.15",
            "ARB": "1.2", "OP": "2.5", "AVAX": "35.0", "MATIC": "0.8",
            "LINK": "15.0", "UNI": "7.0", "AAVE": "100.0", "CRV": "0.5",
            "MKR": "1500.0", "SNX": "3.0", "COMP": "50.0",
        }

    def get_fills(self, since_ms: int = 0) -> List[HLFill]:
        """Get fills since a given timestamp."""
        return [f for f in self.fills if f.timestamp_ms >= since_ms]


class HLProxy:
    """Real Hyperliquid proxy using hyperliquid-python-sdk.

    Auth priority: keystore > HL_PRIVATE_KEY env var.
    Use TradingConfig.get_private_key() to resolve credentials.
    """

    def __init__(self, private_key: Optional[str] = None, testnet: bool = True):
        if private_key is None:
            try:
                from common.credentials import resolve_private_key
                private_key = resolve_private_key(venue="hl")
            except RuntimeError:
                private_key = ""
        self.private_key = private_key
        self.testnet = testnet
        self._info = None
        self._exchange = None
        self._address = None
        self.placed_orders: List[Dict] = []
        self.fills: List[HLFill] = []

    def _ensure_client(self):
        if self._info is not None:
            return
        from eth_account import Account
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        _patch_spot_meta_indexing()

        base_url = constants.TESTNET_API_URL if self.testnet else constants.MAINNET_API_URL
        self._info = Info(base_url, skip_ws=True, timeout=10)

        account = Account.from_key(self.private_key)
        self._address = account.address
        self._exchange = Exchange(account, base_url)
        log.info("HL client initialized: %s (testnet=%s)", self._address, self.testnet)

    def set_leverage(self, leverage: int, coin: str = "ETH", is_cross: bool = True):
        """Set leverage for a coin. Call explicitly instead of hardcoding on init."""
        self._ensure_client()
        try:
            self._exchange.update_leverage(leverage, coin, is_cross=is_cross)
            log.info("Set %s leverage to %dx %s", coin, leverage, "cross" if is_cross else "isolated")
        except Exception as e:
            log.warning("Failed to set %s leverage: %s", coin, e)

    @staticmethod
    def _hl_coin(instrument: str) -> str:
        """Convert internal instrument name to HL coin name (ETH-PERP → ETH)."""
        return instrument.replace("-PERP", "").replace("-perp", "")

    def get_snapshot(self, instrument: str = "ETH-PERP") -> MarketSnapshot:
        """Get real market data from HL."""
        self._ensure_client()
        try:
            coin = self._hl_coin(instrument)
            book = self._info.l2_snapshot(coin)
            bids = book.get("levels", [[]])[0] if book.get("levels") else []
            asks = book.get("levels", [[], []])[1] if len(book.get("levels", [])) > 1 else []

            best_bid = float(bids[0]["px"]) if bids else 0.0
            best_ask = float(asks[0]["px"]) if asks else 0.0
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
            spread = ((best_ask - best_bid) / mid * 10000) if mid > 0 else 0.0

            return MarketSnapshot(
                instrument=instrument,
                mid_price=round(mid, 2),
                bid=round(best_bid, 2),
                ask=round(best_ask, 2),
                spread_bps=round(spread, 2),
                timestamp_ms=int(time.time() * 1000),
            )
        except (ConnectionError, OSError, TimeoutError) as e:
            log.error("HL snapshot network error for %s: %s", instrument, e)
            return MarketSnapshot(instrument=instrument)
        except Exception as e:
            log.error("HL snapshot unexpected error for %s: %s", instrument, e, exc_info=True)
            return MarketSnapshot(instrument=instrument)

    def place_orders_from_clearing(self, fills: List[Dict]) -> List[Dict]:
        """Place real orders on HL from clearing fills.

        Uses market-crossing IOC prices to guarantee execution:
        buys at ask + 0.5% slippage, sells at bid - 0.5% slippage.
        """
        self._ensure_client()
        placed = []

        # Get current orderbook for market-crossing prices
        ob_cache: Dict[str, Dict[str, float]] = {}
        for f in fills:
            inst = f["instrument"]
            if inst not in ob_cache:
                try:
                    snap = self.get_snapshot(inst)
                    ob_cache[inst] = {"bid": float(snap.bid), "ask": float(snap.ask)}
                except Exception:
                    ob_cache[inst] = {"bid": 0.0, "ask": 0.0}

        for f in fills:
            qty = Decimal(str(f.get("quantity_filled", "0")))
            if qty <= ZERO:
                continue

            is_buy = f["side"] == "buy"
            sz = float(qty)
            inst = f["instrument"]
            ob = ob_cache.get(inst, {"bid": 0.0, "ask": 0.0})

            # Use aggressive market-crossing price (0.5% slippage)
            if is_buy:
                price = round(ob["ask"] * 1.005, 1)  # above ask
            else:
                price = round(ob["bid"] * 0.995, 1)  # below bid

            if price <= 0:
                price = float(f["fill_price"])  # fallback to clearing price

            try:
                coin = self._hl_coin(inst)
                result = self._exchange.order(
                    coin,
                    is_buy,
                    sz,
                    price,
                    {"limit": {"tif": "Ioc"}},
                )

                # Handle top-level error
                if result.get("status") == "err":
                    log.warning("HL order rejected: %s %s %s @ %s — %s",
                                f["side"], sz, f["instrument"], price, result.get("response"))
                    continue

                # Parse successful response
                resp = result.get("response", {})
                if not isinstance(resp, dict):
                    log.warning("HL unexpected response: %s", resp)
                    continue

                statuses = resp.get("data", {}).get("statuses", [])
                status = statuses[0] if statuses else {}

                if isinstance(status, str):
                    log.warning("HL order status string: %s", status)
                elif "filled" in status:
                    filled_info = status["filled"]
                    self.fills.append(HLFill(
                        oid=filled_info.get("oid", ""),
                        instrument=f["instrument"],
                        side=f["side"],
                        price=Decimal(str(filled_info.get("avgPx", price))),
                        quantity=Decimal(str(filled_info.get("totalSz", sz))),
                        timestamp_ms=int(time.time() * 1000),
                    ))
                    log.info("HL filled: %s %s %s @ %s",
                             f["side"], filled_info.get("totalSz", sz),
                             f["instrument"], filled_info.get("avgPx", price))
                elif "resting" in status:
                    log.info("HL resting: %s %s %s @ %s", f["side"], sz, f["instrument"], price)
                elif "error" in status:
                    log.info("HL no fill: %s %s %s @ %s — %s",
                             f["side"], sz, f["instrument"], price, status["error"])
                else:
                    log.warning("HL order status: %s", status)

                placed.append(f)
                self.placed_orders.append(f)
            except (ConnectionError, OSError, TimeoutError) as e:
                log.error("HL order network error: %s %s %s @ %s — %s",
                          f["side"], sz, f["instrument"], price, e)
            except Exception as e:
                log.critical("HL order unexpected failure: %s %s %s @ %s — %s",
                             f["side"], sz, f["instrument"], price, e, exc_info=True)

        log.info("Placed %d/%d orders on HL", len(placed),
                 sum(1 for f in fills if Decimal(str(f.get("quantity_filled", "0"))) > ZERO))
        return placed

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        """Fetch candle data from HL."""
        self._ensure_client()
        end = int(time.time() * 1000)
        start = end - lookback_ms
        return self._info.candles_snapshot(coin, interval, start, end)

    def get_meta_and_asset_ctxs(self) -> Any:
        """Fetch metadata and asset contexts for all perps."""
        self._ensure_client()
        return self._info.meta_and_asset_ctxs()

    def get_all_mids(self) -> Dict[str, str]:
        """Fetch mid prices for all assets."""
        self._ensure_client()
        return self._info.all_mids()

    def get_fills(self, since_ms: int = 0) -> List[HLFill]:
        """Get fills from HL user state."""
        if self._info and self._address:
            try:
                user_fills = self._info.user_fills(self._address)
                for uf in user_fills:
                    ts = int(uf.get("time", 0))
                    if ts >= since_ms:
                        self.fills.append(HLFill(
                            oid=uf.get("oid", ""),
                            instrument=uf.get("coin", ""),
                            side=uf.get("side", "").lower(),
                            price=Decimal(str(uf.get("px", "0"))),
                            quantity=Decimal(str(uf.get("sz", "0"))),
                            timestamp_ms=ts,
                            fee=Decimal(str(uf.get("fee", "0"))),
                        ))
            except (ConnectionError, OSError, TimeoutError) as e:
                log.error("Failed to fetch HL fills (network): %s", e)
            except Exception as e:
                log.error("Failed to fetch HL fills: %s", e, exc_info=True)
        return [f for f in self.fills if f.timestamp_ms >= since_ms]
