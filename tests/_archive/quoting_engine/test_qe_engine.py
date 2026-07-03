"""Tests for QuotingEngine orchestrator."""
import math
from quoting_engine.config import MarketConfig, SpreadParams, LadderParams, SkewParams
from quoting_engine.engine import QuotingEngine


def _make_engine(**kwargs) -> QuotingEngine:
    cfg = MarketConfig(
        tick_size=0.01,
        spread=SpreadParams(min_spread_bps=1.0, max_spread_bps=200.0),
        ladder=LadderParams(num_levels=3, delta_bps=1.0, s0=1.0, lam=0.5),
        skew=SkewParams(k_inv=0.5, inv_limit=10.0, mode="both"),
        **kwargs,
    )
    return QuotingEngine(cfg)


def _warmup(engine: QuotingEngine, mid: float = 100.0, n: int = 5):
    """Feed enough ticks for vol estimator to be ready."""
    for i in range(n):
        engine.tick(mid=mid + i * 0.01, bid=mid - 0.05, ask=mid + 0.05)


def test_engine_basic_tick():
    e = _make_engine()
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05)
    assert not r.halted
    assert len(r.levels) == 3
    assert r.fv_raw > 0
    assert r.half_spread > 0


def test_engine_halt_on_dd_red():
    e = _make_engine()
    e.set_risk_classifiers(
        vol_bin_classify=lambda s: (1.0, "low"),
        dd_multiplier=lambda d: (float("inf"), "red") if d >= 2.5 else (1.0, "green"),
    )
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05, daily_drawdown_pct=3.0)
    assert r.halted
    assert r.levels == []


def test_engine_reduce_only_on_dd_orange():
    e = _make_engine()
    e.set_risk_classifiers(
        vol_bin_classify=lambda s: (1.0, "low"),
        dd_multiplier=lambda d: (2.0, "orange") if d >= 1.5 else (1.0, "green"),
    )
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05, daily_drawdown_pct=2.0)
    assert r.reduce_only


def test_engine_vol_warmup():
    e = _make_engine()
    # First tick should still produce levels (fallback vol)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05)
    assert len(r.levels) == 3
    assert not r.meta.get("vol_ready", True)  # not ready yet


def test_engine_without_risk_classifiers():
    e = _make_engine()
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05)
    assert r.vol_bin == "default"
    assert r.dd_bin == "default"
    assert r.m_vol == 1.0
    assert r.m_dd == 1.0


def test_engine_with_mock_classifiers():
    e = _make_engine()
    e.set_risk_classifiers(
        vol_bin_classify=lambda s: (2.5, "high"),
        dd_multiplier=lambda d: (1.5, "yellow"),
    )
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05)
    assert r.m_vol == 2.5
    assert r.vol_bin == "high"
    assert r.m_dd == 1.5
    assert r.dd_bin == "yellow"


def test_engine_inventory_affects_fv():
    e = _make_engine()
    _warmup(e)
    r_flat = e.tick(mid=100.0, bid=99.95, ask=100.05, inventory=0.0)

    e2 = _make_engine()
    _warmup(e2)
    r_long = e2.tick(mid=100.0, bid=99.95, ask=100.05, inventory=5.0)

    assert r_long.fv_skewed < r_flat.fv_skewed  # long -> lower FV


def test_engine_statefulness():
    e = _make_engine()
    # Volatile prices should change vol estimate
    prices = [100.0, 101.0, 99.0, 102.0, 98.0]
    results = []
    for p in prices:
        results.append(e.tick(mid=p, bid=p - 0.5, ask=p + 0.5))
    # Later ticks should have higher vol
    assert results[-1].sigma_price > results[0].sigma_price


# === Phase 2: Feed integration tests ===

from quoting_engine.feeds.oracle_monitor import OracleFreshnessMonitor, OracleMonitorConfig
from quoting_engine.feeds.microprice import L2Book, L2MicropriceCalculator
from quoting_engine.feeds.funding_rate import CrossVenueFundingRate, ConstantFundingRate


