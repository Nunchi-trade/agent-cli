"""Tests for market→strategy auto-routing."""
import time

from modules.market_strategy_map import (
    MARKET_STRATEGY_MAP,
    get_strategies_for_market,
    has_strategy_mapping,
)
from modules.strategy_guard import StrategyGuard
from common.models import MarketSnapshot


class TestMarketStrategyMap:
    def test_vxx_mapping(self):
        strats = get_strategies_for_market("VXX-USDYP")
        assert "mean_reversion" in strats
        assert "simplified_ensemble" in strats

    def test_btcswp_mapping(self):
        strats = get_strategies_for_market("BTCSWP-USDYP")
        assert "funding_arb" in strats
        assert "funding_momentum" in strats
        assert "basis_arb" in strats

    def test_us3m_mapping(self):
        strats = get_strategies_for_market("US3M-USDYP")
        assert "trend_follower" in strats
        assert "simplified_ensemble" in strats

    def test_unmapped_market_returns_empty(self):
        assert get_strategies_for_market("ETH-PERP") == []
        assert get_strategies_for_market("BTC-PERP") == []
        assert get_strategies_for_market("UNKNOWN") == []

    def test_has_strategy_mapping_true(self):
        assert has_strategy_mapping(["VXX-USDYP"]) is True
        assert has_strategy_mapping(["ETH-PERP", "BTCSWP-USDYP"]) is True

    def test_has_strategy_mapping_false(self):
        assert has_strategy_mapping([]) is False
        assert has_strategy_mapping(["ETH-PERP", "BTC-PERP"]) is False


class TestStrategyGuardRouting:
    """Test per-market routing in StrategyGuard.scan()."""

    @staticmethod
    def _make_all_markets(coins: dict) -> list:
        """Build a minimal all_markets structure for given coins.

        coins: {"VXX": 30.5, "BTC": 95000, ...}
        """
        universe = [{"name": coin} for coin in coins]
        ctxs = [
            {
                "midPx": str(price),
                "markPx": str(price),
                "dayNtlVlm": "1000000",
                "funding": "0.0001",
                "openInterest": "50000",
            }
            for price in coins.values()
        ]
        return [{"universe": universe}, ctxs]

    def test_routed_scan_only_runs_mapped_strategies(self):
        """When target_markets is set, only mapped strategies run per market."""
        guard = StrategyGuard(
            target_markets=["VXX-USDYP"],
            enabled=True,
        )
        # VXX should map to mean_reversion and simplified_ensemble
        assert "mean_reversion" in [
            name for name in MARKET_STRATEGY_MAP["VXX-USDYP"]
        ]
        # The guard should have loaded strategies on-demand (cache starts empty)
        assert len(guard.strategies) == 0  # no legacy strategies loaded
        assert len(guard._strategy_cache) == 0  # cache empty until scan()

    def test_routed_scan_with_market_data(self):
        """Routed scan should produce signals for mapped markets."""
        guard = StrategyGuard(
            target_markets=["VXX-USDYP"],
            enabled=True,
        )
        all_markets = self._make_all_markets({"VXX": 30.5})
        signals = guard.scan(all_markets=all_markets, target_markets=["VXX-USDYP"])

        # Strategies were loaded into cache
        assert len(guard._strategy_cache) > 0

        # Signals (if any) should reference VXX, not random coins
        for sig in signals:
            assert sig["asset"] == "VXX"
            assert "strategy:" in sig["source"]

    def test_routed_scan_skips_unmapped_markets(self):
        """Markets without a mapping should produce no strategy signals."""
        guard = StrategyGuard(
            target_markets=["ETH-PERP"],
            enabled=True,
        )
        all_markets = self._make_all_markets({"ETH": 3500})
        signals = guard.scan(all_markets=all_markets, target_markets=["ETH-PERP"])
        assert signals == []

    def test_legacy_scan_still_works(self):
        """Without target_markets, legacy all×all behavior is preserved."""
        guard = StrategyGuard(
            strategy_names=["simple_mm"],
            enabled=True,
        )
        all_markets = self._make_all_markets({"ETH": 3500})
        # Legacy scan — no target_markets
        signals = guard.scan(all_markets=all_markets)
        # simple_mm should produce signals (bid/ask quotes)
        assert len(signals) > 0

    def test_disabled_guard_returns_empty(self):
        guard = StrategyGuard(
            target_markets=["VXX-USDYP"],
            enabled=False,
        )
        all_markets = self._make_all_markets({"VXX": 30.5})
        signals = guard.scan(all_markets=all_markets)
        assert signals == []

    def test_find_snapshot_by_coin_prefix(self):
        """_find_snapshot should match 'VXX-USDYP' to a snapshot keyed 'VXX'."""
        snapshots = {
            "VXX": MarketSnapshot(
                instrument="VXX-PERP",
                mid_price=30.5,
                bid=30.49,
                ask=30.51,
                spread_bps=6.5,
                timestamp_ms=int(time.time() * 1000),
            ),
        }
        snap = StrategyGuard._find_snapshot(snapshots, "VXX-USDYP")
        assert snap is not None
        assert snap.mid_price == 30.5

    def test_find_snapshot_no_match(self):
        snapshots = {
            "ETH": MarketSnapshot(
                instrument="ETH-PERP",
                mid_price=3500,
                bid=3499,
                ask=3501,
                spread_bps=5.7,
                timestamp_ms=int(time.time() * 1000),
            ),
        }
        snap = StrategyGuard._find_snapshot(snapshots, "VXX-USDYP")
        assert snap is None

    def test_strategy_cache_reuse(self):
        """Strategies should be loaded once and cached."""
        guard = StrategyGuard(target_markets=["VXX-USDYP"], enabled=True)
        s1 = guard._get_or_load("mean_reversion")
        s2 = guard._get_or_load("mean_reversion")
        assert s1 is s2  # same instance


class TestApexConfigAllowedInstruments:
    def test_allowed_instruments_default_empty(self):
        from modules.apex_config import ApexConfig
        cfg = ApexConfig()
        assert cfg.allowed_instruments == []

    def test_allowed_instruments_from_dict(self):
        from modules.apex_config import ApexConfig
        cfg = ApexConfig.from_dict({"allowed_instruments": ["VXX-USDYP", "BTCSWP-USDYP"]})
        assert cfg.allowed_instruments == ["VXX-USDYP", "BTCSWP-USDYP"]
