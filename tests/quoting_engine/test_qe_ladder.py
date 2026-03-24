"""Tests for LadderBuilder."""
import math
from quoting_engine.config import LadderParams
from quoting_engine.ladder import LadderBuilder


def test_ladder_level_count():
    p = LadderParams(num_levels=3, delta_bps=1.0, s0=1.0, lam=0.5)
    builder = LadderBuilder(p, tick_size=0.01)
    levels = builder.build(fv=100.0, half_spread=0.05, mid=100.0)
    assert len(levels) == 3


def test_ladder_symmetry_flat():
    p = LadderParams(num_levels=2, delta_bps=1.0, s0=1.0, lam=0.3)
    builder = LadderBuilder(p, tick_size=0.01)
    levels = builder.build(fv=100.0, half_spread=0.05, mid=100.0)
    for lv in levels:
        # Bid and ask should be symmetric around FV
        assert abs((lv.bid_price + lv.ask_price) / 2.0 - 100.0) < 0.02
        # Sizes should be equal with no skew
        assert lv.bid_size == lv.ask_size


def test_ladder_price_spacing():
    p = LadderParams(num_levels=3, delta_bps=2.0, s0=1.0, lam=0.0)
    builder = LadderBuilder(p, tick_size=0.01)
    levels = builder.build(fv=100.0, half_spread=0.05, mid=100.0)
    # Level 1 should be delta further from FV than level 0
    delta = 2.0 * 100.0 / 10_000  # 0.02
    diff = levels[0].bid_price - levels[1].bid_price
    assert abs(diff - delta) < 0.02  # within a tick


def test_ladder_size_decay():
    p = LadderParams(num_levels=3, delta_bps=1.0, s0=1.0, lam=0.5)
    builder = LadderBuilder(p, tick_size=0.01)
    levels = builder.build(fv=100.0, half_spread=0.05, mid=100.0)
    assert levels[0].bid_size > levels[1].bid_size
    assert levels[1].bid_size > levels[2].bid_size


def test_ladder_size_skew():
    p = LadderParams(num_levels=2, delta_bps=1.0, s0=1.0, lam=0.0)
    builder = LadderBuilder(p, tick_size=0.01)
    levels = builder.build(fv=100.0, half_spread=0.05, mid=100.0,
                           bid_size_mult=0.7, ask_size_mult=1.3)
    for lv in levels:
        assert lv.ask_size > lv.bid_size


def test_ladder_tick_rounding():
    p = LadderParams(num_levels=1, delta_bps=0.0, s0=1.0, lam=0.0)
    builder = LadderBuilder(p, tick_size=0.01)
    levels = builder.build(fv=100.003, half_spread=0.047, mid=100.0)
    # Prices should be rounded to tick (0.01)
    for lv in levels:
        assert abs(lv.bid_price - round(lv.bid_price, 2)) < 1e-10
        assert abs(lv.ask_price - round(lv.ask_price, 2)) < 1e-10


def test_zero_fv_returns_empty():
    p = LadderParams(num_levels=3)
    builder = LadderBuilder(p, tick_size=0.01)
    levels = builder.build(fv=0.0, half_spread=0.05, mid=100.0)
    assert levels == []


def test_ladder_min_size_ratio():
    """Outer levels should never drop below min_size_ratio * s0."""
    p = LadderParams(num_levels=5, delta_bps=1.0, s0=1.0, lam=2.0,
                     min_size_ratio=0.1)
    builder = LadderBuilder(p, tick_size=0.01)
    levels = builder.build(fv=100.0, half_spread=0.05, mid=100.0)
    min_floor = 0.1 * 1.0  # min_size_ratio * s0
    for lv in levels:
        assert lv.bid_size >= min_floor - 1e-9
        assert lv.ask_size >= min_floor - 1e-9
    # Without floor, level 4 would be exp(-2.0*4)=0.00034 which is far below 0.1
    assert levels[-1].bid_size >= min_floor - 1e-9
