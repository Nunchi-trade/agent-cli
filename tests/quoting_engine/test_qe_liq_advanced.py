"""Tests for Advanced Liquidation Detection (G6)."""
from quoting_engine.config import MarketConfig, LiquidationDetectorConfig, LadderParams
from quoting_engine.engine import QuotingEngine


def _make_engine(liq_cfg: LiquidationDetectorConfig, num_levels: int = 4) -> QuotingEngine:
    cfg = MarketConfig(
        liquidation_detector=liq_cfg,
        ladder=LadderParams(num_levels=num_levels, delta_bps=1.5, s0=1.0, lam=0.3),
    )
    engine = QuotingEngine(cfg)
    for _ in range(35):
        engine.tick(mid=100.0, bid=99.9, ask=100.1)
    return engine


def test_liq_mid_burst_triggers():
    """Large mid move within window -> liquidation cooldown triggered."""
    engine = _make_engine(LiquidationDetectorConfig(
        enabled=True, mid_burst_bps=20.0, mid_burst_window=3,
        spread_mult=2.0, size_mult=0.5, cooldown_ticks=5,
    ))
    # Stable mid
    r1 = engine.tick(mid=100.0, bid=99.9, ask=100.1)
    assert r1.meta["liq_mid_burst"] is False

    # Sudden mid spike: 100 -> 100.5 -> 101.0 within 3 ticks
    # Burst = 1.0 / 101.0 * 10000 = ~99 bps > 20 bps threshold
    engine.tick(mid=100.5, bid=100.4, ask=100.6)
    r3 = engine.tick(mid=101.0, bid=100.9, ask=101.1)
    assert r3.meta["liq_mid_burst"] is True
    # Spread should be wider due to cooldown
    assert r3.half_spread > r1.half_spread


def test_liq_catcher_levels_preserved():
    """During liq cooldown, ToB levels are pulled but deep catchers remain."""
    engine = _make_engine(LiquidationDetectorConfig(
        enabled=True, oi_drop_threshold_pct=5.0,
        spread_mult=2.0, size_mult=0.5, cooldown_ticks=5,
        liq_catcher_levels=2,  # keep last 2 levels
        liq_catcher_size_mult=0.3,
    ), num_levels=4)

    # First tick: set baseline OI
    engine.tick(mid=100.0, bid=99.9, ask=100.1, open_interest=1000.0)

    # Second tick: OI drops 10% -> trigger
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, open_interest=900.0)
    assert r.meta["liq_triggered"] is True

    # Should have 4 levels but first 2 (ToB) have zero size, last 2 are catchers
    assert len(r.levels) == 4
    # ToB levels should be zeroed
    assert r.levels[0].bid_size == 0.0
    assert r.levels[0].ask_size == 0.0
    assert r.levels[1].bid_size == 0.0
    assert r.levels[1].ask_size == 0.0
    # Catcher levels should have non-zero sizes
    assert r.levels[2].bid_size > 0.0
    assert r.levels[3].bid_size > 0.0


def test_liq_escalation_to_reduce_only():
    """After escalation_ticks in cooldown, engine goes reduce-only."""
    engine = _make_engine(LiquidationDetectorConfig(
        enabled=True, oi_drop_threshold_pct=5.0,
        spread_mult=2.0, size_mult=0.5, cooldown_ticks=10,
        escalation_ticks=3,
    ))

    # Trigger liq
    engine.tick(mid=100.0, bid=99.9, ask=100.1, open_interest=1000.0)
    engine.tick(mid=100.0, bid=99.9, ask=100.1, open_interest=900.0)

    # Tick through cooldown until escalation
    results = []
    for _ in range(5):
        r = engine.tick(mid=100.0, bid=99.9, ask=100.1, open_interest=900.0)
        results.append(r)

    # At some point, escalation should trigger reduce_only
    escalated = any(r.meta.get("liq_escalated", False) for r in results)
    assert escalated, "Should have escalated to reduce-only"
    # The tick where escalation fires should have reduce_only=True
    escalated_results = [r for r in results if r.meta.get("liq_escalated", False)]
    assert any(r.reduce_only for r in escalated_results)


def test_liq_no_mid_burst_when_disabled():
    """Mid burst detection disabled when mid_burst_bps=0."""
    engine = _make_engine(LiquidationDetectorConfig(
        enabled=True, mid_burst_bps=0.0,  # disabled
        spread_mult=2.0, cooldown_ticks=5,
    ))
    engine.tick(mid=100.0, bid=99.9, ask=100.1)
    engine.tick(mid=110.0, bid=109.9, ask=110.1)  # huge move
    r = engine.tick(mid=120.0, bid=119.9, ask=120.1)
    assert r.meta["liq_mid_burst"] is False
