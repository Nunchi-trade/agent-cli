"""QuotingEngine: top-level orchestrator wiring all components together.

This is the primary interface consumed by both CompositeMMStrategy (TEE)
and ABM integrations.

Phase 2 additions:
  - Oracle freshness monitor (halt / spread_mult / reduce_only)
  - L2 microprice override for fair value
  - Funding feed as external_ref
  - Event schedule mid update
"""
from __future__ import annotations

import datetime
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from quoting_engine.config import MarketConfig, RegimeOverride
from quoting_engine.fair_value import FairValueCalculator
from quoting_engine.spread import SpreadCalculator
from quoting_engine.ladder import LadderBuilder, LadderLevel
from quoting_engine.inventory import InventorySkewer
from quoting_engine.toxicity import BaseToxicityScorer, StubToxicityScorer
from quoting_engine.event_schedule import BaseEventSchedule, StubEventSchedule
from quoting_engine.vol_estimator import RollingVolEstimator
from quoting_engine.feeds.oracle_monitor import OracleFreshnessMonitor
from quoting_engine.feeds.microprice import L2Book, L2MicropriceCalculator
from quoting_engine.feeds.funding_rate import CrossVenueFundingRate
from quoting_engine.metrics import QuotingMetrics


@dataclass
class QuoteResult:
    """Output of a single engine tick."""
    fv_raw: float                               # fair value before inventory skew
    fv_skewed: float                            # fair value after inventory skew
    half_spread: float                          # half-spread in price units
    sigma_price: float                          # rolling vol in price units
    sigma_log: float                            # rolling vol unitless
    m_vol: float                                # vol-bin multiplier
    vol_bin: str                                # vol-bin name
    m_dd: float                                 # drawdown multiplier
    dd_bin: str                                 # drawdown bin name
    levels: List[LadderLevel] = field(default_factory=list)
    halted: bool = False                        # True if DD red zone
    reduce_only: bool = False                   # True if DD orange zone
    meta: Dict[str, Any] = field(default_factory=dict)


