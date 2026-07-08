"""Tests for Funding Boundary Maker (G7)."""
import datetime

from quoting_engine.config import MarketConfig, FundingBoundaryConfig, FairValueWeights
from quoting_engine.engine import QuotingEngine


def _ts_for(hour: int, minute: int, second: int = 0) -> int:
    """Build a UTC timestamp_ms for a specific time on 2026-01-15."""
    dt = datetime.datetime(2026, 1, 15, hour, minute, second, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def _make_engine(fb_cfg: FundingBoundaryConfig, w_external: float = 0.3) -> QuotingEngine:
    cfg = MarketConfig(
        funding_boundary=fb_cfg,
        fv_weights=FairValueWeights(w_oracle=0.5, w_external=w_external, w_microprice=0.2, w_inventory=0.0),
    )
    engine = QuotingEngine(cfg)
    for _ in range(35):
        engine.tick(mid=100.0, bid=99.9, ask=100.1)
    return engine


def test_funding_boundary_active_pre():
    """HH:59:45 is within 30s pre-window -> boundary active."""
    engine = _make_engine(FundingBoundaryConfig(
        enabled=True, pre_window_s=30, post_window_s=30, size_mult=0.3,
    ))
    ts = _ts_for(14, 59, 45)  # 15 seconds before 15:00
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert r.meta["in_funding_boundary"] is True


def test_funding_boundary_active_post():
    """HH:00:15 is within 30s post-window -> boundary active."""
    engine = _make_engine(FundingBoundaryConfig(
        enabled=True, pre_window_s=30, post_window_s=30, size_mult=0.3,
    ))
    ts = _ts_for(15, 0, 15)  # 15 seconds after 15:00
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert r.meta["in_funding_boundary"] is True


def test_funding_boundary_outside():
    """HH:30:00 is far from boundary -> not active."""
    engine = _make_engine(FundingBoundaryConfig(
        enabled=True, pre_window_s=30, post_window_s=30, size_mult=0.3,
    ))
    ts = _ts_for(15, 30, 0)
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, now_ms=ts, timestamp_ms=ts)
    assert r.meta["in_funding_boundary"] is False


def test_funding_boundary_pins_fv():
    """During boundary with pin_fv_to_oracle, FV should equal oracle mid."""
    engine = _make_engine(FundingBoundaryConfig(
        enabled=True, pre_window_s=30, post_window_s=30,
        size_mult=0.3, pin_fv_to_oracle=True,
    ), w_external=0.5)
    ts = _ts_for(14, 59, 45)
    # External ref would normally pull FV away from oracle
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, external_ref=110.0,
                    now_ms=ts, timestamp_ms=ts)
    # FV should be pinned to oracle (100.0), not pulled toward 110
    # fv_raw should equal mid since pin_fv_to_oracle overrides the blend
    assert abs(r.fv_raw - 100.0) < 0.01, f"FV {r.fv_raw} should be pinned to oracle 100.0"
