"""Radar configuration — pillar weights, thresholds, and presets."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class RadarConfig:
    """Configuration for the opportunity radar."""

    # Screening thresholds
    min_volume_24h: float = 500_000.0
    top_n_deep: int = 20
    score_threshold: int = 150

    # Pillar weights (must sum to 1.0)
    pillar_weights: Dict[str, float] = field(default_factory=lambda: {
        "market_structure": 0.35,
        "technicals": 0.40,
        "funding": 0.25,
    })

    # BTC macro modifiers per direction at each macro state
    macro_modifiers: Dict[str, Dict[str, int]] = field(default_factory=lambda: {
        "strong_up":   {"LONG": 30, "SHORT": -30},
        "up":          {"LONG": 15, "SHORT": -15},
        "neutral":     {"LONG": 0,  "SHORT": 0},
        "down":        {"LONG": -15, "SHORT": 15},
        "strong_down": {"LONG": -30, "SHORT": 30},
    })

    # Hard disqualifier thresholds
    disqualify_thresholds: Dict[str, Any] = field(default_factory=lambda: {
        "counter_trend_4h_strength": 50,
        "extreme_rsi_long": 80,
        "extreme_rsi_short": 20,
        "volume_dying_ratio": 0.5,
        "heavy_funding_annualized_pct": 50.0,
        "btc_headwind_modifier": -30,
    })

    # Scan history for cross-scan momentum
    scan_history_size: int = 12

    # Candle lookbacks (in milliseconds)
    lookback_4h_ms: int = 14_400_000 * 50   # ~50 4h candles
    lookback_1h_ms: int = 3_600_000 * 48     # 48 1h candles
    lookback_15m_ms: int = 900_000 * 48      # 48 15m candles

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RadarConfig":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @classmethod
    def from_yaml(cls, path: str) -> "RadarConfig":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_volume_24h": self.min_volume_24h,
            "top_n_deep": self.top_n_deep,
            "score_threshold": self.score_threshold,
            "pillar_weights": dict(self.pillar_weights),
            "macro_modifiers": {k: dict(v) for k, v in self.macro_modifiers.items()},
            "disqualify_thresholds": dict(self.disqualify_thresholds),
            "scan_history_size": self.scan_history_size,
            "lookback_4h_ms": self.lookback_4h_ms,
            "lookback_1h_ms": self.lookback_1h_ms,
            "lookback_15m_ms": self.lookback_15m_ms,
        }


RADAR_PRESETS: Dict[str, RadarConfig] = {
    "default": RadarConfig(),
    "aggressive": RadarConfig(
        min_volume_24h=100_000.0,
        top_n_deep=30,
        score_threshold=120,
    ),
    # Tuned for 3-market yex testnet competition. v2 (2026-04-09): raised
    # score threshold from 80 to 100 to filter out noise-quality scans that
    # were producing losing entries under the v1 cohort run.
    "competition": RadarConfig(
        min_volume_24h=50_000.0,    # was 500_000
        top_n_deep=10,              # was 20 — yex only has 3 markets so 10 is generous
        score_threshold=100,        # v2: was 80 (default 150)
    ),
}
