"""RadarGuard — bridge between pure engine, persistence, and logging."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from modules.radar_config import RadarConfig
from modules.radar_engine import OpportunityRadarEngine
from modules.radar_state import RadarHistoryStore, RadarResult

log = logging.getLogger("radar_guard")


class RadarGuard:
    """Owns engine + history store + logging.

    Wraps the pure engine with persistence and human-readable output.
    """

    def __init__(
        self,
        config: Optional[RadarConfig] = None,
        history_store: Optional[RadarHistoryStore] = None,
    ):
        self.config = config or RadarConfig()
        self.engine = OpportunityRadarEngine(self.config)
        self.history = history_store or RadarHistoryStore(
            max_size=self.config.scan_history_size,
        )
        self.last_result: Optional[RadarResult] = None

    def scan(
        self,
        all_markets: list,
        btc_candles_4h: List[Dict],
        btc_candles_1h: List[Dict],
        asset_candles: Dict[str, Dict[str, List[Dict]]],
    ) -> RadarResult:
        """Run a full scan, persist results, and log summary."""
        # Load history for momentum
        scan_history = self.history.get_history()

        # Run pure engine
        result = self.engine.scan(
            all_markets=all_markets,
            btc_candles_4h=btc_candles_4h,
            btc_candles_1h=btc_candles_1h,
            asset_candles=asset_candles,
            scan_history=scan_history,
        )

        # Persist
        self.history.save_scan(result)
        self.last_result = result

        # Log summary
        stats = result.stats
        log.info(
            "Scan complete: %d assets → %d stage1 → %d deep → %d qualified "
            "(%.1fs, BTC %s)",
            stats.get("assets_scanned", 0),
            stats.get("passed_stage1", 0),
            stats.get("deep_dived", 0),
            stats.get("qualified", 0),
            stats.get("scan_duration_ms", 0) / 1000,
            result.btc_macro.get("trend", "?"),
        )

        for opp in result.opportunities[:5]:
            log.info(
                "  %s %s: score=%d (raw=%d + macro=%+d) "
                "MS=%.0f TEC=%.0f FND=%.0f | RSI=%.0f",
                opp.direction, opp.asset, opp.final_score,
                opp.raw_score, opp.macro_modifier,
                opp.pillar_scores.get("market_structure", 0),
                opp.pillar_scores.get("technicals", 0),
                opp.pillar_scores.get("funding", 0),
                opp.technicals.get("rsi1h", 50),
            )

        return result
