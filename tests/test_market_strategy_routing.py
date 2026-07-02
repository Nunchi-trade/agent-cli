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
    def test_btcswp_osrs_mapping(self):
        assert get_strategies_for_market("BTCSWP-OSRS") == ["cfi_hedge"]

    def test_btcswp_para_mapping(self):
        assert get_strategies_for_market("BTCSWP-PARA") == ["cfi_hedge"]

    def test_btcswp_mapping(self):
        assert get_strategies_for_market("BTCSWP-USDYP") == ["cfi_hedge"]

    def test_unmapped_market_returns_empty(self):
        assert get_strategies_for_market("ETH-PERP") == []
        assert get_strategies_for_market("BTC-PERP") == []
        assert get_strategies_for_market("VXX-USDYP") == []
        assert get_strategies_for_market("UNKNOWN") == []

    def test_has_strategy_mapping_true(self):
        assert has_strategy_mapping(["BTCSWP-PARA"]) is True
        assert has_strategy_mapping(["ETH-PERP", "BTCSWP-USDYP"]) is True

    def test_has_strategy_mapping_false(self):
        assert has_strategy_mapping([]) is False
        assert has_strategy_mapping(["ETH-PERP", "BTC-PERP"]) is False


class TestStrategyGuardRouting:
    """Test per-market routing in StrategyGuard.scan()."""

    @staticmethod
    def _make_all_markets(coins: dict) -> list:
        """Build a minimal all_markets structure for given coins.

        coins: {"BTCSWP": 95000, ...}
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
        guard = StrategyGuard(
            target_markets=["BTCSWP-PARA"],
            enabled=True,
        )
        assert MARKET_STRATEGY_MAP["BTCSWP-PARA"] == ["cfi_hedge"]
        assert len(guard.strategies) == 0
        assert len(guard._strategy_cache) == 0

    def test_routed_scan_with_market_data(self):
        guard = StrategyGuard(
            target_markets=["BTCSWP-PARA"],
            enabled=True,
        )
        all_markets = self._make_all_markets({"BTCSWP": 95000})
        signals = guard.scan(all_markets=all_markets, target_markets=["BTCSWP-PARA"])

        assert len(guard._strategy_cache) > 0
        for sig in signals:
            assert sig["asset"] == "BTCSWP"
            assert "strategy:" in sig["source"]

    def test_routed_scan_skips_unmapped_markets(self):
        guard = StrategyGuard(
            target_markets=["ETH-PERP"],
            enabled=True,
        )
        all_markets = self._make_all_markets({"ETH": 3500})
        signals = guard.scan(all_markets=all_markets, target_markets=["ETH-PERP"])
        assert signals == []

    def test_legacy_scan_still_works(self):
        guard = StrategyGuard(
            strategy_names=["cfi_hedge"],
            enabled=True,
        )
        all_markets = self._make_all_markets({"BTC": 95000})
        signals = guard.scan(all_markets=all_markets)
        assert isinstance(signals, list)

    def test_disabled_guard_returns_empty(self):
        guard = StrategyGuard(
            target_markets=["BTCSWP-PARA"],
            enabled=False,
        )
        all_markets = self._make_all_markets({"BTCSWP": 95000})
        signals = guard.scan(all_markets=all_markets)
        assert signals == []

    def test_find_snapshot_by_coin_prefix(self):
        snapshots = {
            "BTCSWP": MarketSnapshot(
                instrument="BTCSWP-PARA",
                mid_price=95000.0,
                bid=94999.0,
                ask=95001.0,
                spread_bps=0.2,
                timestamp_ms=int(time.time() * 1000),
            ),
        }
        snap = StrategyGuard._find_snapshot(snapshots, "BTCSWP-PARA")
        assert snap is not None
        assert snap.mid_price == 95000.0

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
        snap = StrategyGuard._find_snapshot(snapshots, "BTCSWP-PARA")
        assert snap is None

    def test_strategy_cache_reuse(self):
        guard = StrategyGuard(target_markets=["BTCSWP-PARA"], enabled=True)
        s1 = guard._get_or_load("cfi_hedge")
        s2 = guard._get_or_load("cfi_hedge")
        assert s1 is s2


class TestApexConfigAllowedInstruments:
    def test_allowed_instruments_default_empty(self):
        from modules.apex_config import ApexConfig
        cfg = ApexConfig()
        assert cfg.allowed_instruments == []

    def test_allowed_instruments_from_dict(self):
        from modules.apex_config import ApexConfig
        cfg = ApexConfig.from_dict({"allowed_instruments": ["BTCSWP-PARA"]})
        assert cfg.allowed_instruments == ["BTCSWP-PARA"]
