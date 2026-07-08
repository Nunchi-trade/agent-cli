"""PulseEngine — pure, stateless detector (zero I/O).

Detects assets with sudden capital inflow using OI delta, volume surge,
funding shifts, and price breakouts as proxy signals for smart money flow.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from modules.pulse_config import PulseConfig
from modules.pulse_state import AssetSnapshot, PulseResult, PulseSignal


class PulseEngine:
    """Stateless Pulse detection engine. Zero I/O."""

    # Tier name → tier number mapping
    TIER_MAP = {
        "FIRST_JUMP": 1,
        "CONTRIB_EXPLOSION": 2,
        "IMMEDIATE_MOVER": 3,
        "NEW_ENTRY_DEEP": 4,
        "DEEP_CLIMBER": 5,
    }

    def __init__(self, config: Optional[PulseConfig] = None):
        self.config = config or PulseConfig()
        # Track which sectors already have a FIRST_JUMP winner per scan cycle
        self._sector_first_jump: Dict[str, str] = {}  # sector → first asset

    def scan(
        self,
        all_markets: list,
        asset_candles: Dict[str, Dict[str, List[Dict]]],
        scan_history: List[Dict],
    ) -> PulseResult:
        """Run full Pulse detection pipeline.

        Args:
            all_markets: [meta_dict, asset_ctxs_list] from HL API
            asset_candles: {asset: {"1h": [...]}} for breakout detection
            scan_history: list of previous PulseResult dicts
        """
        start_ms = int(time.time() * 1000)
        cfg = self.config

        # Reset per-scan sector tracking for FIRST_JUMP
        self._sector_first_jump = {}

        # 1. Parse current market snapshots
        snapshots = self._parse_markets(all_markets, start_ms)

        # 2. Filter by volume minimum
        qualifying = [s for s in snapshots if s.volume_24h >= cfg.volume_min_24h]

        # 3. Check if we have enough history
        has_baseline = len(scan_history) >= cfg.min_scans_for_signal

        signals: List[PulseSignal] = []
        if has_baseline:
            for snap in qualifying:
                signal = self._detect_signals(snap, asset_candles, scan_history)
                if signal:
                    signals.append(signal)

        # 4. Sort by confidence
        signals.sort(key=lambda s: s.confidence, reverse=True)

        return PulseResult(
            scan_time_ms=start_ms,
            signals=signals,
            snapshots=snapshots,
            stats={
                "total_assets": len(snapshots),
                "qualifying": len(qualifying),
                "signals_detected": len(signals),
                "has_baseline": has_baseline,
                "history_depth": len(scan_history),
                "scan_duration_ms": int(time.time() * 1000) - start_ms,
            },
        )

    def _parse_markets(self, all_markets: list, now_ms: int) -> List[AssetSnapshot]:
        """Extract asset snapshots from HL all_markets data."""
        if len(all_markets) < 2:
            return []

        universe = all_markets[0].get("universe", [])
        ctxs = all_markets[1]
        snapshots = []

        for i, ctx in enumerate(ctxs):
            if i >= len(universe):
                break
            try:
                asset_name = universe[i].get("name", "")
            except (IndexError, AttributeError):
                continue
            snapshots.append(AssetSnapshot(
                asset=asset_name,
                timestamp_ms=now_ms,
                open_interest=float(ctx.get("openInterest", 0)),
                volume_24h=float(ctx.get("dayNtlVlm", 0)),
                funding_rate=float(ctx.get("funding", 0)),
                mark_price=float(ctx.get("markPx", 0)),
            ))

        return snapshots

    def _detect_signals(
        self,
        snap: AssetSnapshot,
        asset_candles: Dict[str, Dict[str, List[Dict]]],
        scan_history: List[Dict],
    ) -> Optional[PulseSignal]:
        """Detect signals for a single asset. Returns best signal or None."""
        cfg = self.config

        # Compute baselines
        from modules.pulse_state import PulseHistoryStore
        store = PulseHistoryStore.__new__(PulseHistoryStore)
        oi_baseline = store.get_asset_oi_baseline(snap.asset, scan_history, cfg.oi_baseline_window)
        funding_history = store.get_asset_funding_history(snap.asset, scan_history, 3)

        # Check erratic behavior
        is_erratic = self._is_erratic(snap.asset, scan_history)

        # Detect individual signals
        oi_signal = self._detect_oi_delta(snap, oi_baseline)
        vol_signal = self._detect_volume_surge(snap, asset_candles.get(snap.asset, {}))
        funding_signal = self._detect_funding_flip(snap, funding_history)
        breakout_signal = self._detect_price_breakout(
            snap, asset_candles.get(snap.asset, {}).get("1h", []),
        )

        # No signals detected
        if not any([oi_signal, vol_signal, funding_signal, breakout_signal]):
            return None

        # Classify signal type
        signal_type = self._classify_signal_type(oi_signal, vol_signal, funding_signal, breakout_signal)

        # Classify direction
        direction = self._classify_direction(oi_signal, vol_signal, funding_signal, breakout_signal, snap)

        # Compute confidence
        confidence = cfg.signal_weights.get(signal_type, 50.0)

        # Erratic penalty
        if is_erratic:
            confidence *= 0.5

        # --- 5-tier entry classification ---
        oi_delta_pct = oi_signal.get("delta_pct", 0) if oi_signal else 0
        vol_ratio = vol_signal.get("surge_ratio", 0) if vol_signal else 0
        signal_tier = self._classify_tier(
            snap, oi_delta_pct, vol_ratio, scan_history,
        )

        return PulseSignal(
            asset=snap.asset,
            signal_type=signal_type,
            direction=direction,
            confidence=round(confidence, 1),
            oi_delta_pct=round(oi_delta_pct, 2),
            volume_surge_ratio=round(vol_ratio, 2),
            funding_shift=round(funding_signal.get("shift", 0), 6) if funding_signal else 0,
            price_change_pct=round(breakout_signal.get("breakout_pct", 0), 2) if breakout_signal else 0,
            details={
                "oi_signal": bool(oi_signal),
                "vol_signal": bool(vol_signal),
                "funding_signal": bool(funding_signal),
                "breakout_signal": bool(breakout_signal),
                "oi_baseline": round(oi_baseline, 2) if oi_baseline else None,
                "mark_price": snap.mark_price,
                "volume_24h": snap.volume_24h,
            },
            is_erratic=is_erratic,
            signal_tier=signal_tier,
        )

    def _detect_oi_delta(
        self, snap: AssetSnapshot, baseline_oi: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        """Detect OI delta above threshold."""
        if baseline_oi is None or baseline_oi <= 0:
            return None
        delta_pct = (snap.open_interest - baseline_oi) / baseline_oi * 100
        if delta_pct >= self.config.oi_delta_breakout_pct:
            return {"delta_pct": delta_pct, "current_oi": snap.open_interest, "baseline_oi": baseline_oi}
        return None

    def _detect_volume_surge(
        self, snap: AssetSnapshot, candles: Dict[str, List[Dict]],
    ) -> Optional[Dict[str, Any]]:
        """Detect volume surge — recent 4h volume vs 24h average quarter."""
        candles_4h = candles.get("4h", [])
        if not candles_4h or snap.volume_24h <= 0:
            # Fallback: use 24h volume relative to a baseline
            return None

        # Recent 4h volume from last candle
        recent_vol = float(candles_4h[-1].get("v", 0)) if candles_4h else 0
        # Average 4h volume = 24h volume / 6
        avg_4h_vol = snap.volume_24h / 6

        if avg_4h_vol <= 0:
            return None

        surge_ratio = recent_vol / avg_4h_vol
        if surge_ratio >= self.config.volume_surge_ratio:
            return {"surge_ratio": surge_ratio, "recent_vol": recent_vol, "avg_4h_vol": avg_4h_vol}
        return None

    def _detect_funding_flip(
        self, snap: AssetSnapshot, funding_history: List[float],
    ) -> Optional[Dict[str, Any]]:
        """Detect funding rate reversal or acceleration."""
        if not funding_history:
            return None

        prev_rate = funding_history[-1]
        curr_rate = snap.funding_rate

        # Direction flip
        if prev_rate != 0 and curr_rate != 0:
            if (prev_rate > 0 and curr_rate < 0) or (prev_rate < 0 and curr_rate > 0):
                shift = curr_rate - prev_rate
                if abs(shift) >= self.config.funding_flip_threshold:
                    return {"shift": shift, "prev": prev_rate, "current": curr_rate, "type": "flip"}

        # Acceleration (same direction, magnitude increase)
        if prev_rate != 0:
            change_pct = abs((curr_rate - prev_rate) / prev_rate) * 100
            if change_pct >= self.config.funding_acceleration_pct and abs(curr_rate) > abs(prev_rate):
                shift = curr_rate - prev_rate
                return {"shift": shift, "prev": prev_rate, "current": curr_rate,
                        "type": "acceleration", "change_pct": change_pct}

        return None

    def _detect_price_breakout(
        self, snap: AssetSnapshot, candles_1h: List[Dict],
    ) -> Optional[Dict[str, Any]]:
        """Detect price breakout from recent range."""
        if len(candles_1h) < self.config.breakout_lookback_bars:
            return None

        lookback = candles_1h[-self.config.breakout_lookback_bars:]
        highs = [float(c["h"]) for c in lookback]
        lows = [float(c["l"]) for c in lookback]

        range_high = max(highs[:-1]) if len(highs) > 1 else highs[0]
        range_low = min(lows[:-1]) if len(lows) > 1 else lows[0]

        if range_high <= 0:
            return None

        current = snap.mark_price
        exceed_pct = self.config.breakout_exceed_pct

        # Breakout above range
        if current > range_high:
            breakout_pct = (current - range_high) / range_high * 100
            if breakout_pct >= exceed_pct:
                return {"breakout_pct": breakout_pct, "direction": "up",
                        "range_high": range_high, "range_low": range_low}

        # Breakout below range
        if current < range_low:
            breakout_pct = (range_low - current) / range_low * 100
            if breakout_pct >= exceed_pct:
                return {"breakout_pct": -breakout_pct, "direction": "down",
                        "range_high": range_high, "range_low": range_low}

        return None

    def _classify_signal_type(
        self,
        oi_signal: Optional[Dict],
        vol_signal: Optional[Dict],
        funding_signal: Optional[Dict],
        breakout_signal: Optional[Dict],
    ) -> str:
        """Classify the signal type based on which detectors fired."""
        # IMMEDIATE_MOVER: both extreme OI AND extreme volume
        if oi_signal and vol_signal:
            oi_pct = oi_signal.get("delta_pct", 0)
            vol_ratio = vol_signal.get("surge_ratio", 0)
            if oi_pct >= self.config.oi_delta_immediate_pct and vol_ratio >= self.config.volume_surge_immediate:
                return "IMMEDIATE_MOVER"

        # Individual signals by priority
        if oi_signal and oi_signal.get("delta_pct", 0) >= self.config.oi_delta_breakout_pct:
            return "OI_BREAKOUT"
        if vol_signal:
            return "VOLUME_SURGE"
        if funding_signal:
            return "FUNDING_FLIP"
        return "OI_BREAKOUT"  # fallback

    def _classify_direction(
        self,
        oi_signal: Optional[Dict],
        vol_signal: Optional[Dict],
        funding_signal: Optional[Dict],
        breakout_signal: Optional[Dict],
        snap: AssetSnapshot,
    ) -> str:
        """Classify LONG or SHORT via majority vote across signals."""
        votes = {"LONG": 0, "SHORT": 0}

        # Funding direction
        if snap.funding_rate > 0:
            votes["LONG"] += 1  # longs paying -> new positions likely long
        elif snap.funding_rate < 0:
            votes["SHORT"] += 1

        # Funding flip direction
        if funding_signal:
            if funding_signal.get("shift", 0) > 0:
                votes["LONG"] += 1  # funding becoming more positive = longs entering
            else:
                votes["SHORT"] += 1

        # Price breakout direction
        if breakout_signal:
            if breakout_signal.get("direction") == "up":
                votes["LONG"] += 1
            else:
                votes["SHORT"] += 1

        # Volume surge + price momentum (use mark vs previous)
        if vol_signal and breakout_signal:
            if breakout_signal.get("direction") == "up":
                votes["LONG"] += 1
            else:
                votes["SHORT"] += 1

        return "LONG" if votes["LONG"] >= votes["SHORT"] else "SHORT"

    def _classify_tier(
        self,
        snap: AssetSnapshot,
        oi_delta_pct: float,
        vol_ratio: float,
        scan_history: List[Dict],
    ) -> int:
        """Classify an asset into the 5-tier signal hierarchy.

        Returns tier number (1-5), or 0 if no tier matches.
        Highest matching tier wins (lowest number = highest priority).
        """
        cfg = self.config

        # Tier 1: FIRST_JUMP — first asset in its sector to show OI+volume breakout
        sector = cfg.sector_map.get(snap.asset, "")
        if sector and oi_delta_pct >= cfg.oi_delta_breakout_pct and vol_ratio >= cfg.volume_surge_ratio:
            if sector not in self._sector_first_jump:
                self._sector_first_jump[sector] = snap.asset
                return 1  # FIRST_JUMP

        # Tier 2: CONTRIB_EXPLOSION — simultaneous extreme OI AND volume
        if (oi_delta_pct >= cfg.contrib_explosion_oi_pct
                and vol_ratio >= cfg.contrib_explosion_vol_mult):
            return 2  # CONTRIB_EXPLOSION

        # Tier 3: IMMEDIATE_MOVER — either extreme OI OR extreme volume
        if (oi_delta_pct >= cfg.oi_delta_immediate_pct
                or vol_ratio >= cfg.volume_surge_immediate):
            return 3  # IMMEDIATE_MOVER

        # Tier 4: NEW_ENTRY_DEEP — OI grows but volume is low (smart money accumulation)
        if (oi_delta_pct >= cfg.new_entry_deep_oi_pct
                and 0 < vol_ratio <= cfg.new_entry_deep_max_vol_mult):
            return 4  # NEW_ENTRY_DEEP

        # Tier 5: DEEP_CLIMBER — sustained OI climb over 3+ consecutive scan windows
        if self._check_deep_climber(snap.asset, scan_history):
            return 5  # DEEP_CLIMBER

        return 0  # Unclassified

    def _check_deep_climber(self, asset: str, scan_history: List[Dict]) -> bool:
        """Check if asset has sustained OI climb over N consecutive windows."""
        cfg = self.config
        min_windows = cfg.deep_climber_min_windows
        min_oi_pct = cfg.deep_climber_min_oi_pct

        if len(scan_history) < min_windows:
            return False

        recent = scan_history[-min_windows:]
        consecutive_climbs = 0

        for i in range(1, len(recent)):
            prev_oi = self._get_asset_oi(asset, recent[i - 1])
            curr_oi = self._get_asset_oi(asset, recent[i])
            if prev_oi and prev_oi > 0 and curr_oi:
                pct_change = (curr_oi - prev_oi) / prev_oi * 100
                if pct_change >= min_oi_pct:
                    consecutive_climbs += 1
                else:
                    consecutive_climbs = 0

        return consecutive_climbs >= min_windows - 1

    def _get_asset_oi(self, asset: str, scan: Dict) -> Optional[float]:
        """Extract OI for an asset from a historical scan dict."""
        for snap in scan.get("snapshots", []):
            if snap.get("asset") == asset:
                return snap.get("open_interest", 0)
        return None

    def _is_erratic(self, asset: str, scan_history: List[Dict]) -> bool:
        """Check if asset has erratic OI rank behavior (bouncing)."""
        if len(scan_history) < self.config.erratic_window:
            return False

        # Collect OI rank changes
        ranks = []
        recent = scan_history[-self.config.erratic_window:]
        for scan in recent:
            # Sort snapshots by OI to compute rank
            snaps = scan.get("snapshots", [])
            sorted_by_oi = sorted(snaps, key=lambda s: s.get("open_interest", 0), reverse=True)
            for rank, s in enumerate(sorted_by_oi):
                if s.get("asset") == asset:
                    ranks.append(rank)
                    break

        if len(ranks) < 3:
            return False

        # Count direction reversals
        reversals = 0
        for i in range(2, len(ranks)):
            prev_dir = ranks[i - 1] - ranks[i - 2]
            curr_dir = ranks[i] - ranks[i - 1]
            if prev_dir != 0 and curr_dir != 0 and (prev_dir > 0) != (curr_dir > 0):
                reversals += 1

        return reversals >= self.config.erratic_max_reversals
