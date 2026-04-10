"""Paradex VenueAdapter — thin bridge around ParadexProxy.

Keeps SDK/auth/reconciliation details in parent/paradex_proxy.py and exposes the
repo's venue-agnostic adapter interface.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from common.models import MarketSnapshot
from common.venue_adapter import Fill, VenueAdapter, VenueCapabilities
from parent.paradex_proxy import ParadexFill, ParadexProxy

log = logging.getLogger("adapters.paradex")


def _paradex_fill_to_fill(fill: ParadexFill) -> Fill:
    return Fill(
        oid=fill.oid,
        instrument=fill.instrument,
        side=fill.side,
        price=float(fill.price),
        quantity=float(fill.quantity),
        timestamp_ms=fill.timestamp_ms,
        fee=float(fill.fee),
    )


class ParadexVenueAdapter(VenueAdapter):
    """VenueAdapter implementation backed by ParadexProxy."""

    def __init__(self, proxy: ParadexProxy):
        self._proxy = proxy

    def connect(self, private_key: str, testnet: bool = True) -> None:
        self._proxy.connect()

    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            supports_alo=False,
            supports_trigger_orders=False,
            supports_builder_fee=False,
            supports_cross_margin=False,
        )

    def get_snapshot(self, instrument: str) -> MarketSnapshot:
        market = self._proxy.get_market_metadata(instrument)
        bid = self._coerce_float(market, "best_bid", "bid", "bid_price")
        ask = self._coerce_float(market, "best_ask", "ask", "ask_price")
        mid = self._coerce_float(market, "mark_price", "mid", "mid_price", "index_price", "last_price")
        if mid <= 0 and bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        spread = ((ask - bid) / mid * 10000) if mid > 0 and bid > 0 and ask > 0 else 0.0
        return MarketSnapshot(
            instrument=instrument,
            mid_price=mid,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            timestamp_ms=int(time.time() * 1000),
            volume_24h=self._coerce_float(market, "volume_24h", "turnover_24h", "quote_volume_24h"),
            open_interest=self._coerce_float(market, "open_interest", "openInterest"),
        )

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        return self._proxy.fetch_candles(coin, interval, lookback_ms)

    def get_all_markets(self) -> list:
        return self._proxy.fetch_markets()

    def get_all_mids(self) -> Dict[str, str]:
        mids: Dict[str, str] = {}
        for market in self._proxy.fetch_markets():
            instrument = str(market.get("symbol") or market.get("market") or market.get("instrument") or "")
            if not instrument:
                continue
            mid = self._coerce_float(market, "mark_price", "mid", "mid_price", "index_price", "last_price")
            if mid <= 0:
                bid = self._coerce_float(market, "best_bid", "bid", "bid_price")
                ask = self._coerce_float(market, "best_ask", "ask", "ask_price")
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2
            mids[instrument] = str(mid if mid > 0 else 0.0)
        return mids

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[dict] = None,
    ) -> Optional[Fill]:
        order = {
            "symbol": instrument,
            "side": side.upper(),
            "size": size,
            "price": price,
            "time_in_force": tif.upper(),
        }
        if builder:
            log.debug("Ignoring builder fee payload for Paradex order: %s", builder)
        result = self._proxy.submit_order(order)
        if not result:
            return None
        fill_like = {
            "id": result.get("id") or result.get("order_id") or result.get("client_id") or "",
            "symbol": result.get("symbol") or instrument,
            "side": result.get("side") or side,
            "price": result.get("avg_price") or result.get("price") or price,
            "size": result.get("filled_size") or result.get("size") or size,
            "timestamp_ms": result.get("timestamp_ms") or result.get("timestamp") or int(time.time() * 1000),
            "fee": result.get("fee") or 0.0,
        }
        return _paradex_fill_to_fill(self._proxy.record_fill(fill_like))

    def cancel_order(self, instrument: str, oid: str) -> bool:
        result = self._proxy.cancel_order(oid)
        return bool(result)

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        orders = self._proxy.fetch_orders()
        if not instrument:
            return orders
        instrument_upper = instrument.upper()
        filtered: List[Dict] = []
        for order in orders:
            symbol = str(order.get("symbol") or order.get("market") or order.get("instrument") or "")
            if symbol.upper() == instrument_upper:
                filtered.append(order)
        return filtered

    def get_account_state(self) -> Dict:
        return self._proxy.get_account_state()

    def set_leverage(self, leverage: int, coin: str, is_cross: bool = True) -> None:
        log.info("Paradex leverage control not implemented yet; requested leverage=%s coin=%s cross=%s", leverage, coin, is_cross)

    @staticmethod
    def _coerce_float(data: Dict[str, object], *keys: str) -> float:
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0
