"""Tests for Soft/Hard Inventory Caps + Micro-Clips (G5)."""
from quoting_engine.config import MarketConfig, SkewParams
from quoting_engine.engine import QuotingEngine
from quoting_engine.inventory import InventorySkewer


def _make_skewer(**kwargs) -> InventorySkewer:
    defaults = dict(k_inv=0.5, inv_limit=10.0, mode="both", size_skew_factor=0.3,
                    soft_cap=5.0, hard_cap=8.0, micro_clip_size=0.2, micro_clip_interval=3)
    defaults.update(kwargs)
    return InventorySkewer(SkewParams(**defaults))


def test_inv_state_normal():
    """Inventory below soft_cap -> normal."""
    s = _make_skewer(soft_cap=5.0, hard_cap=8.0)
    assert s.inventory_state(3.0) == "normal"
    assert s.inventory_state(-4.0) == "normal"
    assert s.inventory_state(0.0) == "normal"


def test_inv_state_soft_breach():
    """Inventory between soft_cap and hard_cap -> soft_breach."""
    s = _make_skewer(soft_cap=5.0, hard_cap=8.0)
    assert s.inventory_state(6.0) == "soft_breach"
    assert s.inventory_state(-7.0) == "soft_breach"


def test_inv_state_hard_breach():
    """Inventory >= hard_cap -> hard_breach."""
    s = _make_skewer(soft_cap=5.0, hard_cap=8.0)
    assert s.inventory_state(8.0) == "hard_breach"
    assert s.inventory_state(-10.0) == "hard_breach"


def test_micro_clip_generation():
    """Micro-clip fires during soft_breach on correct interval."""
    s = _make_skewer(soft_cap=5.0, hard_cap=8.0, micro_clip_size=0.2, micro_clip_interval=3)
    # Long position -> clip should sell
    clip = s.micro_clip_order(inventory=6.0, tick_count=3)  # 3 % 3 == 0
    assert clip is not None
    assert clip["side"] == "sell"
    assert clip["size"] == 0.2

    # Short position -> clip should buy
    clip = s.micro_clip_order(inventory=-6.0, tick_count=6)  # 6 % 3 == 0
    assert clip is not None
    assert clip["side"] == "buy"


def test_micro_clip_interval():
    """Micro-clip does not fire on non-interval ticks."""
    s = _make_skewer(micro_clip_interval=3)
    clip = s.micro_clip_order(inventory=6.0, tick_count=4)  # 4 % 3 != 0
    assert clip is None


def test_backwards_compat_no_caps():
    """When soft_cap=0 and hard_cap=0, falls back to inv_limit."""
    s = _make_skewer(inv_limit=10.0, soft_cap=0.0, hard_cap=0.0, micro_clip_size=0.0)
    # Below inv_limit -> normal
    assert s.inventory_state(9.0) == "normal"
    # At inv_limit -> hard_breach (since soft=hard=inv_limit)
    assert s.inventory_state(10.0) == "hard_breach"
    assert s.effective_limit() == 10.0


def test_engine_hard_breach_halts():
    """Engine halts when inventory exceeds hard_cap."""
    cfg = MarketConfig(skew=SkewParams(
        k_inv=0.5, inv_limit=10.0, soft_cap=5.0, hard_cap=8.0,
    ))
    engine = QuotingEngine(cfg)
    for _ in range(35):
        engine.tick(mid=100.0, bid=99.9, ask=100.1)
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, inventory=9.0)
    assert r.halted is True
    assert r.reduce_only is True


def test_engine_soft_breach_reduce_only():
    """Engine sets reduce_only when inventory in soft_breach."""
    cfg = MarketConfig(skew=SkewParams(
        k_inv=0.5, inv_limit=10.0, soft_cap=5.0, hard_cap=8.0,
    ))
    engine = QuotingEngine(cfg)
    for _ in range(35):
        engine.tick(mid=100.0, bid=99.9, ask=100.1)
    r = engine.tick(mid=100.0, bid=99.9, ask=100.1, inventory=6.0)
    assert r.halted is False
    assert r.reduce_only is True
    assert r.meta["inv_state"] == "soft_breach"
