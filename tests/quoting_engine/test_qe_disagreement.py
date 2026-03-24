"""Tests for Disagreement Mode (G2)."""
from quoting_engine.config import MarketConfig, DisagreementConfig
from quoting_engine.engine import QuotingEngine


def _make_engine(disagree_cfg: DisagreementConfig) -> QuotingEngine:
    cfg = MarketConfig(disagreement=disagree_cfg)
    engine = QuotingEngine(cfg)
    for _ in range(35):
        engine.tick(mid=100.0, bid=99.9, ask=100.1)
    return engine


def test_disagreement_widens_spread():
    """Large oracle/external divergence -> wider spread and reduced sizes."""
    engine = _make_engine(DisagreementConfig(
        enabled=True, threshold_bps=10.0, spread_mult=1.5, size_mult=0.7,
    ))
    # Baseline: no external ref (defaults to oracle)
    r_base = engine.tick(mid=100.0, bid=99.9, ask=100.1)

    # Divergent external: 100.5 vs mid=100.0 -> 50 bps divergence > 10 bps threshold
    r_disagree = engine.tick(mid=100.0, bid=99.9, ask=100.1, external_ref=100.5)

    assert r_disagree.half_spread > r_base.half_spread
    assert r_disagree.meta["disagree_active"] is True


def test_disagreement_below_threshold_noop():
    """Small divergence below threshold -> no effect."""
    engine = _make_engine(DisagreementConfig(
        enabled=True, threshold_bps=10.0, spread_mult=1.5, size_mult=0.7,
    ))
    # 5 bps divergence (100.05 vs 100.0) < 10 bps threshold
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, external_ref=100.05)
    assert r.meta["disagree_active"] is False


def test_disagreement_disabled():
    """Disabled disagreement -> no effect even with large divergence."""
    engine = _make_engine(DisagreementConfig(enabled=False))
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, external_ref=110.0)
    assert r.meta["disagree_active"] is False
