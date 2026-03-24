"""Tests for Tiered Toxicity + One-Sided Mode (G4)."""
from quoting_engine.toxicity import MarkoutToxicityScorer, ToxicityResponse


def _make_scorer(**kwargs) -> MarkoutToxicityScorer:
    defaults = dict(
        lookback=2,
        ema_alpha=0.9,  # fast EMA for test responsiveness
        scale_bps=2.0,
        t1_threshold=0.3,
        t2_threshold=0.6,
        t1_spread_mult=1.5,
        t1_size_mult=0.7,
        t2_spread_mult=2.0,
        t2_size_mult=0.3,
        cooldown_ticks=3,
    )
    defaults.update(kwargs)
    return MarkoutToxicityScorer(**defaults)


def test_tox_normal_passthrough():
    """No fills -> toxicity=0 -> normal tier."""
    scorer = _make_scorer()
    resp = scorer.score_full(mid=100.0, bid=99.9, ask=100.1, timestamp_ms=1000)
    assert resp.tier == "normal"
    assert resp.size_mult == 1.0
    assert resp.cancel_bids is False
    assert resp.cancel_asks is False


def test_tox_t1_widens_spread():
    """Moderate toxicity -> T1 tier with spread mult and size reduction."""
    scorer = _make_scorer(t1_threshold=0.03, t2_threshold=0.8, ema_alpha=1.0)
    # Record buy fill at tick 0
    scorer.record_fill(fill_price=100.0, side="buy", tick_count=0)
    # Advance 1 tick with mid going down
    scorer.score(mid=96.0, bid=95.9, ask=96.1, timestamp_ms=1000)  # tick 1
    # Tick 2: fill matures (age=2 >= lookback=2), markout = (95-100)/100 = -0.05
    # score_full calls score() which advances to tick 2 and resolves the fill
    resp = scorer.score_full(mid=95.0, bid=94.9, ask=95.1, timestamp_ms=2000)

    # Toxicity = max(0, -(-0.05)) = 0.05 > t1_threshold=0.03 -> T1
    assert resp.tier == "T1"
    assert resp.size_mult == 0.7
    assert resp.cancel_bids is False
    assert resp.cancel_asks is False


def test_tox_t2_cancels_side():
    """High toxicity -> T2 tier, cancels the adverse side."""
    scorer = _make_scorer(t1_threshold=0.05, t2_threshold=0.1, ema_alpha=1.0)

    # Multiple toxic buy fills that mature with bad mid
    for i in range(5):
        scorer.record_fill(fill_price=100.0, side="buy", tick_count=i)

    # Advance past lookback with mid at 80 -> markout = (80-100)/100 = -0.2
    for i in range(5, 15):
        scorer.score(mid=80.0, bid=79.9, ask=80.1, timestamp_ms=i * 1000)

    resp = scorer.score_full(mid=80.0, bid=79.9, ask=80.1, timestamp_ms=15000)

    assert resp.tier == "T2"
    assert resp.size_mult == 0.3
    # Buy side is adverse -> cancel bids
    assert resp.cancel_bids is True
    assert resp.cancel_asks is False


def test_tox_adverse_side_tracking():
    """Sell fills with negative markouts -> sell side becomes adverse."""
    scorer = _make_scorer(t1_threshold=0.05, t2_threshold=0.1, ema_alpha=1.0)

    # Sell fills that go wrong: sell at 100, mid goes to 120
    for i in range(5):
        scorer.record_fill(fill_price=100.0, side="sell", tick_count=i)

    # Advance past lookback with mid at 120
    for i in range(5, 15):
        scorer.score(mid=120.0, bid=119.9, ask=120.1, timestamp_ms=i * 1000)

    resp = scorer.score_full(mid=120.0, bid=119.9, ask=120.1, timestamp_ms=15000)

    assert resp.tier == "T2"
    # Sell side is adverse -> cancel asks
    assert resp.cancel_asks is True
    assert resp.cancel_bids is False


def test_tox_cooldown():
    """After toxicity drops, tier persists during cooldown."""
    scorer = _make_scorer(
        t1_threshold=0.03, t2_threshold=0.8,
        cooldown_ticks=3, ema_alpha=1.0, lookback=1,
    )

    # One toxic fill: buy at 100
    scorer.record_fill(fill_price=100.0, side="buy", tick_count=0)
    # Tick 1: fill matures with bad mid -> markout = (90-100)/100 = -0.1, tox = 0.1 > 0.03
    resp1 = scorer.score_full(mid=90.0, bid=89.9, ask=90.1, timestamp_ms=1000)
    assert resp1.tier == "T1"
    assert resp1.cooldown_remaining == 3

    # Now benign fill to reset EMA: buy at 100, mature at 110 -> markout = +0.1
    scorer.record_fill(fill_price=100.0, side="buy", tick_count=scorer._tick_count)
    scorer.score(mid=110.0, bid=109.9, ask=110.1, timestamp_ms=2000)
    # At this point EMA is positive (alpha=1.0 -> last value), so toxicity=0
    # But cooldown should keep us in T1
    resp2 = scorer.score_full(mid=110.0, bid=109.9, ask=110.1, timestamp_ms=3000)
    # Should still be T1 due to cooldown (remaining >= 1)
    assert resp2.tier == "T1"

    # Exhaust cooldown
    for t in range(4000, 8000, 1000):
        resp = scorer.score_full(mid=110.0, bid=109.9, ask=110.1, timestamp_ms=t)
    # Eventually should return to normal
    assert resp.tier == "normal"
