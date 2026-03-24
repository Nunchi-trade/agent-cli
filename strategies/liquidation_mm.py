"""Liquidation flow market maker.

Provides liquidity during forced liquidation cascades. Detects OI drops
as a proxy for liquidations, widens spreads to protect against toxic flow,
and provides deeper liquidity on the contra side to capture cascade spread.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common.models import MarketSnapshot, StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext
from strategies.risk_multipliers import VolBinClassifier, dd_multiplier

from quoting_engine.config import MarketConfig, LiquidationDetectorConfig
from quoting_engine.engine import QuotingEngine
from quoting_engine.feeds.oracle_monitor import OracleFreshnessMonitor, OracleMonitorConfig
from quoting_engine.feeds.microprice import L2MicropriceCalculator
from quoting_engine.feeds.funding_rate import CrossVenueFundingRate, HyperliquidFundingRate
from quoting_engine.toxicity import StubToxicityScorer
from quoting_engine.event_schedule import StubEventSchedule


class LiquidationMMStrategy(BaseStrategy):
    """Market maker that adapts to liquidation cascade events."""

    def __init__(
        self,
        strategy_id: str = "liquidation_mm",
        config: Optional[MarketConfig] = None,
        base_size: float = 1.0,
        oi_drop_threshold_pct: float = 5.0,
        cascade_spread_mult: float = 2.5,
        cascade_size_mult: float = 0.4,
        cooldown_ticks: int = 15,
        **kwargs,
    ):
        super().__init__(strategy_id=strategy_id)

        self.cascade_spread_mult = cascade_spread_mult
        self.cascade_size_mult = cascade_size_mult

        if config is None:
            config = MarketConfig()
        config.ladder.s0 = base_size

        # Enable liquidation detector
        config.liquidation_detector = LiquidationDetectorConfig(
            enabled=True,
            oi_drop_threshold_pct=oi_drop_threshold_pct,
            spread_mult=cascade_spread_mult,
            size_mult=cascade_size_mult,
            cooldown_ticks=cooldown_ticks,
        )
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

        # Track price direction for cascade side detection
        self._prev_mid: float = 0.0

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

        # Detect cascade direction from price movement
        price_dropping = mid < self._prev_mid if self._prev_mid > 0 else False
        self._prev_mid = mid

        liq_triggered = result.meta.get("liq_triggered", False)
        liq_cooldown = result.meta.get("liq_cooldown_remaining", 0)
        in_cascade = liq_triggered or liq_cooldown > 0

        if result.halted:
            return []

        meta: Dict[str, Any] = {
            "vol_bin": result.vol_bin,
            "fv_skewed": round(result.fv_skewed, 4),
            "half_spread": round(result.half_spread, 4),
            "sigma": round(result.sigma_price, 4),
            "liq_triggered": liq_triggered,
            "liq_cooldown": liq_cooldown,
            "in_cascade": in_cascade,
            "cascade_direction": "down" if price_dropping else "up",
            "inventory": inventory,
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
                meta={**meta, "signal": f"liq_reduce_{side}"},
            )]

        orders: List[StrategyDecision] = []

        for level in result.levels:
            lmeta = {**meta, "ladder_level": level.level}

            bid_size = level.bid_size
            ask_size = level.ask_size

            # During cascade: asymmetric sizing
            # If price dropping (long liquidations), reduce bid (cascade side),
            # increase ask (contra side — provide liquidity to forced sellers)
            if in_cascade:
                if price_dropping:
                    bid_size *= self.cascade_size_mult  # reduce cascade side
                    ask_size *= (2.0 - self.cascade_size_mult)  # increase contra
                else:
                    ask_size *= self.cascade_size_mult
                    bid_size *= (2.0 - self.cascade_size_mult)
                bid_size = round(bid_size, 6)
                ask_size = round(ask_size, 6)

            if bid_size > 0:
                orders.append(StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="buy", size=bid_size, limit_price=level.bid_price,
                    meta={**lmeta, "signal": "liq_bid" if in_cascade else "normal_bid"},
                ))
            if ask_size > 0:
                orders.append(StrategyDecision(
                    action="place_order", instrument=snapshot.instrument,
                    side="sell", size=ask_size, limit_price=level.ask_price,
                    meta={**lmeta, "signal": "liq_ask" if in_cascade else "normal_ask"},
                ))
        return orders
