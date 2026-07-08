"""Standalone Pulse runner — tick loop that detects capital inflow."""
from __future__ import annotations

import skills._bootstrap  # noqa: F401 — auto-setup sys.path

import logging
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional

from modules.pulse_config import PulseConfig
from modules.pulse_guard import PulseGuard
from modules.pulse_state import PulseResult

log = logging.getLogger("pulse_runner")


class PulseRunner:
    """Autonomous Pulse detection tick loop."""

    def __init__(
        self,
        hl,
        config: Optional[PulseConfig] = None,
        tick_interval: float = 60.0,
        json_output: bool = False,
        data_dir: str = "data/pulse",
    ):
        self.hl = hl
        self.config = config or PulseConfig()
        self.tick_interval = tick_interval
        self.json_output = json_output
        self.guard = PulseGuard(config=self.config)
        self.guard.history.path = f"{data_dir}/scan-history.json"
        self._running = False
        self.scan_count = 0

    def run(self, max_scans: int = 0) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        log.info("Pulse detector started: tick=%.0fs min_vol=%.0f",
                 self.tick_interval, self.config.volume_min_24h)

        while self._running:
            if max_scans > 0 and self.scan_count >= max_scans:
                break

            try:
                result = self._scan_tick()
                self.scan_count += 1
                self._print_result(result)
            except Exception as e:
                log.error("Scan %d failed: %s", self.scan_count + 1, e, exc_info=True)

            if self._running and self.tick_interval > 0 and (max_scans == 0 or self.scan_count < max_scans):
                time.sleep(self.tick_interval)

        log.info("Pulse detector stopped after %d scans", self.scan_count)

    def run_once(self) -> PulseResult:
        result = self._scan_tick()
        self.scan_count = 1
        self._print_result(result)
        return result

    def _scan_tick(self) -> PulseResult:
        all_markets = self.hl.get_all_markets()

        # Pre-screen to find assets worth fetching candles for
        from modules.pulse_engine import PulseEngine
        engine = PulseEngine(self.config)
        snapshots = engine._parse_markets(all_markets, int(time.time() * 1000))
        qualifying = [s for s in snapshots if s.volume_24h >= self.config.volume_min_24h]

        # Fetch candles in parallel (only 4h + 1h for qualifying assets)
        asset_candles: Dict[str, Dict[str, list]] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {}
            for snap in qualifying[:30]:  # Cap at 30 to limit API calls
                for interval, lookback in [("4h", 14_400_000 * 6), ("1h", 3_600_000 * 48)]:
                    futures[pool.submit(
                        self.hl.get_candles, snap.asset, interval, lookback,
                    )] = (snap.asset, interval)

            for future in as_completed(futures):
                key = futures[future]
                try:
                    data = future.result()
                    name, tf = key
                    if name not in asset_candles:
                        asset_candles[name] = {}
                    asset_candles[name][tf] = data
                except Exception as e:
                    log.warning("Failed candles for %s %s: %s", key[0], key[1], e)

        return self.guard.scan(all_markets=all_markets, asset_candles=asset_candles)

    def _print_result(self, result: PulseResult) -> None:
        if self.json_output:
            import json
            print(json.dumps(result.to_dict(), indent=2))
            return

        stats = result.stats
        print(f"\n{'='*60}")
        print(f"PULSE #{self.scan_count}  |  "
              f"{stats.get('total_assets', 0)} assets -> "
              f"{stats.get('qualifying', 0)} qualifying -> "
              f"{stats.get('signals_detected', 0)} signals  "
              f"(history={stats.get('history_depth', 0)})")
        print(f"{'='*60}")

        if not result.signals:
            if not stats.get("has_baseline"):
                print(f"Building baseline... ({stats.get('history_depth', 0)}/{self.config.min_scans_for_signal} scans)")
            else:
                print("No capital inflow signals detected.")
            return

        print(f"{'#':<4} {'Type':<18} {'Dir':<6} {'Asset':<8} {'Conf':<6} "
              f"{'OI%':<8} {'VolX':<7} {'Fund':<10}")
        print("-" * 70)

        for i, sig in enumerate(result.signals[:10], 1):
            erratic = " *" if sig.is_erratic else ""
            print(f"{i:<4} {sig.signal_type:<18} {sig.direction:<6} "
                  f"{sig.asset:<8} {sig.confidence:<6.0f} "
                  f"{sig.oi_delta_pct:+<7.1f}% {sig.volume_surge_ratio:<7.1f} "
                  f"{sig.funding_shift:+.6f}{erratic}")
        print()

    def _handle_shutdown(self, signum, frame):
        log.info("Shutdown signal received")
        self._running = False
