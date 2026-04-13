"""Radar market-source builders for supported venues.

The RADAR engine was originally written against Hyperliquid's market/candle shapes.
This module adapts other venues into the minimal interface RADAR expects:

- get_all_markets() -> [meta_info, asset_ctxs]
- get_candles(asset, interval, lookback_ms) -> list[{t,o,h,l,c,v}]
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from cli.venue_factory import normalize_venue


class ParadexPublicRadarAdapter:
    """Public-market adapter that reshapes Paradex data for RADAR.

    Uses public endpoints only, so RADAR can screen Paradex without requiring
    private account credentials.
    """

    def __init__(self, *, mainnet: bool = False):
        from paradex_py import Paradex

        env = "prod" if mainnet else "testnet"
        self._client = Paradex(env=env, auto_auth=False).api_client
        self._symbol_by_coin: Dict[str, str] = {}
        self._all_markets = self._load_markets()

    def _load_markets(self) -> list:
        response = self._client.fetch_markets()
        market_rows = response.get("results", []) if isinstance(response, dict) else []
        perps = [
            row for row in market_rows
            if row.get("asset_kind") == "PERP" and str(row.get("symbol", "")).endswith("-USD-PERP")
        ]

        universe: List[Dict[str, Any]] = []
        asset_ctxs: List[Dict[str, Any]] = []
        for market in perps:
            symbol = str(market.get("symbol", ""))
            coin = symbol.removesuffix("-USD-PERP")
            if not coin:
                continue

            self._symbol_by_coin[coin] = symbol
            summary_resp = self._client.fetch_markets_summary({"market": symbol})
            summary_rows = summary_resp.get("results", []) if isinstance(summary_resp, dict) else []
            summary = summary_rows[0] if summary_rows else {}

            universe.append({"name": coin})
            asset_ctxs.append(
                {
                    "dayNtlVlm": float(summary.get("volume_24h", 0) or 0),
                    "funding": float(summary.get("funding_rate", 0) or 0),
                    "openInterest": float(summary.get("open_interest", 0) or 0),
                    "markPx": float(summary.get("mark_price", 0) or 0),
                }
            )

        return [{"universe": universe}, asset_ctxs]

    def get_all_markets(self) -> list:
        return self._all_markets

    def get_candles(self, asset: str, interval: str, lookback_ms: int) -> List[Dict[str, Any]]:
        symbol = self._symbol_by_coin.get(asset, asset if asset.endswith("-USD-PERP") else f"{asset}-USD-PERP")
        resolution = {"15m": "15", "1h": "60", "4h": "60"}.get(interval)
        if resolution is None:
            raise ValueError(f"Unsupported RADAR interval for Paradex: {interval}")

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - int(lookback_ms)
        response = self._client.fetch_klines(symbol, resolution, start_ms, end_ms)
        raw_rows = response.get("results", []) if isinstance(response, dict) else []
        candles = [self._row_to_candle(row) for row in raw_rows if isinstance(row, (list, tuple)) and len(row) >= 6]

        if interval == "4h":
            return self._aggregate_to_4h(candles)
        return candles

    @staticmethod
    def _row_to_candle(row: list | tuple) -> Dict[str, Any]:
        ts, o, h, l, c, v = row[:6]
        return {"t": ts, "o": o, "h": h, "l": l, "c": c, "v": v}

    @staticmethod
    def _aggregate_to_4h(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: List[Dict[str, Any]] = []
        bucket: List[Dict[str, Any]] = []
        for candle in candles:
            bucket.append(candle)
            if len(bucket) == 4:
                grouped.append(
                    {
                        "t": bucket[0]["t"],
                        "o": bucket[0]["o"],
                        "h": max(float(item["h"]) for item in bucket),
                        "l": min(float(item["l"]) for item in bucket),
                        "c": bucket[-1]["c"],
                        "v": sum(float(item["v"]) for item in bucket),
                    }
                )
                bucket = []
        return grouped


def build_radar_market_source(*, venue: str, mainnet: bool = False, mock: bool = False):
    """Build a RADAR-compatible market source for the requested venue."""
    normalized = normalize_venue(venue)

    if mock:
        from cli.hl_adapter import DirectMockProxy

        return DirectMockProxy(), "MOCK"

    if normalized == "hl":
        from cli.config import TradingConfig
        from cli.hl_adapter import DirectHLProxy
        from parent.hl_proxy import HLProxy

        private_key = TradingConfig(venue=normalized).get_private_key()
        raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
        return DirectHLProxy(raw_hl), f"LIVE ({'mainnet' if mainnet else 'testnet'})"

    if normalized == "paradex":
        source = ParadexPublicRadarAdapter(mainnet=mainnet)
        return source, f"LIVE ({'mainnet' if mainnet else 'testnet'})"

    raise AssertionError(f"Unhandled venue: {normalized}")