def test_engine_oracle_monitor_halt():
    """Stale oracle in kill zone should halt the engine."""
    monitor = OracleFreshnessMonitor(OracleMonitorConfig(kill_ms=60000))
    e = _make_engine()
    e2 = QuotingEngine(e.config, oracle_monitor=monitor)
    _warmup(e2)
    r = e2.tick(
        mid=100.0, bid=99.95, ask=100.05,
        oracle_timestamp_ms=1000, now_ms=100000,  # 99s old -> kill
    )
    assert r.halted
    assert r.meta.get("oracle_zone") == "kill"


def test_engine_oracle_warning_widens_spread():
    """Warning zone should widen spread by 1.5x."""
    monitor = OracleFreshnessMonitor(OracleMonitorConfig(warning_ms=5000, stale_ms=15000))
    cfg = MarketConfig(
        tick_size=0.01,
        spread=SpreadParams(min_spread_bps=1.0, max_spread_bps=200.0),
        ladder=LadderParams(num_levels=3, delta_bps=1.0, s0=1.0, lam=0.5),
        skew=SkewParams(k_inv=0.5, inv_limit=10.0, mode="both"),
    )
    # Engine without monitor
    e_normal = QuotingEngine(cfg)
    _warmup(e_normal)
    r_normal = e_normal.tick(mid=100.0, bid=99.95, ask=100.05)

    # Engine with monitor in warning zone
    e_warn = QuotingEngine(cfg, oracle_monitor=monitor)
    _warmup(e_warn)
    r_warn = e_warn.tick(
        mid=100.0, bid=99.95, ask=100.05,
        oracle_timestamp_ms=1000, now_ms=8000,  # 7s old -> warning
    )
    assert r_warn.half_spread > r_normal.half_spread
    assert r_warn.meta.get("oracle_zone") == "warning"


def test_engine_microprice_override():
    """L2 microprice should be used when l2_book is provided."""
    mp_calc = L2MicropriceCalculator(depth_levels=1)
    cfg = MarketConfig(
        tick_size=0.01,
        spread=SpreadParams(min_spread_bps=1.0, max_spread_bps=200.0),
        ladder=LadderParams(num_levels=1, delta_bps=1.0, s0=1.0, lam=0.5),
        skew=SkewParams(k_inv=0.5, inv_limit=10.0, mode="both"),
    )
    e = QuotingEngine(cfg, microprice_calc=mp_calc)
    _warmup(e)

    book = L2Book(
        bids=[(99.0, 30.0)],
        asks=[(101.0, 10.0)],
    )
    r = e.tick(mid=100.0, bid=99.0, ask=101.0, l2_book=book)
    assert r.meta.get("microprice_source") == "l2_depth"


def test_engine_funding_feed_as_external_ref():
    """Funding feed should be used as external_ref in FV calc."""
    src = ConstantFundingRate(rate=0.05)
    feed = CrossVenueFundingRate(sources=[src])
    feed.refresh()  # populate latest

    cfg = MarketConfig(
        tick_size=0.01,
        spread=SpreadParams(min_spread_bps=1.0, max_spread_bps=200.0),
        ladder=LadderParams(num_levels=1, delta_bps=1.0, s0=1.0, lam=0.5),
        skew=SkewParams(k_inv=0.5, inv_limit=10.0, mode="both"),
    )
    e = QuotingEngine(cfg, funding_feed=feed)
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05)
    assert r.meta.get("funding_source") != "none"


def test_engine_backward_compat_no_feeds():
    """Engine should work exactly as Phase 1 when no feeds provided."""
    e = _make_engine()
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05)
    assert not r.halted
    assert len(r.levels) == 3
    assert r.meta.get("oracle_zone") == "fresh"
    assert r.meta.get("microprice_source") == "bid_ask_proxy"
    assert r.meta.get("funding_source") == "none"


