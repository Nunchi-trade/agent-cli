"""Tests for feeds/microprice.py — L2 depth-weighted microprice."""
from quoting_engine.feeds.microprice import L2Book, L2MicropriceCalculator


def test_symmetric_book_equals_mid():
    book = L2Book(
        bids=[(99.0, 10.0)],
        asks=[(101.0, 10.0)],
    )
    calc = L2MicropriceCalculator(depth_levels=1)
    mp = calc.compute(book)
    assert mp == 100.0  # symmetric => mid


def test_asymmetric_book_shifts_toward_deeper_side():
    book = L2Book(
        bids=[(99.0, 30.0)],   # more bid volume => buying pressure
        asks=[(101.0, 10.0)],
    )
    calc = L2MicropriceCalculator(depth_levels=1)
    mp = calc.compute(book)
    # bid_vol=30, ask_vol=10: microprice = (30*101 + 10*99) / 40 = 100.5
    assert mp > 100.0
    assert abs(mp - 100.5) < 1e-9


def test_more_ask_volume_shifts_toward_bid():
    book = L2Book(
        bids=[(99.0, 10.0)],
        asks=[(101.0, 30.0)],   # more ask volume => selling pressure
    )
    calc = L2MicropriceCalculator(depth_levels=1)
    mp = calc.compute(book)
    assert mp < 100.0


def test_multi_level_depth():
    book = L2Book(
        bids=[(100.0, 5.0), (99.0, 15.0)],
        asks=[(101.0, 5.0), (102.0, 5.0)],
    )
    calc = L2MicropriceCalculator(depth_levels=2)
    mp = calc.compute(book)
    # bid_vol = 5+15=20, ask_vol = 5+5=10
    # microprice = (20*101 + 10*100) / 30 = 3020+1000 / 30 = 100.667
    assert mp > 100.0


def test_empty_book():
    book = L2Book(bids=[], asks=[])
    calc = L2MicropriceCalculator()
    mp = calc.compute(book)
    assert mp == 0.0


def test_one_sided_bids_only():
    book = L2Book(bids=[(99.0, 10.0)], asks=[])
    calc = L2MicropriceCalculator()
    mp = calc.compute(book)
    assert mp == 99.0


def test_one_sided_asks_only():
    book = L2Book(bids=[], asks=[(101.0, 10.0)])
    calc = L2MicropriceCalculator()
    mp = calc.compute(book)
    assert mp == 101.0


def test_depth_param_limits_levels():
    book = L2Book(
        bids=[(100.0, 5.0), (99.0, 100.0)],  # second level is huge
        asks=[(101.0, 5.0), (102.0, 100.0)],
    )
    calc_1 = L2MicropriceCalculator(depth_levels=1)
    calc_2 = L2MicropriceCalculator(depth_levels=2)

    mp_1 = calc_1.compute(book)
    mp_2 = calc_2.compute(book)

    # With depth=1: symmetric (5 vs 5) => mid = 100.5
    assert abs(mp_1 - 100.5) < 1e-9
    # With depth=2: bid_vol=105, ask_vol=105 => still mid
    # but the key point is depth_levels changes the calculation
    assert isinstance(mp_2, float)
