"""PulseGuard — bridge between pure engine, persistence, and logging."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from modules.pulse_config import PulseConfig
from modules.pulse_engine import PulseEngine
from modules.pulse_state import PulseHistoryStore, PulseResult

log = logging.getLogger("pulse_guard")


class PulseGuard:
    """Owns engine + history store + logging."""

    def __init__(
        self,
        config: Optional[PulseConfig] = None,
        history_store: Optional[PulseHistoryStore] = None,
    ):
        self.config = config or PulseConfig()
        self.engine = PulseEngine(self.config)
        self.history = history_store or PulseHistoryStore(
            max_size=self.config.scan_history_size,
        )
        self.last_result: Optional[PulseResult] = None

    def scan(
        self,
        all_markets: list,
        asset_candles: Dict[str, Dict[str, List[Dict]]],
    ) -> PulseResult:
        """Run scan, persist results, log summary."""
        scan_history = self.history.get_history()

        result = self.engine.scan(
            all_markets=all_markets,
            asset_candles=asset_candles,
            scan_history=scan_history,
        )

        self.history.save_scan(result)
        self.last_result = result

        stats = result.stats
        log.info(
            "Pulse scan: %d assets -> %d qualifying -> %d signals (history=%d)",
            stats.get("total_assets", 0),
            stats.get("qualifying", 0),
            stats.get("signals_detected", 0),
            stats.get("history_depth", 0),
        )

        for sig in result.signals[:5]:
            erratic_flag = " [ERRATIC]" if sig.is_erratic else ""
            log.info(
                "  %s %s %s conf=%.0f OI=%+.1f%% vol=%.1fx fund=%+.6f%s",
                sig.signal_type, sig.direction, sig.asset,
                sig.confidence, sig.oi_delta_pct,
                sig.volume_surge_ratio, sig.funding_shift, erratic_flag,
            )

        return result
