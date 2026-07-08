"""Tests for feeds/funding_rate.py — funding rate sources and aggregator."""
from quoting_engine.feeds.funding_rate import (
    ConstantFundingRate,
    CrossVenueFundingRate,
    HyperliquidFundingRate,
    PushFundingRate,
)


def test_constant_source():
    src = ConstantFundingRate(rate=0.01)
    assert src.fetch() == 0.01


def test_hl_source_before_update():
    src = HyperliquidFundingRate()
    assert src.fetch() is None


def test_hl_source_after_update():
    src = HyperliquidFundingRate()
    src.update(0.05)
    assert src.fetch() == 0.05


def test_cross_venue_single_source():
    src = ConstantFundingRate(rate=0.02)
    feed = CrossVenueFundingRate(sources=[src])
    result = feed.refresh()
    assert result.value == 0.02
    assert not result.stale


def test_cross_venue_median_three_sources():
    sources = [
        ConstantFundingRate(rate=0.01),
        ConstantFundingRate(rate=0.05),
        ConstantFundingRate(rate=0.03),
    ]
    feed = CrossVenueFundingRate(sources=sources)
    result = feed.refresh()
    assert result.value == 0.03  # median


def test_cross_venue_failing_source():
    """When a source returns 0 (fail), it's excluded from median."""
    hl = HyperliquidFundingRate()  # returns 0 (not updated)
    good = ConstantFundingRate(rate=0.04)
    feed = CrossVenueFundingRate(sources=[hl, good])
    result = feed.refresh()
    assert result.value == 0.04


def test_cross_venue_all_fail():
    hl = HyperliquidFundingRate()  # returns 0
    feed = CrossVenueFundingRate(sources=[hl])
    result = feed.refresh()
    assert result.stale


def test_cross_venue_latest():
    src = ConstantFundingRate(rate=0.02)
    feed = CrossVenueFundingRate(sources=[src])
    # Before refresh
    assert feed.latest() is None
    # After refresh
    feed.refresh()
    assert feed.latest().value == 0.02


def test_push_source_custom_venue():
    src = PushFundingRate("binance")
    assert src.venue == "binance"
    assert src.fetch() is None


def test_push_source_update():
    src = PushFundingRate("okx")
    src.update(0.0005)
    assert src.fetch() == 0.0005
