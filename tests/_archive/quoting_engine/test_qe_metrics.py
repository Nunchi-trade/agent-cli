"""Tests for QuotingMetrics KPI tracking."""
from dataclasses import dataclass
from quoting_engine.metrics import QuotingMetrics


@dataclass
class MockLevel:
    """Minimal LadderLevel stand-in for metrics tests."""
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float


def test_uptime_all_quoting():
    """10 ticks all with two-sided levels -> uptime=1.0."""
    m = QuotingMetrics()
    levels = [MockLevel(99.0, 1.0, 101.0, 1.0)]
    for _ in range(10):
        m.on_tick(levels, halted=False, mid=100.0, best_bid=99.0, best_ask=101.0)
    assert m.uptime == 1.0
    assert m._total_ticks == 10
    assert m._quoting_ticks == 10


def test_uptime_with_halts():
    """5 halted + 5 quoting -> uptime=0.5."""
    m = QuotingMetrics()
    levels = [MockLevel(99.0, 1.0, 101.0, 1.0)]
    for _ in range(5):
        m.on_tick([], halted=True, mid=100.0, best_bid=99.0, best_ask=101.0)
    for _ in range(5):
        m.on_tick(levels, halted=False, mid=100.0, best_bid=99.0, best_ask=101.0)
    assert m.uptime == 0.5


def test_tob_share():
    """Our best bid at or above market -> TOB credit."""
    m = QuotingMetrics()
    # Our bid=99.5 >= market best_bid=99.0 -> TOB
    levels_at_tob = [MockLevel(99.5, 1.0, 100.5, 1.0)]
    # Our bid=98.0 < market best_bid=99.0 AND ask=102.0 > best_ask=101.0 -> no TOB
    levels_away = [MockLevel(98.0, 1.0, 102.0, 1.0)]

    for _ in range(6):
        m.on_tick(levels_at_tob, halted=False, mid=100.0, best_bid=99.0, best_ask=101.0)
    for _ in range(4):
        m.on_tick(levels_away, halted=False, mid=100.0, best_bid=99.0, best_ask=101.0)

    assert abs(m.tob_share - 0.6) < 1e-9


def test_markout_computation():
    """Verify markout calculation with known mid history."""
    m = QuotingMetrics(markout_horizons=(1, 2))
    levels = [MockLevel(99.0, 1.0, 101.0, 1.0)]

    # Tick 0: mid=100
    m.on_tick(levels, halted=False, mid=100.0, best_bid=99.0, best_ask=101.0)
    # Fill at tick 0: buy at 99.5
    m.on_fill("buy", 99.5, 1.0, 100.0, tick_index=0)

    # Tick 1: mid=101
    m.on_tick(levels, halted=False, mid=101.0, best_bid=100.0, best_ask=102.0)
    # Tick 2: mid=102
    m.on_tick(levels, halted=False, mid=102.0, best_bid=101.0, best_ask=103.0)

    markouts = m.compute_markouts()
    # 1-tick markout: mid[1] - fill_price = 101 - 99.5 = 1.5
    assert markouts[1] == 1.5
    # 2-tick markout: mid[2] - fill_price = 102 - 99.5 = 2.5
    assert markouts[2] == 2.5


def test_effective_spread_capture():
    """Sell fill above mid -> positive effective spread capture."""
    m = QuotingMetrics()
    # Sell at 100.5 when mid is 100.0 -> eff_spread = 2*(100.5-100.0)*1 = 1.0
    m.on_fill("sell", 100.5, 1.0, 100.0, tick_index=0)
    assert abs(m.effective_spread - 1.0) < 1e-9

    # Buy at 99.5 when mid is 100.0 -> eff_spread = 2*(99.5-100.0)*(-1) = 1.0
    m.on_fill("buy", 99.5, 1.0, 100.0, tick_index=1)
    assert abs(m.effective_spread - 1.0) < 1e-9  # average of 1.0 and 1.0


def test_metrics_reset():
    """Reset should clear all counters."""
    m = QuotingMetrics()
    levels = [MockLevel(99.0, 1.0, 101.0, 1.0)]
    m.on_tick(levels, halted=False, mid=100.0, best_bid=99.0, best_ask=101.0)
    m.on_fill("buy", 99.5, 1.0, 100.0, tick_index=0)
    assert m._total_ticks == 1
    assert len(m._fills) == 1

    m.reset()
    assert m._total_ticks == 0
    assert m._quoting_ticks == 0
    assert m._tob_ticks == 0
    assert len(m._fills) == 0
    assert m.uptime == 0.0
    assert m.effective_spread is None
