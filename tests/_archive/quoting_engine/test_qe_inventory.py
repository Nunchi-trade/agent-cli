"""Tests for InventorySkewer."""
from quoting_engine.config import SkewParams
from quoting_engine.inventory import InventorySkewer


def test_price_skew_long_lowers_fv():
    s = InventorySkewer(SkewParams(k_inv=0.5, inv_limit=10.0, mode="both"))
    fv = s.price_skew(fv=100.0, inventory=5.0, sigma_price=0.10)
    assert fv < 100.0  # long -> lower FV


def test_price_skew_short_raises_fv():
    s = InventorySkewer(SkewParams(k_inv=0.5, inv_limit=10.0, mode="both"))
    fv = s.price_skew(fv=100.0, inventory=-5.0, sigma_price=0.10)
    assert fv > 100.0  # short -> higher FV


def test_price_skew_flat_unchanged():
    s = InventorySkewer(SkewParams(k_inv=0.5, inv_limit=10.0, mode="both"))
    fv = s.price_skew(fv=100.0, inventory=0.0, sigma_price=0.10)
    assert fv == 100.0


def test_size_skew_long_reduces_bids():
    s = InventorySkewer(SkewParams(k_inv=0.5, inv_limit=10.0, mode="both", size_skew_factor=0.3))
    bid, ask = s.size_skew(1.0, 1.0, inventory=5.0)
    assert bid < 1.0   # shrink bids when long
    assert ask > 1.0   # grow asks when long


def test_size_skew_short_reduces_asks():
    s = InventorySkewer(SkewParams(k_inv=0.5, inv_limit=10.0, mode="both", size_skew_factor=0.3))
    bid, ask = s.size_skew(1.0, 1.0, inventory=-5.0)
    assert bid > 1.0   # grow bids when short
    assert ask < 1.0   # shrink asks when short


def test_mode_price_only():
    s = InventorySkewer(SkewParams(k_inv=0.5, inv_limit=10.0, mode="price"))
    # Price skew should work
    fv = s.price_skew(fv=100.0, inventory=5.0, sigma_price=0.10)
    assert fv < 100.0
    # Size skew should be identity
    bid, ask = s.size_skew(1.0, 1.0, inventory=5.0)
    assert bid == 1.0
    assert ask == 1.0


def test_mode_size_only():
    s = InventorySkewer(SkewParams(k_inv=0.5, inv_limit=10.0, mode="size", size_skew_factor=0.3))
    # Price skew should be identity
    fv = s.price_skew(fv=100.0, inventory=5.0, sigma_price=0.10)
    assert fv == 100.0
    # Size skew should work
    bid, ask = s.size_skew(1.0, 1.0, inventory=5.0)
    assert bid < 1.0


def test_size_skew_clamp_non_negative():
    s = InventorySkewer(SkewParams(k_inv=1.0, inv_limit=1.0, mode="size", size_skew_factor=2.0))
    # Extreme: inventory = inv_limit, factor = 2.0 -> mult = 1 - 2*1 = -1 -> clamped to 0
    bid, ask = s.size_skew(1.0, 1.0, inventory=1.0)
    assert bid >= 0.0
    assert ask >= 0.0
