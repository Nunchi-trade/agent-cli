"""Per-market configuration model and YAML loader."""
from __future__ import annotations

import os
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field


class FairValueWeights(BaseModel):
    """Weights for the composite fair value calculation."""
    w_oracle: float = 0.50
    w_external: float = 0.00      # Phase 1: stub (external_ref defaults to oracle)
    w_microprice: float = 0.30
    w_inventory: float = 0.20


class SpreadParams(BaseModel):
    """Parameters for the half-spread model."""
    h_fee_bps: float = 1.0        # exchange fee component in bps
    vol_scale: float = 1.0        # scaling factor applied to h_vol
    rebate_credit_bps: float = 0.0
    min_spread_bps: float = 2.0   # floor (full spread)
    max_spread_bps: float = 50.0  # ceiling (full spread)
    growth_mode: bool = False     # HIP-3 growth mode: reduces fees ~90%
    growth_mode_scale: float = 0.1  # fee/rebate scale factor in growth mode


class LadderParams(BaseModel):
    """Parameters for the multi-level ladder."""
    num_levels: int = 3
    delta_bps: float = 1.5        # inter-level spacing in bps
    s0: float = 1.0               # base size at level 0
    lam: float = 0.5              # exponential decay factor for size
    min_size_ratio: float = 0.1   # floor: size >= min_size_ratio * s0 at all levels


class SkewParams(BaseModel):
    """Parameters for inventory skewing."""
    k_inv: float = 0.5            # inventory skew intensity
    inv_limit: float = 10.0       # max allowed inventory
    mode: Literal["price", "size", "both"] = "both"
    size_skew_factor: float = 0.3 # how aggressively to skew sizes
    soft_cap: float = 0.0         # 0 = use inv_limit (backwards compat)
    hard_cap: float = 0.0         # 0 = use inv_limit
    micro_clip_size: float = 0.0  # periodic unwind order size (0=disabled)
    micro_clip_interval: int = 5  # ticks between micro-clips


class FairValueBandConfig(BaseModel):
    """Confidence band clamping for FV around oracle."""
    enabled: bool = False
    band_min_bps: float = 2.0         # minimum band width in bps
    k_sigma: float = 2.0              # sigma multiplier for band
    k_disagree: float = 0.3           # fraction of disagreement allowed in band


class DisagreementConfig(BaseModel):
    """Spread/size adjustment when oracle and external diverge."""
    enabled: bool = False
    threshold_bps: float = 10.0       # bps divergence to trigger
    spread_mult: float = 1.5          # spread multiplier when disagreeing
    size_mult: float = 0.7            # size reduction when disagreeing


class FundingBoundaryConfig(BaseModel):
    """Funding settlement boundary window protection."""
    enabled: bool = False
    pre_window_s: int = 30            # seconds before HH:00
    post_window_s: int = 30           # seconds after HH:00
    size_mult: float = 0.3            # size reduction during boundary
    pin_fv_to_oracle: bool = True     # override FV to oracle mid during boundary


class RegimeOverride(BaseModel):
    """Per-regime parameter overrides. Defaults = no change."""
    spread_mult: float = 1.0
    size_mult: float = 1.0
    num_levels: Optional[int] = None       # override ladder depth
    w_oracle_override: Optional[float] = None  # override FV oracle weight
    reduce_only: bool = False


class SessionRegimeConfig(BaseModel):
    """Session-based spread regime (e.g., VXXN wider off-hours)."""
    enabled: bool = False
    in_session_start_utc: str = "14:30"   # HH:MM UTC
    in_session_end_utc: str = "21:00"     # HH:MM UTC
    off_session_spread_mult: float = 3.0  # spread multiplier outside session
    # Full regime system (G3)
    regimes: Dict[str, RegimeOverride] = Field(default_factory=dict)
    weekend_days: List[int] = Field(default_factory=lambda: [5, 6])  # Sat=5, Sun=6
    reopen_window_minutes: int = 30       # minutes after weekend end


class LiquidationDetectorConfig(BaseModel):
    """OI-drop-based liquidation flow detection."""
    enabled: bool = False
    oi_drop_threshold_pct: float = 5.0     # % OI drop to trigger
    spread_mult: float = 2.0               # spread multiplier during cooldown
    size_mult: float = 0.5                 # size multiplier during cooldown
    cooldown_ticks: int = 10               # ticks to stay defensive
    # Advanced: mid-move burst detection (G6)
    mid_burst_bps: float = 0.0            # mid-move threshold to trigger (0=disabled)
    mid_burst_window: int = 3             # ticks to measure burst over
    # Tiered ladder: keep deep catchers during liq (G6)
    liq_catcher_levels: int = 0           # deep levels to keep (0=pull all)
    liq_catcher_size_mult: float = 0.3    # size for catcher levels
    # Time-stop escalation (G6)
    escalation_ticks: int = 0             # ticks after which escalate to reduce-only (0=disabled)


class OracleMonitorConfig(BaseModel):
    """Thresholds for oracle staleness detection."""
    warning_ms: int = 5_000       # start widening spreads
    stale_ms: int = 15_000        # force reduce-only + 3x spread
    kill_ms: int = 60_000         # halt quoting entirely
    enabled: bool = True


class FeedConfig(BaseModel):
    """Configuration for data feeds."""
    oracle_monitor: OracleMonitorConfig = Field(default_factory=OracleMonitorConfig)
    event_calendar_path: str = ""   # path to calendar YAML (empty = use default)
    microprice_depth: int = 5       # L2 book levels for microprice


class MarketConfig(BaseModel):
    """Complete per-market configuration for the quoting engine."""
    market_name: str = "funding_rate"
    instrument: str = "FR-PERP"
    tick_size: float = 0.01
    round_duration_s: float = 20.0

    fv_weights: FairValueWeights = Field(default_factory=FairValueWeights)
    spread: SpreadParams = Field(default_factory=SpreadParams)
    ladder: LadderParams = Field(default_factory=LadderParams)
    skew: SkewParams = Field(default_factory=SkewParams)
    feeds: FeedConfig = Field(default_factory=FeedConfig)
    fv_band: FairValueBandConfig = Field(default_factory=FairValueBandConfig)
    disagreement: DisagreementConfig = Field(default_factory=DisagreementConfig)
    funding_boundary: FundingBoundaryConfig = Field(default_factory=FundingBoundaryConfig)
    session_regime: SessionRegimeConfig = Field(default_factory=SessionRegimeConfig)
    liquidation_detector: LiquidationDetectorConfig = Field(default_factory=LiquidationDetectorConfig)

    vol_window: int = 30          # rolling window for vol estimation
    funding_dampening: float = 0.0  # 0=pass-through; >0 => scale external_ref by 1/value


def load_market_config(path: str) -> MarketConfig:
    """Load MarketConfig from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return MarketConfig(**data)


def load_market_config_by_name(name: str, config_dir: str = "") -> MarketConfig:
    """Load config by market name (funding_rate, vxxn, us3m).

    Searches config_dir or falls back to the package's configs/ directory.
    """
    if not config_dir:
        config_dir = os.path.join(
            os.path.dirname(__file__), "configs"
        )
    path = os.path.join(config_dir, f"{name}.yaml")
    return load_market_config(path)