# === Phase 3: Per-market playbook features ===

from quoting_engine.config import FairValueWeights


def test_engine_funding_dampening():
    """Funding dampening should scale external_ref by 1/dampening."""
    # With dampening=120, a funding rate of 0.12 becomes 0.001 in the FV blend.
    # FV = w_oracle*100 + w_external*external_ref.
    # Without dampening: external_ref=0.12, FV_ext_component = 0.5*0.12 = 0.06
    # With dampening=120: external_ref=0.001, FV_ext_component = 0.5*0.001 = 0.0005
    src = ConstantFundingRate(rate=0.12)
    feed_no = CrossVenueFundingRate(sources=[src])
    feed_no.refresh()
    feed_yes = CrossVenueFundingRate(sources=[ConstantFundingRate(rate=0.12)])
    feed_yes.refresh()

    cfg_no_damp = MarketConfig(
        tick_size=0.01,
        spread=SpreadParams(min_spread_bps=1.0, max_spread_bps=200.0),
        ladder=LadderParams(num_levels=1, delta_bps=1.0, s0=1.0, lam=0.5),
        skew=SkewParams(k_inv=0.0, inv_limit=10.0, mode="both"),
        fv_weights=FairValueWeights(w_oracle=0.5, w_external=0.5, w_microprice=0.0, w_inventory=0.0),
        funding_dampening=0,
    )
    cfg_damp = MarketConfig(
        tick_size=0.01,
        spread=SpreadParams(min_spread_bps=1.0, max_spread_bps=200.0),
        ladder=LadderParams(num_levels=1, delta_bps=1.0, s0=1.0, lam=0.5),
        skew=SkewParams(k_inv=0.0, inv_limit=10.0, mode="both"),
        fv_weights=FairValueWeights(w_oracle=0.5, w_external=0.5, w_microprice=0.0, w_inventory=0.0),
        funding_dampening=120,
    )

    e_no = QuotingEngine(cfg_no_damp, funding_feed=feed_no)
    e_yes = QuotingEngine(cfg_damp, funding_feed=feed_yes)
    _warmup(e_no); _warmup(e_yes)

    r_no = e_no.tick(mid=100.0, bid=99.95, ask=100.05)
    r_yes = e_yes.tick(mid=100.0, bid=99.95, ask=100.05)
    # FV = 0.5*100 + 0.5*external_ref. Pure oracle FV = 50.0 (no external).
    # Dampened should have smaller external_ref contribution -> closer to 50.0
    oracle_only_fv = 0.5 * 100.0  # baseline with external_ref=0
    assert abs(r_yes.fv_raw - oracle_only_fv) < abs(r_no.fv_raw - oracle_only_fv)
    # The difference should be significant (120x dampening)
    assert abs(r_no.fv_raw - r_yes.fv_raw) > 0.05


from quoting_engine.config import SessionRegimeConfig
import datetime


def _make_session_engine(enabled: bool, mult: float = 3.0) -> QuotingEngine:
    cfg = MarketConfig(
        tick_size=0.01,
        spread=SpreadParams(min_spread_bps=1.0, max_spread_bps=500.0),
        ladder=LadderParams(num_levels=1, delta_bps=1.0, s0=1.0, lam=0.5),
        skew=SkewParams(k_inv=0.0, inv_limit=10.0, mode="both"),
        session_regime=SessionRegimeConfig(
            enabled=enabled,
            in_session_start_utc="14:30",
            in_session_end_utc="21:00",
            off_session_spread_mult=mult,
        ),
    )
    return QuotingEngine(cfg)


def _utc_hm_to_ms(hour: int, minute: int) -> int:
    """Convert a UTC hour:minute on 2026-03-02 (Monday) to epoch ms."""
    dt = datetime.datetime(2026, 3, 2, hour, minute, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def test_engine_session_regime_in_session():
    """16:00 UTC is in-session -- mult=1.0."""
    e = _make_session_engine(enabled=True)
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05, now_ms=_utc_hm_to_ms(16, 0))
    assert r.meta.get("session_mult") == 1.0