class QuotingEngine:
    """Stateful quoting engine for a single market.

    Maintains rolling vol estimator and vol-bin classifier state across ticks.
    One engine instance per market.
    """

    def __init__(
        self,
        config: MarketConfig,
        toxicity_scorer: Optional[BaseToxicityScorer] = None,
        event_schedule: Optional[BaseEventSchedule] = None,
        oracle_monitor: Optional[OracleFreshnessMonitor] = None,
        microprice_calc: Optional[L2MicropriceCalculator] = None,
        funding_feed: Optional[CrossVenueFundingRate] = None,
        metrics: Optional[QuotingMetrics] = None,
    ):
        self.config = config
        self._metrics = metrics

        # Sub-components
        self._fv_calc = FairValueCalculator(config.fv_weights)
        self._spread_calc = SpreadCalculator(config.spread, config.tick_size)
        self._ladder = LadderBuilder(config.ladder, config.tick_size)
        self._skewer = InventorySkewer(config.skew)
        self._tox = toxicity_scorer or StubToxicityScorer()
        self._events = event_schedule or StubEventSchedule()
        self._vol = RollingVolEstimator(window=config.vol_window)

        # Phase 2 feeds
        self._oracle_monitor = oracle_monitor
        self._microprice_calc = microprice_calc
        self._funding_feed = funding_feed

        # Risk classifiers — injected, not imported from TEE
        self._vol_bin_classify: Optional[Callable[[float], Tuple[float, str]]] = None
        self._dd_multiplier: Optional[Callable[[float], Tuple[float, str]]] = None

        # Liquidation flow detection state
        self._prev_oi: float = 0.0
        self._liq_cooldown_remaining: int = 0
        self._liq_total_cooldown: int = 0  # total cooldown length for escalation tracking

        # Mid-burst detection (G6)
        liq_window = config.liquidation_detector.mid_burst_window
        self._mid_history_short: deque = deque(maxlen=max(liq_window, 1))

        # Engine tick counter (for micro-clip interval tracking)
        self._tick_count: int = 0

    def set_risk_classifiers(
        self,
        vol_bin_classify: Callable[[float], Tuple[float, str]],
        dd_multiplier: Callable[[float], Tuple[float, str]],
    ) -> None:
        """Inject risk classifiers from the TEE risk_multipliers module."""
        self._vol_bin_classify = vol_bin_classify
        self._dd_multiplier = dd_multiplier

    def _classify_vol(self, sigma_log: float) -> Tuple[float, str]:
        if self._vol_bin_classify:
            return self._vol_bin_classify(sigma_log)
        return 1.0, "default"

    def _get_dd_mult(self, daily_drawdown_pct: float) -> Tuple[float, str]:
        if self._dd_multiplier:
            return self._dd_multiplier(daily_drawdown_pct)
        return 1.0, "default"

    def _in_funding_boundary(self, now_ms: int) -> bool:
        """Check if current time is within funding settlement boundary window."""
        cfg = self.config.funding_boundary
        if not cfg.enabled or now_ms <= 0:
            return False
        dt = datetime.datetime.fromtimestamp(now_ms / 1000.0, tz=datetime.timezone.utc)
        secs_into_hour = dt.minute * 60 + dt.second
        # Pre-boundary: last N seconds of the hour
        if secs_into_hour >= (3600 - cfg.pre_window_s):
            return True
        # Post-boundary: first N seconds of the hour
        if secs_into_hour < cfg.post_window_s:
            return True
        return False

    def _get_regime(self, now_ms: int) -> Tuple[str, RegimeOverride]:
        """Return (regime_name, overrides) for current time.

        Supports 4 regimes: OPEN, CLOSE, WEEKEND, REOPEN_WINDOW.
        Falls back to simple in/out session when no regimes dict is configured.
        """
        sr = self.config.session_regime
        if not sr.enabled or now_ms <= 0:
            return "OPEN", RegimeOverride()

        dt = datetime.datetime.fromtimestamp(now_ms / 1000.0, tz=datetime.timezone.utc)

        # Weekend check
        if dt.weekday() in sr.weekend_days:
            if sr.regimes and "WEEKEND" in sr.regimes:
                return "WEEKEND", sr.regimes["WEEKEND"]
            return "WEEKEND", RegimeOverride(spread_mult=sr.off_session_spread_mult)

        # Reopen window: first N minutes after weekend ends
        if sr.weekend_days and dt.weekday() == (max(sr.weekend_days) + 1) % 7:
            minutes_since_midnight = dt.hour * 60 + dt.minute
            if minutes_since_midnight < sr.reopen_window_minutes:
                if sr.regimes and "REOPEN_WINDOW" in sr.regimes:
                    return "REOPEN_WINDOW", sr.regimes["REOPEN_WINDOW"]
                return "REOPEN_WINDOW", RegimeOverride(spread_mult=2.0, size_mult=0.5)

        # In-session check
        current_minutes = dt.hour * 60 + dt.minute
        start_h, start_m = (int(x) for x in sr.in_session_start_utc.split(":"))
        end_h, end_m = (int(x) for x in sr.in_session_end_utc.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        if start_minutes <= current_minutes < end_minutes:
            if sr.regimes and "OPEN" in sr.regimes:
                return "OPEN", sr.regimes["OPEN"]
            return "OPEN", RegimeOverride()

        # Off-session
        if sr.regimes and "CLOSE" in sr.regimes:
            return "CLOSE", sr.regimes["CLOSE"]
        return "CLOSE", RegimeOverride(spread_mult=sr.off_session_spread_mult)

    def _get_session_mult(self, now_ms: int) -> float:
        """Backwards-compat wrapper: return just the spread multiplier."""
        _, regime = self._get_regime(now_ms)
        return regime.spread_mult

    def tick(
        self,
        mid: float,
        bid: float,
        ask: float,
        inventory: float = 0.0,
        daily_drawdown_pct: float = 0.0,
        reduce_only: bool = False,
        timestamp_ms: int = 0,
        external_ref: float = 0.0,
        l2_book: Optional[L2Book] = None,
        oracle_timestamp_ms: int = 0,
        now_ms: int = 0,
        open_interest: float = 0.0,
    ) -> QuoteResult:
        """Run one tick of the quoting engine.

        Args:
            mid: oracle mid price (snapshot.mid_price).
            bid: best bid (snapshot.bid).
            ask: best ask (snapshot.ask).
            inventory: signed net position (positive = long).
            daily_drawdown_pct: current drawdown as % of TVL (0-100).
            reduce_only: True if risk manager has flagged reduce-only.
            timestamp_ms: current timestamp for event schedule.
            external_ref: external reference price (0 = use oracle).
            l2_book: L2 order book snapshot for microprice calculation.
            oracle_timestamp_ms: timestamp of last oracle update.
            now_ms: current wall-clock time in ms (for oracle freshness).
            open_interest: current market open interest for liquidation detection.

        Returns:
            QuoteResult with computed levels and metadata.
        """
        self._tick_count += 1

        # --- Oracle freshness check (Phase 2) ---
        oracle_zone = "fresh"
        oracle_age_ms = 0
        oracle_spread_mult = 1.0

        if self._oracle_monitor and self._oracle_monitor.enabled:
            status = self._oracle_monitor.check(oracle_timestamp_ms, now_ms)
            oracle_zone = status.zone
            oracle_age_ms = status.age_ms
            oracle_spread_mult = status.spread_mult

            if status.halt:
                return QuoteResult(
                    fv_raw=mid, fv_skewed=mid,
                    half_spread=0.0,
                    sigma_price=0.0, sigma_log=0.0,
                    m_vol=1.0, vol_bin="default",
                    m_dd=1.0, dd_bin="default",
                    halted=True, reduce_only=True,
                    meta={
                        "oracle_zone": oracle_zone,
                        "oracle_age_ms": oracle_age_ms,
                        "halt_reason": "oracle_kill",
                    },
                )

            if status.reduce_only:
                reduce_only = True

        # --- L2 microprice (Phase 2) ---
        microprice_override: Optional[float] = None
        microprice_source = "bid_ask_proxy"
        if self._microprice_calc and l2_book is not None:
            mp = self._microprice_calc.compute(l2_book)
            if mp > 0:
                microprice_override = mp
                microprice_source = "l2_depth"

        # --- Funding feed as external_ref (Phase 2) ---
        funding_source = "none"
        if self._funding_feed:
            feed_result = self._funding_feed.latest()
            if feed_result is not None and not feed_result.stale and feed_result.value != 0.0:
                external_ref = feed_result.value
                funding_source = feed_result.source

        # --- Funding dampening (per-market scaling) ---
        if self.config.funding_dampening > 0 and external_ref != 0.0:
            external_ref *= (1.0 / self.config.funding_dampening)

        # --- Vol estimation ---
        sigma_price, sigma_log = self._vol.update(mid)

        # --- Risk regime classification ---
        m_vol, vol_bin = self._classify_vol(sigma_log)
        m_dd, dd_bin = self._get_dd_mult(daily_drawdown_pct)

        # --- Halt check (DD red) ---
        if math.isinf(m_dd):
            return QuoteResult(
                fv_raw=mid, fv_skewed=mid,
                half_spread=0.0,
                sigma_price=sigma_price, sigma_log=sigma_log,
                m_vol=m_vol, vol_bin=vol_bin,
                m_dd=m_dd, dd_bin=dd_bin,
                halted=True, reduce_only=True,
            )

        # --- Orange DD triggers reduce-only ---
        if m_dd >= 2.0:
            reduce_only = True

        # --- Inventory cap checks (G5) ---
        inv_state = self._skewer.inventory_state(inventory)
        if inv_state == "hard_breach":
            return QuoteResult(
                fv_raw=mid, fv_skewed=mid,
                half_spread=0.0,
                sigma_price=sigma_price, sigma_log=sigma_log,
                m_vol=m_vol, vol_bin=vol_bin,
                m_dd=m_dd, dd_bin=dd_bin,
                halted=True, reduce_only=True,
                meta={"inv_state": "hard_breach"},
            )
        if inv_state == "soft_breach":
            reduce_only = True

        # --- Regime determination (G3) — needed before FV for oracle weight override ---
        regime_name, regime = self._get_regime(now_ms)

        # --- Update event schedule mid (Phase 2) ---
        if hasattr(self._events, "set_mid"):
            self._events.set_mid(mid)

        # --- Fair value ---
        # inventory_term=0: inventory adjustment is handled post-FV by
        # InventorySkewer.price_skew() to avoid double-counting.  The FV blend
        # only combines price signals (oracle, external, microprice).
        # w_inventory should be 0 in market configs.
        fv_raw = self._fv_calc.compute(
            oracle_price=mid,
            bid=bid,
            ask=ask,
            external_ref=external_ref,
            inventory_term=0.0,
            microprice_override=microprice_override,
            oracle_weight_override=regime.w_oracle_override,
        )

        # --- FV band clamping (G1) ---
        fv_band_cfg = self.config.fv_band
        if fv_band_cfg.enabled and mid > 0:
            disagree_abs = abs(mid - external_ref) if external_ref > 0 and external_ref != mid else 0.0
            band = max(
                fv_band_cfg.band_min_bps * mid / 10_000,
                fv_band_cfg.k_sigma * sigma_price,
                fv_band_cfg.k_disagree * disagree_abs,
            )
            fv_raw = max(mid - band, min(fv_raw, mid + band))

        # --- Funding boundary (G7) ---
        in_funding_boundary = self._in_funding_boundary(now_ms)
        if in_funding_boundary and self.config.funding_boundary.pin_fv_to_oracle:
            fv_raw = mid

        # --- Inventory skew on FV ---
        fv_skewed = self._skewer.price_skew(fv_raw, inventory, sigma_price)

        # --- Toxicity and event components ---
        tox_response = None
        if hasattr(self._tox, 'score_full'):
            tox_response = self._tox.score_full(mid, bid, ask, timestamp_ms)
            h_tox = tox_response.h_tox
        else:
            h_tox = self._tox.score(mid, bid, ask, timestamp_ms)
        h_event = self._events.h_event(self.config.instrument, timestamp_ms)

        # --- Half-spread ---
        half_spread = self._spread_calc.compute(
            mid=mid,
            sigma_price=sigma_price,
            m_vol=m_vol,
            m_dd=m_dd,
            h_tox=h_tox,
            h_event=h_event,
        )

        # Apply oracle spread multiplier (Phase 2)
        half_spread *= oracle_spread_mult

        # --- Session / regime spread multiplier (regime already computed above) ---
        session_mult = regime.spread_mult
        half_spread *= session_mult
        if regime.reduce_only:
            reduce_only = True

        # --- Liquidation flow detection ---
        liq_cfg = self.config.liquidation_detector
        liq_triggered = False
        liq_mid_burst = False
        liq_escalated = False

        if liq_cfg.enabled:
            # OI-drop trigger
            if open_interest > 0:
                if self._prev_oi > 0:
                    oi_change_pct = (open_interest - self._prev_oi) / self._prev_oi * 100.0
                    if oi_change_pct <= -liq_cfg.oi_drop_threshold_pct:
                        self._liq_cooldown_remaining = liq_cfg.cooldown_ticks
                        self._liq_total_cooldown = liq_cfg.cooldown_ticks
                        liq_triggered = True
                self._prev_oi = open_interest

            # Mid-burst trigger (G6)
            self._mid_history_short.append(mid)
            if liq_cfg.mid_burst_bps > 0 and len(self._mid_history_short) >= liq_cfg.mid_burst_window:
                burst_range = max(self._mid_history_short) - min(self._mid_history_short)
                burst_threshold = liq_cfg.mid_burst_bps * mid / 10_000
                if burst_range > burst_threshold:
                    if self._liq_cooldown_remaining <= 0:
                        self._liq_cooldown_remaining = liq_cfg.cooldown_ticks
                        self._liq_total_cooldown = liq_cfg.cooldown_ticks
                    liq_mid_burst = True

        if self._liq_cooldown_remaining > 0:
            half_spread *= liq_cfg.spread_mult
            self._liq_cooldown_remaining -= 1

            # Time-stop escalation (G6): if cooldown has been running too long
            if liq_cfg.escalation_ticks > 0:
                ticks_in_cooldown = self._liq_total_cooldown - self._liq_cooldown_remaining
                if ticks_in_cooldown >= liq_cfg.escalation_ticks:
                    reduce_only = True
                    liq_escalated = True

        # --- Disagreement mode (G2) ---
        disagree_active = False
        disagree_cfg = self.config.disagreement
        if disagree_cfg.enabled and external_ref > 0 and external_ref != mid and mid > 0:
            disagree_bps = abs(mid - external_ref) / mid * 10_000
            if disagree_bps > disagree_cfg.threshold_bps:
                half_spread *= disagree_cfg.spread_mult
                disagree_active = True

        # --- Size skew multipliers ---
        bid_mult, ask_mult = self._skewer.size_skew(1.0, 1.0, inventory)

        # Apply tiered toxicity size/cancel adjustments (G4)
        if tox_response is not None:
            bid_mult *= tox_response.size_mult
            ask_mult *= tox_response.size_mult
            if tox_response.cancel_bids:
                bid_mult = 0.0
            if tox_response.cancel_asks:
                ask_mult = 0.0

        # Apply disagreement size reduction (G2)
        if disagree_active:
            bid_mult *= disagree_cfg.size_mult
            ask_mult *= disagree_cfg.size_mult

        # Apply funding boundary size reduction (G7)
        if in_funding_boundary:
            bid_mult *= self.config.funding_boundary.size_mult
            ask_mult *= self.config.funding_boundary.size_mult

        # Apply liquidation size reduction during cooldown
        liq_in_cooldown = self._liq_cooldown_remaining > 0 or liq_triggered
        if liq_in_cooldown:
            bid_mult *= liq_cfg.size_mult
            ask_mult *= liq_cfg.size_mult

        # Apply regime size multiplier (G3)
        bid_mult *= regime.size_mult
        ask_mult *= regime.size_mult

        # --- Ladder ---
        levels = self._ladder.build(
            fv=fv_skewed,
            half_spread=half_spread,
            mid=mid,
            bid_size_mult=bid_mult,
            ask_size_mult=ask_mult,
            num_levels_override=regime.num_levels,
        )

        # Tiered ladder during liq: pull ToB, keep deep catchers (G6)
        if liq_in_cooldown and liq_cfg.liq_catcher_levels > 0 and len(levels) > liq_cfg.liq_catcher_levels:
            # Zero out ToB levels (first N levels)
            pull_count = len(levels) - liq_cfg.liq_catcher_levels
            for i in range(pull_count):
                levels[i] = type(levels[i])(
                    level=levels[i].level,
                    bid_price=levels[i].bid_price,
                    bid_size=0.0,
                    ask_price=levels[i].ask_price,
                    ask_size=0.0,
                )
            # Scale catcher levels
            for i in range(pull_count, len(levels)):
                levels[i] = type(levels[i])(
                    level=levels[i].level,
                    bid_price=levels[i].bid_price,
                    bid_size=round(levels[i].bid_size * liq_cfg.liq_catcher_size_mult, 6),
                    ask_price=levels[i].ask_price,
                    ask_size=round(levels[i].ask_size * liq_cfg.liq_catcher_size_mult, 6),
                )

        # --- Micro-clip check (G5) ---
        micro_clip = self._skewer.micro_clip_order(inventory, self._tick_count)

        # --- KPI tracking ---
        if self._metrics:
            self._metrics.on_tick(levels, False, mid, bid, ask)

        return QuoteResult(
            fv_raw=fv_raw,
            fv_skewed=fv_skewed,
            half_spread=half_spread,
            sigma_price=sigma_price,
            sigma_log=sigma_log,
            m_vol=m_vol,
            vol_bin=vol_bin,
            m_dd=m_dd,
            dd_bin=dd_bin,
            levels=levels,
            halted=False,
            reduce_only=reduce_only,
            meta={
                "h_tox": h_tox,
                "h_event": h_event,
                "vol_ready": self._vol.ready,
                "oracle_zone": oracle_zone,
                "oracle_age_ms": oracle_age_ms,
                "microprice_source": microprice_source,
                "funding_source": funding_source,
                "regime_name": regime_name,
                "session_mult": session_mult,
                "liq_triggered": liq_triggered,
                "liq_mid_burst": liq_mid_burst,
                "liq_escalated": liq_escalated,
                "liq_cooldown_remaining": self._liq_cooldown_remaining,
                "disagree_active": disagree_active,
                "in_funding_boundary": in_funding_boundary,
                "tox_tier": tox_response.tier if tox_response else "normal",
                "tox_cancel_bids": tox_response.cancel_bids if tox_response else False,
                "tox_cancel_asks": tox_response.cancel_asks if tox_response else False,
                "inv_state": inv_state,
                "micro_clip": micro_clip,
            },
        )
