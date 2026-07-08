"""Volatility-regime adaptive market maker.

Dynamically switches quoting behavior based on the vol regime classifier.
In calm markets: tight spreads, larger size, more levels.
In volatile/extreme: wide spreads, minimal size, survival mode.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext
from strategies.risk_multipliers import VolBinClassifier, dd_multiplier

from quoting_engine.config import MarketConfig, SpreadParams, LadderParams
from quoting_engine.engine import QuotingEngine
from quoting_engine.feeds.oracle_monitor import OracleFreshnessMonitor, OracleMonitorConfig
from quoting_engine.feeds.microprice import L2MicropriceCalculator
from quoting_engine.feeds.funding_rate import CrossVenueFundingRate, HyperliquidFundingRate
from quoting_engine.toxicity import StubToxicityScorer
from quoting_engine.event_schedule import StubEventSchedule

# Regime-specific overrides: (min_spread_bps, max_spread_bps, size_mult, num_levels)
REGIME_PARAMS = {
    "I_low":      (2.0,  8.0,  1.5, 4),   # calm: tight, large, aggressive
    "II_normal":  (5.0,  20.0, 1.0, 3),   # normal: balanced
    "III_high":   (15.0, 40.0, 0.5, 2),   # volatile: wide, small, defensive
    "IV_extreme": (30.0, 80.0, 0.2, 1),   # extreme: survival mode
}


class RegimeMMStrategy(BaseStrategy):
    """Market maker that adapts quoting parameters to volatility regime."""

    def __init__(
        self,
        strategy_id: str = "regime_mm",
        config: Optional[MarketConfig] = None,
        base_size: float = 1.0,
        **kwargs,
    ):
        super().__init__(strategy_id=strategy_id)

        self._base_size = base_size
        if config is None:
            config = MarketConfig()
        config.ladder.s0 = base_size
        self._config = config

        self._vol_bin = VolBinClassifier()
        self._hl_funding = HyperliquidFundingRate()
        self._funding_feed = CrossVenueFundingRate(sources=[self._hl_funding])
        self._current_regime = "II_normal"

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

    def _apply_regime(self, regime: str) -> None:
        """Adjust engine config for the current volatility regime."""
        params = REGIME_PARAMS.get(regime, REGIME_PARAMS["II_normal"])
        min_sp, max_sp, size_mult, n_levels = params

        self._config.spread.min_spread_bps = min_sp
        self._config.spread.max_spread_bps = max_sp
        self._config.ladder.s0 = self._base_size * size_mult
        self._config.ladder.num_levels = n_levels
        self._current_regime = regime

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

        # Adapt regime based on engine's vol classification
        new_regime = result.vol_bin
        if new_regime and new_regime != self._current_regime:
            self._apply_regime(new_regime)

        if result.halted:
            return []

        meta: Dict[str, Any] = {
            "regime": self._current_regime,
            "vol_bin": result.vol_bin,
            "m_vol": result.m_vol,
            "m_dd": result.m_dd,
            "fv_skewed": round(result.fv_skewed, 4),
            "half_spread": round(result.half_spread, 4),
            "sigma": round(result.sigma_price, 4),
            "inventory": inventory,
        }

        # In extreme regime with reduce_only, aggressively unwind
        if result.reduce_only or self._current_regime == "IV_extreme":
            if not result.levels or inventory == 0.0:
                return []
            lv = result.levels[0]
            if inventory > 0:
                return [StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="sell", size=min(lv.ask_size, abs(inventory)),
                    limit_price=lv.ask_price,
                    meta={**meta, "signal": "regime_reduce_sell"},
                )]
            return [StrategyDecision(
                action="place_order", instrument=snapshot.instrument,
                side="buy", size=min(lv.bid_size, abs(inventory)),
                limit_price=lv.bid_price,
                meta={**meta, "signal": "regime_reduce_buy"},
            )]

        orders: List[StrategyDecision] = []
        for level in result.levels:
            lmeta = {**meta, "ladder_level": level.level}
            if level.bid_size > 0:
                orders.append(StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="buy", size=level.bid_size, limit_price=level.bid_price,
                    meta={**lmeta, "signal": "regime_bid"},
                ))
            if level.ask_size > 0:
                orders.append(StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="sell", size=level.ask_size, limit_price=level.ask_price,
                    meta={**lmeta, "signal": "regime_ask"},
                ))
        return orders
