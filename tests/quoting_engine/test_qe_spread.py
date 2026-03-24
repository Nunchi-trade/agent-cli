"""Tests for SpreadCalculator."""
from quoting_engine.config import SpreadParams
from quoting_engine.spread import SpreadCalculator


def test_spread_floor_at_half_tick():
    p = SpreadParams(h_fee_bps=0.0, vol_scale=0.0, min_spread_bps=0.0, max_spread_bps=1000.0)
    calc = SpreadCalculator(p, tick_size=0.01)
    h = calc.compute(mid=100.0, sigma_price=0.0)
    assert h >= 0.005  # half tick


def test_spread_increases_with_vol():
    p = SpreadParams(h_fee_bps=0.0, vol_scale=1.0, min_spread_bps=0.0, max_spread_bps=1000.0)
    calc = SpreadCalculator(p, tick_size=0.0001)
    h_low = calc.compute(mid=100.0, sigma_price=0.01)
    h_high = calc.compute(mid=100.0, sigma_price=0.10)
    assert h_high > h_low


def test_spread_clamp_min():
    p = SpreadParams(h_fee_bps=0.0, vol_scale=0.0, min_spread_bps=10.0, max_spread_bps=100.0)
    calc = SpreadCalculator(p, tick_size=0.0001)
    h = calc.compute(mid=100.0, sigma_price=0.0)
    h_min = (10.0 / 2.0) * 100.0 / 10_000  # 0.05
    assert h >= h_min - 1e-10


def test_spread_clamp_max():
    p = SpreadParams(h_fee_bps=100.0, vol_scale=10.0, min_spread_bps=0.0, max_spread_bps=20.0)
    calc = SpreadCalculator(p, tick_size=0.0001)
    h = calc.compute(mid=100.0, sigma_price=1.0)
    h_max = (20.0 / 2.0) * 100.0 / 10_000  # 0.10
    assert h <= h_max + 1e-10


def test_vol_multiplier_widens():
    p = SpreadParams(h_fee_bps=1.0, vol_scale=1.0, min_spread_bps=0.0, max_spread_bps=1000.0)
    calc = SpreadCalculator(p, tick_size=0.0001)
    h_base = calc.compute(mid=100.0, sigma_price=0.05, m_vol=1.0)
    h_wide = calc.compute(mid=100.0, sigma_price=0.05, m_vol=2.5)
    assert h_wide > h_base


def test_dd_multiplier_widens():
    p = SpreadParams(h_fee_bps=1.0, vol_scale=1.0, min_spread_bps=0.0, max_spread_bps=1000.0)
    calc = SpreadCalculator(p, tick_size=0.0001)
    h_base = calc.compute(mid=100.0, sigma_price=0.05, m_dd=1.0)
    h_wide = calc.compute(mid=100.0, sigma_price=0.05, m_dd=1.5)
    assert h_wide > h_base


def test_rebate_credit_narrows():
    p_no_rebate = SpreadParams(h_fee_bps=5.0, vol_scale=1.0, rebate_credit_bps=0.0,
                                min_spread_bps=0.0, max_spread_bps=1000.0)
    p_rebate = SpreadParams(h_fee_bps=5.0, vol_scale=1.0, rebate_credit_bps=2.0,
                             min_spread_bps=0.0, max_spread_bps=1000.0)
    calc_no = SpreadCalculator(p_no_rebate, tick_size=0.0001)
    calc_yes = SpreadCalculator(p_rebate, tick_size=0.0001)
    h_no = calc_no.compute(mid=100.0, sigma_price=0.05)
    h_yes = calc_yes.compute(mid=100.0, sigma_price=0.05)
    assert h_yes <= h_no


def test_zero_mid_returns_zero():
    p = SpreadParams()
    calc = SpreadCalculator(p, tick_size=0.01)
    assert calc.compute(mid=0.0, sigma_price=0.0) == 0.0


def test_spread_growth_mode():
    """Growth mode should reduce the fee component by ~90%."""
    p_normal = SpreadParams(h_fee_bps=10.0, vol_scale=0.0, rebate_credit_bps=2.0,
                            min_spread_bps=0.0, max_spread_bps=1000.0)
    p_growth = SpreadParams(h_fee_bps=10.0, vol_scale=0.0, rebate_credit_bps=2.0,
                            min_spread_bps=0.0, max_spread_bps=1000.0,
                            growth_mode=True, growth_mode_scale=0.1)
    calc_normal = SpreadCalculator(p_normal, tick_size=0.0001)
    calc_growth = SpreadCalculator(p_growth, tick_size=0.0001)
    h_normal = calc_normal.compute(mid=100.0, sigma_price=0.0)
    h_growth = calc_growth.compute(mid=100.0, sigma_price=0.0)
    # Growth mode should produce a significantly smaller spread
    assert h_growth < h_normal * 0.5
