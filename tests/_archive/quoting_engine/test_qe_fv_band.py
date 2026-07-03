"""Tests for FV band clamping (G1)."""
from quoting_engine.config import MarketConfig, FairValueBandConfig, FairValueWeights
from quoting_engine.engine import QuotingEngine


def _make_engine(fv_band_cfg: FairValueBandConfig, w_external: float = 0.5) -> QuotingEngine:
    cfg = MarketConfig(
        fv_weights=FairValueWeights(w_oracle=0.5, w_external=w_external, w_microprice=0.0, w_inventory=0.0),
        fv_band=fv_band_cfg,
    )
    engine = QuotingEngine(cfg)
    # Warm up vol estimator
    for _ in range(35):
        engine.tick(mid=100.0, bid=99.9, ask=100.1)
    return engine


def test_fv_band_clamps_extreme():
    """When external ref is far from oracle, FV should be clamped within band."""
    engine = _make_engine(
        FairValueBandConfig(enabled=True, band_min_bps=2.0, k_sigma=2.0, k_disagree=0.3),
        w_external=0.5,
    )
    # external_ref=120 is 20% away from mid=100 -> FV blend would be ~110
    # Band = max(0.02, tiny_sigma, 0.3*20) = 6.0 -> clamp to [94, 106]
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, external_ref=120.0)
    assert r.fv_raw <= 106.0 + 0.01, f"FV {r.fv_raw} should be clamped to band upper ~106"
    assert r.fv_raw >= 100.0, f"FV {r.fv_raw} should be above oracle"


def test_fv_band_passthrough_when_close():
    """When external ref is close to oracle, FV passes through unclamped."""
    engine = _make_engine(
        FairValueBandConfig(enabled=True, band_min_bps=50.0, k_sigma=2.0, k_disagree=1.5),
        w_external=0.5,
    )
    # external_ref=100.01 is very close -> band is wide enough to not clamp
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, external_ref=100.01)
    # FV should be ~100.005 (blend of 100.0 and 100.01), within wide band
    assert abs(r.fv_raw - 100.005) < 0.1


def test_fv_band_disabled():
    """When band is disabled, FV is not clamped even with extreme external ref."""
    engine = _make_engine(
        FairValueBandConfig(enabled=False),
        w_external=0.5,
    )
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, external_ref=120.0)
    # Naive blend: 0.5*100 + 0.5*120 = 110
    assert r.fv_raw > 108.0, f"FV {r.fv_raw} should be unclamped (~110)"
