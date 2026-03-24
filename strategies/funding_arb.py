"""Cross-venue funding rate arbitrage strategy.

Captures funding rate dislocations between HL and external venues.
When HL funding diverges from the cross-venue median, biases quotes
to collect the premium. Especially valuable for YEX yield perps
where funding IS the product.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext
from strategies.risk_multipliers import VolBinClassifier, dd_multiplier

from quoting_engine.config import MarketConfig
from quoting_engine.engine import QuotingEngine
from quoting_engine.feeds.oracle_monitor import OracleFreshnessMonitor, OracleMonitorConfig
from quoting_engine.feeds.microprice import L2MicropriceCalculator
from quoting_engine.feeds.funding_rate import (
    CrossVenueFundingRate,
    HyperliquidFundingRate,
    PushFundingRate,
)
from quoting_engine.toxicity import StubToxicityScorer
from quoting_engine.event_schedule import StubEventSchedule


class FundingArbStrategy(BaseStrategy):
    """Funding rate dislocation strategy with asymmetric quoting."""

    def __init__(
        self,
        strategy_id: str = "funding_arb",
        config: Optional[MarketConfig] = None,
        base_size: float = 1.0,
        divergence_threshold_bps: float = 2.0,
        max_bias_bps: float = 5.0,
        funding_weight: float = 0.4,
        **kwargs,
    ):
        super().__init__(strategy_id=strategy_id)

        self.divergence_threshold_bps = divergence_threshold_bps
        self.max_bias_bps = max_bias_bps
        self.funding_weight = funding_weight

        if config is None:
            config = MarketConfig()
        config.ladder.s0 = base_size
        self._config = config

        self._vol_bin = VolBinClassifier()

        # HL funding + external sources
        self._hl_funding = HyperliquidFundingRate()
        self._ext_sources: Dict[str, PushFundingRate] = {
            v: PushFundingRate(v) for v in ["binance", "okx", "bybit"]
        }
        all_sources = [self._hl_funding] + list(self._ext_sources.values())
        self._funding_feed = CrossVenueFundingRate(sources=all_sources)

        self._engine = QuotingEngine(
            self._config,
            toxicity_scorer=StubToxicityScorer(),
            event_schedule=StubEventSchedule(),
            oracle_monitor=OracleFreshnessMonitor(OracleMonitorConfig(enabled=False)),
            microprice_calc=L2MicropriceCalculator(),
            funding_feed=self._funding_feed,
        )
        self._engine.set_risk_classifiers(
            vol_bin_classify=self._vol_bin.classify,
            dd_multiplier=dd_multiplier,
        )

        self._last_hl_rate: float = 0.0

    def on_tick(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext] = None,
    ) -> List[StrategyDecision]:
        mid = snapshot.mid_price
        if mid <= 0:
            return []

        inventory = context.position_qty if context else 0.0
        daily_dd = getattr(context, "daily_drawdown_pct", 0.0) if context else 0.0
        reduce_only = context.reduce_only if context else False

        # Update funding feeds
        if snapshot.funding_rate != 0.0:
            self._hl_funding.update(snapshot.funding_rate)
            self._last_hl_rate = snapshot.funding_rate
        self._funding_feed.refresh()

        # Compute funding divergence
        median_result = self._funding_feed.latest()
        median_rate = median_result.value if median_result else self._last_hl_rate
        divergence = self._last_hl_rate - median_rate  # positive = HL funding higher

        # Convert divergence to FV bias in price units
        divergence_bps = divergence * 10_000  # funding rates are already in decimal
        bias_bps = 0.0
        if abs(divergence_bps) > self.divergence_threshold_bps:
            # Clamp bias to max_bias_bps
            raw_bias = divergence_bps * self.funding_weight
            bias_bps = max(-self.max_bias_bps, min(self.max_bias_bps, raw_bias))

        # Pass funding-adjusted price as external_ref
        # Positive divergence → HL funding too high → short bias → lower FV
        external_ref = mid - (bias_bps * mid / 10_000) if bias_bps != 0 else 0.0

        result = self._engine.tick(
            mid=mid,
            bid=snapshot.bid,
            ask=snapshot.ask,
            inventory=inventory,
            daily_drawdown_pct=daily_dd,
            reduce_only=reduce_only,
            timestamp_ms=snapshot.timestamp_ms,
            external_ref=external_ref,
            open_interest=snapshot.open_interest,
        )

        if result.halted:
            return []

        meta: Dict[str, Any] = {
            "divergence_bps": round(divergence_bps, 4),
            "bias_bps": round(bias_bps, 4),
            "hl_rate": self._last_hl_rate,
            "median_rate": median_rate,
            "fv_skewed": round(result.fv_skewed, 4),
            "half_spread": round(result.half_spread, 4),
            "vol_bin": result.vol_bin,
        }

        if result.reduce_only:
            if not result.levels or inventory == 0.0:
                return []
            lv = result.levels[0]
            side = "sell" if inventory > 0 else "buy"
            price = lv.ask_price if inventory > 0 else lv.bid_price
            size = min(lv.ask_size if inventory > 0 else lv.bid_size, abs(inventory))
            return [StrategyDecision(
                action="place_order", instrument=snapshot.instrument,
                side=side, size=size, limit_price=price,
                meta={**meta, "signal": f"reduce_only_{side}"},
            )]

        # Asymmetric sizing: favor the side that collects the premium
        bid_mult = 1.0
        ask_mult = 1.0
        if bias_bps > 0:  # short bias — increase ask, decrease bid
            ask_mult = 1.0 + abs(bias_bps) / self.max_bias_bps * 0.3
            bid_mult = 1.0 - abs(bias_bps) / self.max_bias_bps * 0.3
        elif bias_bps < 0:  # long bias — increase bid, decrease ask
            bid_mult = 1.0 + abs(bias_bps) / self.max_bias_bps * 0.3
            ask_mult = 1.0 - abs(bias_bps) / self.max_bias_bps * 0.3

        orders: List[StrategyDecision] = []
        for level in result.levels:
            lmeta = {**meta, "ladder_level": level.level}
            if level.bid_size > 0:
                orders.append(StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="buy", size=round(level.bid_size * bid_mult, 6),
                    limit_price=level.bid_price,
                    meta={**lmeta, "signal": "funding_bid"},
                ))
            if level.ask_size > 0:
                orders.append(StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="sell", size=round(level.ask_size * ask_mult, 6),
                    limit_price=level.ask_price,
                    meta={**lmeta, "signal": "funding_ask"},
                ))
        return orders
