"""Standalone radar runner — tick loop that fetches data and runs scans."""
from __future__ import annotations

import skills._bootstrap  # noqa: F401 — auto-setup sys.path

import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from modules.radar_config import RadarConfig
from modules.radar_guard import RadarGuard
from modules.radar_state import RadarResult

log = logging.getLogger("radar_runner")


class RadarRunner:
    """Autonomous radar tick loop.

    Each tick:
      1. Fetch all markets from HL
      2. Bulk screen + select top N
      3. Fetch candles for BTC + top assets (parallel)
      4. Run guard.scan()
      5. Print formatted output
    """

    def __init__(
        self,
        hl,
        config: Optional[RadarConfig] = None,
        tick_interval: float = 900.0,  # 15 min default
        json_output: bool = False,
        data_dir: str = "data/radar",
    ):
        self.hl = hl
        self.config = config or RadarConfig()
        self.tick_interval = tick_interval
        self.json_output = json_output
        self.guard = RadarGuard(
            config=self.config,
            history_store=None,  # uses default path
        )
        self.guard.history.path = f"{data_dir}/scan-history.json"
        self._running = False
        self.scan_count = 0

    def run(self, max_scans: int = 0) -> None:
        """Main loop. Blocks until max_scans reached or SIGINT."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        log.info("Radar started: tick=%.0fs top_n=%d min_vol=%.0f",
                 self.tick_interval, self.config.top_n_deep,
                 self.config.min_volume_24h)

        while self._running:
            if max_scans > 0 and self.scan_count >= max_scans:
                log.info("Reached max scans (%d), stopping", max_scans)
                break

            try:
                result = self._scan_tick()
                self.scan_count += 1
                self._print_result(result)
            except Exception as e:
                log.error("Scan %d failed: %s", self.scan_count + 1, e, exc_info=True)

            if self._running and self.tick_interval > 0 and (max_scans == 0 or self.scan_count < max_scans):
                time.sleep(self.tick_interval)

        log.info("Radar stopped after %d scans", self.scan_count)

    def run_once(self) -> RadarResult:
        """Single scan pass — no loop."""
        result = self._scan_tick()
        self.scan_count = 1
        self._print_result(result)
        return result

    def _scan_tick(self) -> RadarResult:
        """Execute a single scan cycle."""
        # 1. Fetch all markets
        all_markets = self.hl.get_all_markets()

        # 2. Pre-screen to find which assets need candle data
        from modules.radar_engine import OpportunityRadarEngine
        temp_engine = OpportunityRadarEngine(self.config)
        assets = temp_engine._bulk_screen(all_markets)
        top_assets = temp_engine._select_top(assets)
        asset_names = [a.name for a in top_assets]

        # 3. Fetch candles in parallel
        btc_candles_4h = []
        btc_candles_1h = []
        asset_candles: Dict[str, Dict[str, list]] = {}

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {}

            # BTC candles
            futures[pool.submit(
                self.hl.get_candles, "BTC", "4h", self.config.lookback_4h_ms,
            )] = ("_btc", "4h")
            futures[pool.submit(
                self.hl.get_candles, "BTC", "1h", self.config.lookback_1h_ms,
            )] = ("_btc", "1h")

            # Asset candles
            for name in asset_names:
                for interval, lookback in [
                    ("4h", self.config.lookback_4h_ms),
                    ("1h", self.config.lookback_1h_ms),
                    ("15m", self.config.lookback_15m_ms),
                ]:
                    futures[pool.submit(
                        self.hl.get_candles, name, interval, lookback,
                    )] = (name, interval)

            for future in as_completed(futures):
                key = futures[future]
                try:
                    data = future.result()
                    if key[0] == "_btc":
                        if key[1] == "4h":
                            btc_candles_4h = data
                        else:
                            btc_candles_1h = data
                    else:
                        asset_name, tf = key
                        if asset_name not in asset_candles:
                            asset_candles[asset_name] = {}
                        asset_candles[asset_name][tf] = data
                except Exception as e:
                    log.warning("Failed to fetch candles for %s %s: %s",
                                key[0], key[1], e)

        # 4. Run guard (engine + persistence)
        result = self.guard.scan(
            all_markets=all_markets,
            btc_candles_4h=btc_candles_4h,
            btc_candles_1h=btc_candles_1h,
            asset_candles=asset_candles,
        )

        return result

    def _print_result(self, result: RadarResult) -> None:
        """Print scan results."""
        if self.json_output:
            import json
            print(json.dumps(result.to_dict(), indent=2))
            return

        stats = result.stats
        print(f"\n{'='*70}")
        print(f"SCAN #{self.scan_count}  |  BTC: {result.btc_macro.get('trend', '?')} "
              f"(EMA diff: {result.btc_macro.get('diff_pct', 0):.2f}%)")
        print(f"Assets: {stats.get('assets_scanned', 0)} → "
              f"{stats.get('passed_stage1', 0)} stage1 → "
              f"{stats.get('deep_dived', 0)} deep → "
              f"{stats.get('qualified', 0)} qualified  "
              f"({stats.get('scan_duration_ms', 0)}ms)")
        print(f"{'='*70}")

        if not result.opportunities:
            print("No qualifying opportunities found.")
            return

        print(f"{'Rank':<5} {'Dir':<6} {'Asset':<8} {'Score':<7} {'Raw':<6} "
              f"{'Macro':<6} {'MS':<5} {'TEC':<5} {'FND':<5} {'RSI':<5} "
              f"{'Vol$':<10} {'Streak':<6}")
        print("-" * 80)

        for i, opp in enumerate(result.opportunities[:10], 1):
            vol_str = _format_volume(opp.market_data.get("vol24h", 0))
            streak = opp.momentum.get("scan_streak", 0)
            print(f"{i:<5} {opp.direction:<6} {opp.asset:<8} "
                  f"{opp.final_score:<7.0f} {opp.raw_score:<6.0f} "
                  f"{opp.macro_modifier:+<5.0f} "
                  f"{opp.pillar_scores.get('market_structure', 0):<5.0f} "
                  f"{opp.pillar_scores.get('technicals', 0):<5.0f} "
                  f"{opp.pillar_scores.get('funding', 0):<5.0f} "
                  f"{opp.technicals.get('rsi1h', 50):<5.0f} "
                  f"{vol_str:<10} {streak:<6}")

            if opp.risks:
                print(f"      Risks: {', '.join(opp.risks)}")

        print()

    def _handle_shutdown(self, signum, frame):
        log.info("Shutdown signal received")
        self._running = False


def _format_volume(vol: float) -> str:
    if vol >= 1e9:
        return f"${vol/1e9:.1f}B"
    if vol >= 1e6:
        return f"${vol/1e6:.1f}M"
    if vol >= 1e3:
        return f"${vol/1e3:.0f}K"
    return f"${vol:.0f}"
