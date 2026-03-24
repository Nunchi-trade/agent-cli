"""Production quoting engine market maker.

Wraps the full QuotingEngine pipeline: composite fair value (4-signal blend),
dynamic spread (fee + vol + toxicity + event), inventory skew, multi-level
quote ladder, risk regime classification. Auto-halts on oracle staleness.
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


class EngineMMStrategy(BaseStrategy):
    """Multi-level inventory-aware market maker using QuotingEngine."""

    def __init__(
        self,
        strategy_id: str = "engine_mm",
        config: Optional[MarketConfig] = None,
        base_size: float = 1.0,
        num_levels: int = 3,
        **kwargs,
    ):
        super().__init__(strategy_id=strategy_id)

        if config is None:
            config = MarketConfig()
        config.ladder.s0 = base_size
        config.ladder.num_levels = num_levels
        self._config = config

        self._vol_bin = VolBinClassifier()
        self._hl_funding = HyperliquidFundingRate()
        self._funding_feed = CrossVenueFundingRate(sources=[self._hl_funding])

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

        if snapshot.funding_rate != 0.0:
            self._hl_funding.update(snapshot.funding_rate)
            self._funding_feed.refresh()

        result = self._engine.tick(
            mid=mid,
            bid=snapshot.bid,
            ask=snapshot.ask,
            inventory=inventory,
            daily_drawdown_pct=daily_dd,
            reduce_only=reduce_only,
            timestamp_ms=snapshot.timestamp_ms,
            open_interest=snapshot.open_interest,
        )

        if result.halted:
            return []

        meta: Dict[str, Any] = {
            "vol_bin": result.vol_bin,
            "m_vol": result.m_vol,
            "fv_raw": round(result.fv_raw, 4),
            "fv_skewed": round(result.fv_skewed, 4),
            "half_spread": round(result.half_spread, 4),
            "sigma": round(result.sigma_price, 4),
            "inventory": inventory,
        }

        if result.reduce_only:
            return self._reduce_only(snapshot, result, inventory, meta)

        orders: List[StrategyDecision] = []
        for level in result.levels:
            lmeta = {**meta, "ladder_level": level.level}
            if level.bid_size > 0:
                orders.append(StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="buy", size=level.bid_size, limit_price=level.bid_price,
                    meta={**lmeta, "signal": "engine_bid"},
                ))
            if level.ask_size > 0:
                orders.append(StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="sell", size=level.ask_size, limit_price=level.ask_price,
                    meta={**lmeta, "signal": "engine_ask"},
                ))
        return orders

    def _reduce_only(self, snapshot, result, inventory, meta):
        if not result.levels or inventory == 0.0:
            return []
        lv = result.levels[0]
        if inventory > 0:
            return [StrategyDecision(
                action="place_order", instrument=snapshot.instrument,
                side="sell", size=min(lv.ask_size, abs(inventory)),
                limit_price=lv.ask_price,
                meta={**meta, "signal": "reduce_only_sell"},
            )]
        return [StrategyDecision(
            action="place_order", instrument=snapshot.instrument,
            side="buy", size=min(lv.bid_size, abs(inventory)),
            limit_price=lv.bid_price,
            meta={**meta, "signal": "reduce_only_buy"},
        )]
