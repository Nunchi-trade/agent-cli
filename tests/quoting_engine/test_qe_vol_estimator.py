"""Tests for RollingVolEstimator."""
from quoting_engine.vol_estimator import RollingVolEstimator


def test_fallback_before_min_samples():
    vol = RollingVolEstimator(window=30, min_samples=3)
    sigma_p, sigma_l = vol.update(100.0)
    # Only 1 price, no log returns yet -> fallback
    assert sigma_l == 3.0 / 10_000
    assert not vol.ready


def test_ready_flag():
    vol = RollingVolEstimator(window=30, min_samples=3)
    for p in [100.0, 100.1, 100.2]:
        vol.update(p)
    assert not vol.ready  # only 2 log returns
    vol.update(100.3)
    assert vol.ready  # now 3 log returns


def test_constant_price_near_zero_vol():
    vol = RollingVolEstimator(window=30, min_samples=3)
    for _ in range(10):
        sigma_p, sigma_l = vol.update(100.0)
    # Constant price -> zero variance (within epsilon)
    assert sigma_l < 1e-5


def test_volatile_prices_higher_vol():
    vol_const = RollingVolEstimator(window=30, min_samples=3)
    vol_move = RollingVolEstimator(window=30, min_samples=3)

    for _ in range(10):
        vol_const.update(100.0)

    prices = [100.0, 101.0, 99.0, 102.0, 98.0, 101.0, 99.5, 100.5, 98.5, 101.5]
    for p in prices:
        vol_move.update(p)

    _, sigma_const = vol_const.update(100.0)
    _, sigma_move = vol_move.update(100.0)
    assert sigma_move > sigma_const


def test_window_size_limits_history():
    vol = RollingVolEstimator(window=5, min_samples=3)
    for i in range(20):
        vol.update(100.0 + i * 0.1)
    assert vol.sample_count == 5  # bounded by window


def test_sigma_price_scales_with_mid():
    vol = RollingVolEstimator(window=30, min_samples=3)
    prices_100 = [100.0, 100.1, 99.9, 100.05, 99.95]
    for p in prices_100:
        sp1, sl1 = vol.update(p)

    vol2 = RollingVolEstimator(window=30, min_samples=3)
    prices_1000 = [p * 10 for p in prices_100]
    for p in prices_1000:
        sp2, sl2 = vol2.update(p)

    # sigma_log should be approximately equal (same % moves)
    assert abs(sl1 - sl2) < 1e-8
    # sigma_price should scale by ~10x
    assert sp2 > sp1 * 5  # rough check
