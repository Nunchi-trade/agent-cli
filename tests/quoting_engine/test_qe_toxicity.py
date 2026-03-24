"""Tests for toxicity scorer — markout-based adverse selection detection."""
from quoting_engine.toxicity import StubToxicityScorer, MarkoutToxicityScorer


def test_stub_returns_zero():
    scorer = StubToxicityScorer()
    assert scorer.score(100.0, 99.95, 100.05, 0) == 0.0


def test_initial_toxicity_zero():
    scorer = MarkoutToxicityScorer(lookback=3)
    assert scorer.toxicity == 0.0
    assert scorer.score(100.0, 99.95, 100.05, 0) == 0.0


def test_benign_flow_no_toxicity():
    """When fills are followed by favorable moves, toxicity stays at 0."""
    scorer = MarkoutToxicityScorer(lookback=2, ema_alpha=0.5, scale_bps=2.0)

    # Buy fill at 100, price moves up to 101 after 2 ticks -> positive markout
    scorer.record_fill(100.0, "buy", tick_count=0)
    # Tick 1: not yet matured
    scorer.score(100.5, 100.45, 100.55, 1000)
    # Tick 2: fill matures, markout = (101 - 100) / 100 = 0.01 -> positive
    h_tox = scorer.score(101.0, 100.95, 101.05, 2000)

    assert scorer.ema_markout > 0  # positive = benign
    assert scorer.toxicity == 0.0  # clamped to 0 for positive markout
    assert h_tox == 0.0


def test_adverse_flow_raises_toxicity():
    """When fills are followed by adverse moves, toxicity increases."""
    scorer = MarkoutToxicityScorer(lookback=2, ema_alpha=1.0, scale_bps=2.0)

    # Buy fill at 100, price drops to 99 after 2 ticks -> negative markout
    scorer.record_fill(100.0, "buy", tick_count=0)
    scorer.score(99.5, 99.45, 99.55, 1000)  # tick 1
    h_tox = scorer.score(99.0, 98.95, 99.05, 2000)  # tick 2: matured

    assert scorer.ema_markout < 0  # negative = adverse
    assert scorer.toxicity > 0
    assert h_tox > 0


def test_sell_fill_adverse():
    """Sell fill followed by price moving up = adverse."""
    scorer = MarkoutToxicityScorer(lookback=2, ema_alpha=1.0, scale_bps=2.0)

    # Sell at 100, price moves to 101 -> adverse for seller
    scorer.record_fill(100.0, "sell", tick_count=0)
    scorer.score(100.5, 100.45, 100.55, 1000)
    scorer.score(101.0, 100.95, 101.05, 2000)

    assert scorer.ema_markout < 0
    assert scorer.toxicity > 0


def test_sell_fill_benign():
    """Sell fill followed by price moving down = benign."""
    scorer = MarkoutToxicityScorer(lookback=2, ema_alpha=1.0, scale_bps=2.0)

    scorer.record_fill(100.0, "sell", tick_count=0)
    scorer.score(99.5, 99.45, 99.55, 1000)
    scorer.score(99.0, 98.95, 99.05, 2000)

    assert scorer.ema_markout > 0  # benign
    assert scorer.toxicity == 0.0


def test_ema_smoothing():
    """EMA with alpha < 1 blends old and new markouts."""
    scorer = MarkoutToxicityScorer(lookback=1, ema_alpha=0.3, scale_bps=2.0)

    # First fill: adverse (-1%)
    scorer.record_fill(100.0, "buy", tick_count=0)
    scorer.score(99.0, 98.95, 99.05, 1000)  # markout = -0.01

    ema_after_first = scorer.ema_markout
    assert abs(ema_after_first - (-0.01)) < 1e-9  # first data point

    # Second fill: benign (+1%)
    scorer.record_fill(100.0, "buy", tick_count=1)
    scorer.score(101.0, 100.95, 101.05, 2000)  # markout = +0.01

    # EMA = 0.3 * 0.01 + 0.7 * (-0.01) = 0.003 - 0.007 = -0.004
    assert abs(scorer.ema_markout - (-0.004)) < 1e-9


def test_toxicity_clamped_to_unit():
    """Toxicity should be in [0, 1] even with extreme markouts."""
    scorer = MarkoutToxicityScorer(lookback=1, ema_alpha=1.0, scale_bps=2.0)

    # Extreme adverse fill: price drops 50%
    scorer.record_fill(100.0, "buy", tick_count=0)
    scorer.score(50.0, 49.95, 50.05, 1000)

    assert scorer.toxicity == 0.5  # markout = -0.5, toxicity = min(1, 0.5) = 0.5


def test_h_tox_scales_with_price():
    """h_tox = toxicity * scale_bps * (mid / 10000)."""
    scorer = MarkoutToxicityScorer(lookback=1, ema_alpha=1.0, scale_bps=2.0)

    scorer.record_fill(100.0, "buy", tick_count=0)
    scorer.score(99.0, 98.95, 99.05, 1000)  # markout = -0.01 -> toxicity = 0.01

    h_tox = scorer.score(100.0, 99.95, 100.05, 2000)  # no new matured fills
    # toxicity = 0.01, h_tox = 0.01 * 2.0 * (100 / 10000) = 0.0002
    expected = 0.01 * 2.0 * (100.0 / 10_000)
    assert abs(h_tox - expected) < 1e-9


def test_multiple_pending_fills():
    """Multiple fills pending at once, all resolve."""
    scorer = MarkoutToxicityScorer(lookback=3, ema_alpha=0.5, scale_bps=2.0)

    scorer.record_fill(100.0, "buy", tick_count=0)
    scorer.record_fill(100.0, "sell", tick_count=1)

    # Advance 3 ticks to mature the first fill
    scorer.score(100.0, 99.95, 100.05, 1000)  # tick 1
    scorer.score(100.0, 99.95, 100.05, 2000)  # tick 2
    scorer.score(100.0, 99.95, 100.05, 3000)  # tick 3: first fill matures

    # Tick 4: second fill matures (price unchanged -> markout = 0 for both)
    scorer.score(100.0, 99.95, 100.05, 4000)

    assert abs(scorer.ema_markout) < 1e-9  # zero markout
    assert scorer.toxicity == 0.0


def test_no_fills_no_toxicity():
    """Without any fills recorded, toxicity stays 0 across many ticks."""
    scorer = MarkoutToxicityScorer(lookback=3)
    for i in range(20):
        h_tox = scorer.score(100.0 + i * 0.1, 99.95, 100.05, i * 1000)
        assert h_tox == 0.0
    assert scorer.toxicity == 0.0
