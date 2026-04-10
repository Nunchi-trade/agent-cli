"""APEX strategy configuration — budget, slots, risk, and presets."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ApexConfig:
    """Configuration for APEX autonomous trading strategy."""

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

    # Rotation cooldown
    min_hold_ms: int = 2_700_000       # 45 min — blocks conviction/stagnation exits
    slot_cooldown_ms: int = 300_000    # 5 min — prevents slot reuse after close

    # Risk
    daily_loss_limit: float = 500.0
    max_same_direction: int = 2

    # Risk Guardian gate machine
    cooldown_duration_ms: int = 1_800_000          # 30 min auto-expiry
    cooldown_trigger_losses: int = 2               # consecutive losses to enter COOLDOWN
    cooldown_drawdown_pct: float = 50.0            # % of daily loss limit → COOLDOWN

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

    # Directional strategy integration
    strategy_enabled: bool = False
    strategy_names: List[str] = field(default_factory=list)
    strategy_interval_ticks: int = 1  # Run every N ticks (1 = every tick)

    # Order type optimization (ALO fee savings ~3 bps round-trip)
    entry_order_type: str = "Alo"  # "Alo" (maker rebate), "Gtc", or "Ioc"

    # Multi-strategy wallets (opt-in: empty dict = single-wallet mode)
    wallet_config: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Instrument filters
    excluded_instruments: List[str] = field(default_factory=list)
    allowed_instruments: List[str] = field(default_factory=list)

    # Signal direction flip — when True, invert all entry directions
    # (long→short, short→long).  On thin markets like YEX BTCSWP, pulse
    # signals (FUNDING_FLIP, VOLUME_SURGE, OI_DELTA) fire at move *exhaustion*,
    # not initiation.  Flipping direction converts a 0% win-rate signal set
    # into a mean-reversion strategy.
    flip_signal_direction: bool = False

    # Preset name (optional). When set, the standalone runner uses this to
    # look up matching pulse/radar presets in PULSE_PRESETS / RADAR_PRESETS
    # at boot. Set automatically by `apex run --preset <name>` via _run_apex.
    preset_name: str = ""

    def __post_init__(self):
        if self.margin_per_slot == 0.0:
            self.margin_per_slot = self.total_budget / max(self.max_slots, 1)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ApexConfig":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @classmethod
    def from_yaml(cls, path: str) -> "ApexConfig":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data.get("apex", data.get("wolf", data)))

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def to_json(self, path: str) -> None:
        """Serialize config to a JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_json(cls, path: str) -> "ApexConfig":
        """Deserialize config from a JSON file."""
        with open(path) as f:
            data = json.load(f)
        return cls.from_dict(data)


APEX_PRESETS: Dict[str, ApexConfig] = {
    "default": ApexConfig(),
    "conservative": ApexConfig(
        max_slots=2,
        leverage=5.0,
        radar_score_threshold=190,
        pulse_confidence_threshold=80.0,
        daily_loss_limit=250.0,
    ),
    "aggressive": ApexConfig(
        max_slots=3,
        leverage=15.0,
        radar_score_threshold=150,
        pulse_confidence_threshold=60.0,
        daily_loss_limit=1000.0,
    ),
    # Tuned for testnet competitions on the yex HIP-3 dex with 3 markets
    # (VXX, US3M, BTCSWP). Pair with --markets VXX-USDYP,US3M-USDYP,BTCSWP-USDYP.
    #
    # Tuning history:
    #   v1 (2026-04-08): leverage=15, radar=110, pulse=45, min_hold=10min,
    #     daily_loss=2000. Demonstrated agents trade but every cohort agent
    #     bled $100-$440 in the first hour because the high leverage + low
    #     entry threshold + 10-min churn was a losing combination on the
    #     low-liquidity yex markets.
    #
    #   v2 (2026-04-09): drop leverage to 5x, raise entry thresholds, longer
    #     min hold, much tighter daily loss limit so losing agents pause
    #     instead of bleeding through their entire balance.
    #
    #   v3 (2026-04-09): Phase 0 of profitability roadmap. Baseline snapshot
    #     showed 0/14 agents profitable, fleet PnL -$9.4k. Root cause: exit
    #     logic structurally asymmetric. Hard stop at -5% ROE on 5x lev = -1%
    #     price move; +0.5% entry slippage + fees meant positions stopped on
    #     noise within minutes, while stagnation TP at +3% ROE was blocked
    #     by the 30-min min_hold. v3 fixes:
    #       - leverage 5x → 3x (more headroom per stop)
    #       - max_negative_roe -5% → -10% (wider stop, ~3.3% price at 3x)
    #       - SLIPPAGE_FACTOR 1.005 → 1.002 (in hl_adapter.py)
    #       - stagnation TP allowed to fire during min_hold (apex_engine.py)
    "competition": ApexConfig(
        max_slots=3,
        leverage=3.0,                     # v3: 5.0 → 3.0 (more headroom per stop)
        max_negative_roe=-10.0,           # v3: -5.0 → -10.0 (~3.3% price at 3x lev)
        flip_signal_direction=True,       # v4: 0/64 wins = inverted signals; flip for mean-reversion
        # v3.1: DISABLE RADAR. Attribution data over 64 trades showed radar
        # produced 51 trades with 0% win rate and -$17/trade avg. It fires
        # ~once every 9 min and every entry is wrong-way. Score threshold
        # doesn't help — the fundamental signal has no edge on BTCSWP.
        # Set threshold impossibly high so no radar entry ever qualifies.
        radar_score_threshold=130,        # v4.2: lowered from 170, market too quiet at 170
        pulse_confidence_threshold=45.0,  # v3: 60 → 45 (observed FUNDING_FLIP on BTCSWP at conf=50)
        radar_interval_ticks=5,           # still scanning for attribution data
        min_hold_ms=1_800_000,            # v2: was 600_000 (10min) -> 30 min
        slot_cooldown_ms=60_000,          # 1 min instead of 5
        daily_loss_limit=200.0,           # v2: was 2000 — pause losing agents fast
        # Force IOC orders so entries cross the spread and fill immediately.
        # The default ALO posts limit orders that almost never fill on the
        # low-liquidity yex markets and the runner cancels them after one
        # tick. IOC trades the maker rebate for guaranteed fills.
        entry_order_type="Ioc",
    ),
}
