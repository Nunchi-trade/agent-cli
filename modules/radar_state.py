"""Radar state models — scan results, opportunities, and history persistence."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Opportunity:
    """A scored trading opportunity from the radar."""
    asset: str
    direction: str  # "LONG" or "SHORT"
    final_score: float
    raw_score: float
    macro_modifier: float

    pillar_scores: Dict[str, float] = field(default_factory=dict)
    # {market_structure, technicals, funding}

    technicals: Dict[str, Any] = field(default_factory=dict)
    # {rsi1h, rsi15m, hourly_trend, trend_4h, trend_4h_strength, patterns,
    #  vol_ratio_1h, vol_ratio_15m, chg1h, chg4h, chg24h}

    market_data: Dict[str, Any] = field(default_factory=dict)
    # {vol24h, oi, funding_rate, mark_price}

    momentum: Dict[str, Any] = field(default_factory=dict)
    # {score_delta, scan_streak}

    risks: List[str] = field(default_factory=list)


@dataclass
class DisqualifiedAsset:
    """An asset that was disqualified during deep dive."""
    asset: str
    direction: str
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RadarResult:
    """Complete result of a single radar pass."""
    scan_time_ms: int
    btc_macro: Dict[str, Any] = field(default_factory=dict)
    # {trend, strength, ema5, ema13, diff_pct, chg1h, modifiers}

    opportunities: List[Opportunity] = field(default_factory=list)
    disqualified: List[DisqualifiedAsset] = field(default_factory=list)

    stats: Dict[str, Any] = field(default_factory=dict)
    # {assets_scanned, passed_stage1, deep_dived, qualified, disqualified_count, scan_duration_ms}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_time_ms": self.scan_time_ms,
            "btc_macro": self.btc_macro,
            "opportunities": [asdict(o) for o in self.opportunities],
            "disqualified": [asdict(d) for d in self.disqualified],
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RadarResult":
        return cls(
            scan_time_ms=d.get("scan_time_ms", 0),
            btc_macro=d.get("btc_macro", {}),
            opportunities=[Opportunity(**o) for o in d.get("opportunities", [])],
            disqualified=[DisqualifiedAsset(**da) for da in d.get("disqualified", [])],
            stats=d.get("stats", {}),
        )


class RadarHistoryStore:
    """Persists scan history to JSON for cross-scan momentum tracking."""

    def __init__(self, path: str = "data/radar/scan-history.json", max_size: int = 12):
        self.path = path
        self.max_size = max_size

    def _ensure_dir(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def save_scan(self, result: RadarResult) -> None:
        """Append a scan result, trimming history to max_size."""
        history = self.get_history()
        history.append(result.to_dict())
        history = history[-self.max_size:]
        self._ensure_dir()
        with open(self.path, "w") as f:
            json.dump(history, f, indent=2)

    def get_history(self) -> List[Dict]:
        """Load all historical scans."""
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def compute_momentum(self, asset: str, current_score: float) -> Dict[str, Any]:
        """Compute cross-scan momentum for an asset.

        Returns {score_delta, scan_streak}.
        """
        history = self.get_history()
        if not history:
            return {"score_delta": 0.0, "scan_streak": 0}

        # Find asset in previous scans
        prev_scores = []
        streak = 0
        for scan in reversed(history):
            found = False
            for opp in scan.get("opportunities", []):
                if opp["asset"] == asset:
                    prev_scores.append(opp["final_score"])
                    found = True
                    break
            if found:
                streak += 1
            else:
                break

        score_delta = current_score - prev_scores[0] if prev_scores else 0.0
        return {"score_delta": round(score_delta, 1), "scan_streak": streak}
