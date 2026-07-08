"""Pulse state models — signals, scan results, and history persistence."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PulseSignal:
    """A detected Pulse signal."""
    asset: str
    signal_type: str            # IMMEDIATE_MOVER, VOLUME_SURGE, OI_BREAKOUT, FUNDING_FLIP
    direction: str              # "LONG" or "SHORT"
    confidence: float           # 0-100
    oi_delta_pct: float = 0.0
    volume_surge_ratio: float = 0.0
    funding_shift: float = 0.0
    price_change_pct: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    is_erratic: bool = False
    signal_tier: int = 0              # 0=unclassified, 1=FIRST_JUMP … 5=DEEP_CLIMBER


@dataclass
class AssetSnapshot:
    """Point-in-time snapshot of one asset's key metrics."""
    asset: str
    timestamp_ms: int = 0
    open_interest: float = 0.0
    volume_24h: float = 0.0
    funding_rate: float = 0.0
    mark_price: float = 0.0


@dataclass
class PulseResult:
    """Complete result of a single Pulse scan."""
    scan_time_ms: int
    signals: List[PulseSignal] = field(default_factory=list)
    snapshots: List[AssetSnapshot] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_time_ms": self.scan_time_ms,
            "signals": [asdict(s) for s in self.signals],
            "snapshots": [asdict(m) for m in self.snapshots],
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PulseResult":
        return cls(
            scan_time_ms=d.get("scan_time_ms", 0),
            signals=[PulseSignal(**s) for s in d.get("signals", [])],
            snapshots=[AssetSnapshot(**m) for m in d.get("snapshots", [])],
            stats=d.get("stats", {}),
        )


class PulseHistoryStore:
    """Persists scan history for cross-scan comparison."""

    def __init__(self, path: str = "data/pulse/scan-history.json", max_size: int = 30):
        self.path = path
        self.max_size = max_size

    def _ensure_dir(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def save_scan(self, result: PulseResult) -> None:
        history = self.get_history()
        history.append(result.to_dict())
        history = history[-self.max_size:]
        self._ensure_dir()
        with open(self.path, "w") as f:
            json.dump(history, f, indent=2)

    def get_history(self) -> List[Dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def get_asset_oi_baseline(self, asset: str, history: List[Dict], window: int = 6) -> Optional[float]:
        """Compute average OI for an asset from recent scan history."""
        oi_values = []
        for scan in history[-window:]:
            for snap in scan.get("snapshots", []):
                if snap.get("asset") == asset:
                    oi_values.append(snap.get("open_interest", 0))
                    break
        if not oi_values:
            return None
        return sum(oi_values) / len(oi_values)

    def get_asset_funding_history(self, asset: str, history: List[Dict], window: int = 3) -> List[float]:
        """Get recent funding rates for an asset."""
        rates = []
        for scan in history[-window:]:
            for snap in scan.get("snapshots", []):
                if snap.get("asset") == asset:
                    rates.append(snap.get("funding_rate", 0))
                    break
        return rates
