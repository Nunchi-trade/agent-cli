"""Guard configuration models and presets. Pure data, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Tier:
    """One profit-lock tier: when ROE hits trigger_pct, lock lock_pct as floor."""

    trigger_pct: float          # ROE % to activate (e.g., 10.0)
    lock_pct: float             # ROE % to lock as price floor (e.g., 5.0)
    retrace: Optional[float] = None      # Per-tier retrace override
    max_breaches: Optional[int] = None   # Per-tier breach count override

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "trigger_pct": self.trigger_pct,
            "lock_pct": self.lock_pct,
        }
        if self.retrace is not None:
            d["retrace"] = self.retrace
        if self.max_breaches is not None:
            d["max_breaches"] = self.max_breaches
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Tier:
        return cls(
            trigger_pct=float(data["trigger_pct"]),
            lock_pct=float(data["lock_pct"]),
            retrace=float(data["retrace"]) if data.get("retrace") is not None else None,
            max_breaches=int(data["max_breaches"]) if data.get("max_breaches") is not None else None,
        )


@dataclass
class GuardConfig:
    """Immutable configuration for one DSL guard instance."""

    direction: str = "long"             # "long" or "short"
    leverage: float = 10.0

    # Phase 1: "Let it breathe"
    phase1_retrace: float = 0.03        # 3% retrace from high water
    phase1_max_breaches: int = 3        # Consecutive breaches before close
    phase1_absolute_floor: float = 0.0  # Hard price floor (0 = disabled)
    phase1_max_duration_ms: int = 5_400_000   # 90 min max in Phase 1 (0 = disabled)
    phase1_weak_peak_ms: int = 2_700_000      # 45 min weak-peak check (0 = disabled)
    phase1_weak_peak_min_roe: float = 3.0     # Min peak ROE% to survive weak-peak check

    # Phase 2: "Lock the bag"
    phase2_retrace: float = 0.015       # 1.5% default retrace
    phase2_max_breaches: int = 2        # Default breaches for tiers without override

    # Breach decay
    breach_decay_mode: str = "hard"     # "hard" (reset to 0) or "soft" (decay by 1)

    # Tiers (ordered by trigger_pct ascending)
    tiers: List[Tier] = field(default_factory=list)

    # Stagnation take-profit (DSL-Tight feature)
    stagnation_enabled: bool = False
    stagnation_min_roe: float = 8.0     # Minimum ROE% to trigger
    stagnation_timeout_ms: int = 3_600_000  # 1 hour

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "leverage": self.leverage,
            "phase1_retrace": self.phase1_retrace,
            "phase1_max_breaches": self.phase1_max_breaches,
            "phase1_absolute_floor": self.phase1_absolute_floor,
            "phase1_max_duration_ms": self.phase1_max_duration_ms,
            "phase1_weak_peak_ms": self.phase1_weak_peak_ms,
            "phase1_weak_peak_min_roe": self.phase1_weak_peak_min_roe,
            "phase2_retrace": self.phase2_retrace,
            "phase2_max_breaches": self.phase2_max_breaches,
            "breach_decay_mode": self.breach_decay_mode,
            "tiers": [t.to_dict() for t in self.tiers],
            "stagnation_enabled": self.stagnation_enabled,
            "stagnation_min_roe": self.stagnation_min_roe,
            "stagnation_timeout_ms": self.stagnation_timeout_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GuardConfig:
        tiers = [Tier.from_dict(t) for t in data.get("tiers", [])]
        return cls(
            direction=data.get("direction", "long"),
            leverage=float(data.get("leverage", 10.0)),
            phase1_retrace=float(data.get("phase1_retrace", 0.03)),
            phase1_max_breaches=int(data.get("phase1_max_breaches", 3)),
            phase1_absolute_floor=float(data.get("phase1_absolute_floor", 0.0)),
            phase1_max_duration_ms=int(data.get("phase1_max_duration_ms", 5_400_000)),
            phase1_weak_peak_ms=int(data.get("phase1_weak_peak_ms", 2_700_000)),
            phase1_weak_peak_min_roe=float(data.get("phase1_weak_peak_min_roe", 3.0)),
            phase2_retrace=float(data.get("phase2_retrace", 0.015)),
            phase2_max_breaches=int(data.get("phase2_max_breaches", 2)),
            breach_decay_mode=data.get("breach_decay_mode", "hard"),
            tiers=tiers,
            stagnation_enabled=bool(data.get("stagnation_enabled", False)),
            stagnation_min_roe=float(data.get("stagnation_min_roe", 8.0)),
            stagnation_timeout_ms=int(data.get("stagnation_timeout_ms", 3_600_000)),
        )

    @classmethod
    def from_yaml(cls, path: str) -> GuardConfig:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        guard_data = data.get("guard", data.get("dsl", data))
        return cls.from_dict(guard_data)


# ---- Built-in Presets ----

PRESETS: Dict[str, GuardConfig] = {}


def _register_presets() -> None:
    PRESETS["moderate"] = GuardConfig(
        phase1_retrace=0.03,
        phase1_max_breaches=3,
        phase2_retrace=0.015,
        phase2_max_breaches=2,
        breach_decay_mode="hard",
        tiers=[
            Tier(trigger_pct=10.0, lock_pct=5.0),
            Tier(trigger_pct=20.0, lock_pct=14.0),
            Tier(trigger_pct=30.0, lock_pct=22.0, retrace=0.012),
            Tier(trigger_pct=50.0, lock_pct=40.0, retrace=0.010),
            Tier(trigger_pct=75.0, lock_pct=60.0, retrace=0.008),
            Tier(trigger_pct=100.0, lock_pct=80.0, retrace=0.006),
        ],
    )

    PRESETS["tight"] = GuardConfig(
        phase1_retrace=0.05,
        phase1_max_breaches=3,
        phase2_retrace=0.015,
        phase2_max_breaches=2,
        breach_decay_mode="hard",
        tiers=[
            Tier(trigger_pct=10.0, lock_pct=5.0, max_breaches=3),
            Tier(trigger_pct=20.0, lock_pct=13.0, retrace=0.012, max_breaches=2),
            Tier(trigger_pct=40.0, lock_pct=30.0, retrace=0.010, max_breaches=2),
            Tier(trigger_pct=75.0, lock_pct=64.0, retrace=0.006, max_breaches=1),
        ],
        stagnation_enabled=True,
        stagnation_min_roe=8.0,
        stagnation_timeout_ms=3_600_000,
    )


_register_presets()
