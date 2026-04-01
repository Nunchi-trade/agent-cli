"""StrategyGuard — bridge between BaseStrategy instances and the APEX engine.

Wraps any BaseStrategy into the Guard pattern so APEX can consume its signals
alongside Pulse and Radar. Constructs MarketSnapshot from APEX market data,
runs on_tick(), and converts StrategyDecision outputs into signal dicts.

Supports per-market strategy routing via MARKET_STRATEGY_MAP: when target_markets
are provided, only runs the structurally appropriate strategies for each market.
"""
from __future__ import annotations

import importlib
import logging
import time
from typing import Any, Dict, List, Optional, Set

from common.models import MarketSnapshot, StrategyDecision, asset_to_instrument, instrument_to_asset
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

log = logging.getLogger("strategy_guard")


class StrategyGuard:
    """Owns one or more BaseStrategy instances and bridges them to APEX."""

    def __init__(
        self,
        strategy_names: Optional[List[str]] = None,
        enabled: bool = True,
        target_markets: Optional[List[str]] = None,
    ):
        self.enabled = enabled
        self.target_markets = target_markets or []

        self._strategy_cache: Dict[str, BaseStrategy] = {}
        self.strategies: List[BaseStrategy] = []
        for name in (strategy_names or []):
            strat = self._get_or_load(name)
            if strat:
                self.strategies.append(strat)

    def _get_or_load(self, name: str) -> Optional[BaseStrategy]:
        """Load a strategy by name, caching instances."""
        if name in self._strategy_cache:
            return self._strategy_cache[name]
        strat = self._load_strategy(name)
        if strat:
            self._strategy_cache[name] = strat
        return strat

    @staticmethod
    def _load_strategy(name: str) -> Optional[BaseStrategy]:
        """Load a strategy by registry name or module:class path."""
        try:
            from cli.strategy_registry import resolve_strategy_path
            path = resolve_strategy_path(name)
            module_path, class_name = path.rsplit(":", 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            return cls()
        except Exception as e:
            log.error("Failed to load strategy '%s': %s", name, e)
            return None

    def scan(
        self,
        all_markets: list,
        slot_prices: Optional[Dict[str, float]] = None,
        target_markets: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Run strategies against market data with optional per-market routing.

        When target_markets are provided, uses MARKET_STRATEGY_MAP to determine
        which strategies to run per market. Markets without a mapping are skipped
        (they use Pulse/Radar only). Falls back to legacy behavior (all strategies
        × all markets) when no target_markets are set.

        Returns list of signal dicts compatible with ApexEngine._evaluate_entries:
            {"asset": str, "direction": str, "confidence": float, "source": str}
        """
        if not self.enabled:
            return []

        effective_targets = target_markets or self.target_markets

        if effective_targets:
            return self._scan_routed(all_markets, effective_targets)

        if not self.strategies:
            return []
        snapshots = self._build_snapshots(all_markets)
        if not snapshots:
            return []
        return self._run_strategies_on_snapshots(self.strategies, snapshots)

    def _scan_routed(
        self,
        all_markets: list,
        target_markets: List[str],
    ) -> List[Dict[str, Any]]:
        """Run per-market routed strategies using MARKET_STRATEGY_MAP."""
        from modules.market_strategy_map import get_strategies_for_market

        target_assets = {instrument_to_asset(m) for m in target_markets}
        snapshots = self._build_snapshots(all_markets, only_assets=target_assets)
        if not snapshots:
            return []

        signals: List[Dict[str, Any]] = []

        for instrument in target_markets:
            strat_names = get_strategies_for_market(instrument)
            if not strat_names:
                continue

            snap = self._find_snapshot(snapshots, instrument)
            if not snap:
                continue

            strats = [s for name in strat_names if (s := self._get_or_load(name))]
            asset = instrument_to_asset(snap.instrument)
            for strat in strats:
                self._collect_signals(strat, snap, asset, signals)

        self._log_signals(signals, f"{len(target_markets)} markets (routed)")
        return signals

    @staticmethod
    def _find_snapshot(
        snapshots: Dict[str, MarketSnapshot],
        instrument: str,
    ) -> Optional[MarketSnapshot]:
        """Find a snapshot matching an instrument name.

        Snapshots are keyed by coin name (e.g., "BTC", "VXX").
        Instruments are "VXX-USDYP", "ETH-PERP", etc.
        """
        asset = instrument_to_asset(instrument)
        if asset in snapshots:
            return snapshots[asset]

        for snap in snapshots.values():
            if snap.instrument == instrument:
                return snap

        return None

    def _run_strategies_on_snapshots(
        self,
        strategies: List[BaseStrategy],
        snapshots: Dict[str, MarketSnapshot],
    ) -> List[Dict[str, Any]]:
        """Legacy: run all strategies against all snapshots."""
        signals: List[Dict[str, Any]] = []

        for strat in strategies:
            for coin, snap in snapshots.items():
                self._collect_signals(strat, snap, coin, signals)

        self._log_signals(signals, f"{len(strategies)} strategies × {len(snapshots)} assets")
        return signals

    @staticmethod
    def _collect_signals(
        strat: BaseStrategy,
        snap: MarketSnapshot,
        asset: str,
        signals: List[Dict[str, Any]],
    ) -> None:
        """Run a strategy on a snapshot and append entry signals to the list."""
        try:
            decisions = strat.on_tick(snap, StrategyContext())
        except Exception as e:
            log.debug("Strategy %s failed on %s: %s", strat.strategy_id, asset, e)
            return

        for dec in decisions:
            if dec.action != "place_order" or not dec.side:
                continue

            signals.append({
                "asset": asset,
                "direction": "long" if dec.side == "buy" else "short",
                "confidence": dec.meta.get("confidence", 75.0),
                "source": f"strategy:{strat.strategy_id}",
                "signal": dec.meta.get("signal", strat.strategy_id),
                "meta": dec.meta,
            })

    @staticmethod
    def _log_signals(signals: List[Dict[str, Any]], context: str) -> None:
        if not signals:
            return
        log.info("Strategy guard: %s → %d signals", context, len(signals))
        for sig in signals[:5]:
            log.info(
                "  %s %s %s (conf=%.0f, via %s)",
                sig["signal"], sig["direction"], sig["asset"],
                sig["confidence"], sig["source"],
            )

    @staticmethod
    def _build_snapshots(
        all_markets: list,
        only_assets: Optional[Set[str]] = None,
    ) -> Dict[str, MarketSnapshot]:
        """Build MarketSnapshot for each tradeable asset from HL market data.

        When only_assets is provided, skips assets not in the set.
        """
        snapshots: Dict[str, MarketSnapshot] = {}

        if len(all_markets) < 2:
            return snapshots

        universe = all_markets[0].get("universe", [])
        ctxs = all_markets[1]

        for i, ctx in enumerate(ctxs):
            if i >= len(universe):
                break

            try:
                name = universe[i].get("name", "")
            except (IndexError, AttributeError):
                continue

            if not name:
                continue

            if only_assets is not None and name not in only_assets:
                continue

            try:
                mid = float(ctx.get("midPx", 0) or 0)
                if mid <= 0:
                    continue

                mark = float(ctx.get("markPx", mid) or mid)
                half_spread = mid * 0.0001
                bid = mid - half_spread
                ask = mid + half_spread
                spread_bps = (ask - bid) / mid * 10_000 if mid > 0 else 0

                vol_24h = float(ctx.get("dayNtlVlm", 0) or 0)
                funding = float(ctx.get("funding", 0) or 0)
                oi = float(ctx.get("openInterest", 0) or 0)

                snapshots[name] = MarketSnapshot(
                    instrument=asset_to_instrument(name),
                    mid_price=mid,
                    bid=bid,
                    ask=ask,
                    spread_bps=spread_bps,
                    timestamp_ms=int(time.time() * 1000),
                    volume_24h=vol_24h,
                    funding_rate=funding,
                    open_interest=oi,
                )
            except (ValueError, TypeError):
                continue

        return snapshots