def test_engine_session_regime_off_session():
    """08:00 UTC is off-session -- spread should be 3x wider."""
    e_on = _make_session_engine(enabled=True, mult=3.0)
    e_off = _make_session_engine(enabled=False)
    _warmup(e_on); _warmup(e_off)
    now_ms = _utc_hm_to_ms(8, 0)  # 08:00 UTC
    r_on = e_on.tick(mid=100.0, bid=99.95, ask=100.05, now_ms=now_ms)
    r_off = e_off.tick(mid=100.0, bid=99.95, ask=100.05, now_ms=now_ms)
    assert r_on.meta.get("session_mult") == 3.0
    assert r_on.half_spread > r_off.half_spread * 2.5  # roughly 3x


def test_engine_session_regime_disabled():
    """Disabled session regime -> mult=1.0 regardless of time."""
    e = _make_session_engine(enabled=False)
    _warmup(e)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05, now_ms=_utc_hm_to_ms(3, 0))
    assert r.meta.get("session_mult") == 1.0


# === Liquidation flow detection tests ===

from quoting_engine.config import LiquidationDetectorConfig


def _make_liq_engine(enabled: bool = True) -> QuotingEngine:
    cfg = MarketConfig(
        tick_size=0.01,
        spread=SpreadParams(min_spread_bps=1.0, max_spread_bps=500.0),
        ladder=LadderParams(num_levels=1, delta_bps=1.0, s0=1.0, lam=0.0),
        skew=SkewParams(k_inv=0.0, inv_limit=10.0, mode="both"),
        liquidation_detector=LiquidationDetectorConfig(
            enabled=enabled,
            oi_drop_threshold_pct=5.0,
            spread_mult=2.0,
            size_mult=0.5,
            cooldown_ticks=3,
        ),
    )
    return QuotingEngine(cfg)


def test_engine_liq_detection_triggers():
    """A >5% OI drop should widen spread and reduce size."""
    e = _make_liq_engine(enabled=True)
    _warmup(e)

    # First tick establishes baseline OI
    r1 = e.tick(mid=100.0, bid=99.95, ask=100.05, open_interest=100000.0)
    h_normal = r1.half_spread
    s_normal = r1.levels[0].bid_size

    # 10% OI drop -- triggers liquidation defense
    r2 = e.tick(mid=100.0, bid=99.95, ask=100.05, open_interest=90000.0)
    assert r2.meta.get("liq_triggered") is True
    assert r2.half_spread > h_normal * 1.5  # should be ~2x
    assert r2.levels[0].bid_size < s_normal  # size reduced


def test_engine_liq_cooldown_expires():
    """After cooldown_ticks, spread should return to normal."""
    e = _make_liq_engine(enabled=True)
    _warmup(e)

    # Establish OI then trigger
    e.tick(mid=100.0, bid=99.95, ask=100.05, open_interest=100000.0)
    e.tick(mid=100.0, bid=99.95, ask=100.05, open_interest=90000.0)  # trigger

    # Burn through cooldown (3 ticks) with stable OI
    for _ in range(4):
        r = e.tick(mid=100.0, bid=99.95, ask=100.05, open_interest=90000.0)

    # After cooldown, liq_cooldown_remaining should be 0
    assert r.meta.get("liq_cooldown_remaining") == 0


def test_engine_liq_disabled():
    """Disabled liquidation detector should not track OI."""
    e = _make_liq_engine(enabled=False)
    _warmup(e)
    e.tick(mid=100.0, bid=99.95, ask=100.05, open_interest=100000.0)
    r = e.tick(mid=100.0, bid=99.95, ask=100.05, open_interest=50000.0)  # 50% drop
    assert r.meta.get("liq_triggered") is False
