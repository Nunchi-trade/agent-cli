"""WOLF strategy configuration — budget, slots, risk, and presets."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WolfConfig:
    """Configuration for WOLF autonomous trading strategy."""

    # Budget & Position Management
    total_budget: float = 10_000.0
    max_slots: int = 3
    leverage: float = 10.0
    margin_per_slot: float = 0.0  # auto-computed

    # Entry thresholds
    radar_score_threshold: int = 170
    pulse_immediate_auto_entry: bool = True
    pulse_confidence_threshold: float = 70.0

    # Exit parameters
    conviction_collapse_minutes: int = 30
    stagnation_minutes: int = 60
    stagnation_min_roe: float = 3.0
    max_negative_roe: float = -5.0

    # Risk
    daily_loss_limit: float = 500.0
    max_same_direction: int = 2

    # Guard preset for position guards
    guard_preset: str = "tight"
    guard_leverage_override: Optional[float] = None

    # Tick schedule
    tick_interval_s: float = 60.0
    radar_interval_ticks: int = 15
    watchdog_interval_ticks: int = 5

    # REFLECT self-improvement
    reflect_interval_ticks: int = 240        # Run REFLECT every 4 hours (at 60s ticks)
    reflect_min_round_trips: int = 5         # Min trades before applying adjustments
    reflect_auto_adjust: bool = True         # Auto-adjust params from REFLECT findings

    # Scheduled tasks (UTC hours)
    daily_reset_hour: int = 0             # UTC hour for daily PnL reset
    reflect_report_hour: int = 4             # UTC hour for comprehensive REFLECT report

    # Nightly review
    nightly_review_hour: int = 2          # UTC hour for nightly review
    nightly_review_enabled: bool = True   # Enable/disable nightly review

    # Obsidian integration
    obsidian_vault_path: str = ""         # Path to Obsidian vault (empty = disabled)
    obsidian_scan_interval_ticks: int = 60  # Re-scan vault every hour

    # TWAP execution (Aster-inspired)
    twap_threshold_usd: float = 5000.0       # Use TWAP for entries above this notional
    twap_duration_ticks: int = 5             # Spread entry over N ticks
    twap_urgency: float = 0.7               # 0.0 (passive) to 1.0 (aggressive)

    # Portfolio risk (Aster-inspired)
    portfolio_risk_enabled: bool = True
    portfolio_max_correlated: int = 2
    portfolio_max_same_direction: int = 3
    portfolio_margin_warn: float = 0.7
    portfolio_margin_block: float = 0.9

    # Smart money tracking (Aster-inspired)
    smart_money_enabled: bool = False
    smart_money_addresses: List[str] = field(default_factory=list)
    smart_money_min_position_usd: float = 10_000.0
    smart_money_conviction_threshold: int = 2
    smart_money_poll_interval_ticks: int = 5

    # Instrument filters
    excluded_instruments: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.margin_per_slot == 0.0:
            self.margin_per_slot = self.total_budget / max(self.max_slots, 1)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WolfConfig":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @classmethod
    def from_yaml(cls, path: str) -> "WolfConfig":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data.get("wolf", data))

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


WOLF_PRESETS: Dict[str, WolfConfig] = {
    "default": WolfConfig(),
    "conservative": WolfConfig(
        max_slots=2,
        leverage=5.0,
        radar_score_threshold=190,
        pulse_confidence_threshold=80.0,
        daily_loss_limit=250.0,
    ),
    "aggressive": WolfConfig(
        max_slots=3,
        leverage=15.0,
        radar_score_threshold=150,
        pulse_confidence_threshold=60.0,
        daily_loss_limit=1000.0,
    ),
}
