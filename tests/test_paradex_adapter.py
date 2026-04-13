from __future__ import annotations

from adapters.paradex_adapter import ParadexVenueAdapter


class FakeProxy:
    def get_market_metadata(self, instrument: str):
        return {
            "symbol": instrument,
            "price_tick_size": "0.001",
            "order_size_increment": "0.01",
        }

    def get_market_summary(self, instrument: str):
        return {
            "best_bid": "83.895",
            "best_ask": "83.900",
            "mark_price": "83.8975",
            "volume_24h": "3900000",
            "open_interest": "12345",
        }


def test_get_snapshot_uses_summary_prices():
    adapter = ParadexVenueAdapter(FakeProxy())
    snap = adapter.get_snapshot("SOL-USD-PERP")
    assert snap.instrument == "SOL-USD-PERP"
    assert snap.mid_price == 83.8975
    assert snap.bid == 83.895
    assert snap.ask == 83.9
    assert snap.volume_24h == 3900000.0
    assert snap.open_interest == 12345.0
    assert snap.spread_bps > 0
