"""Portfolio-level risk checks -- cross-instrument correlation and margin monitoring."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from common.models import instrument_to_asset

log = logging.getLogger("portfolio_risk")

# Rough correlation groups for crypto assets
# Assets in the same group are considered correlated (>0.7 correlation)
CORRELATION_GROUPS = {
    "large_cap": {"BTC", "ETH"},
    "l2": {"ARB", "OP", "STRK", "MANTA", "BLAST"},
    "alt_l1": {"SOL", "AVAX", "SUI", "SEI", "APT", "TIA"},
    "defi_blue": {"AAVE", "UNI", "MKR", "COMP", "CRV", "SNX", "LINK"},
    "meme": {"DOGE", "SHIB", "PEPE", "WIF", "BONK"},
    "ai": {"FET", "RNDR", "TAO", "NEAR"},
}

# Reverse lookup: coin -> group name
COIN_TO_GROUP: Dict[str, str] = {}
for group, coins in CORRELATION_GROUPS.items():
    for coin in coins:
        COIN_TO_GROUP[coin] = group


@dataclass
class PortfolioRiskConfig:
    """Configuration for portfolio-level risk."""
    max_correlated_positions: int = 2       # Max positions in same correlation group
    max_same_direction_total: int = 3       # Max total positions in same direction
    margin_utilization_warn: float = 0.7    # Warn at 70% margin utilization
    margin_utilization_block: float = 0.9   # Block new entries at 90%
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PortfolioRiskConfig":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class PortfolioRiskState:
    """Current portfolio risk assessment."""
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # instrument -> {direction, notional}
    margin_utilization: float = 0.0
    correlated_groups: Dict[str, List[str]] = field(default_factory=dict)  # group -> [instruments]
    warnings: List[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""


class PortfolioRiskManager:
    """Cross-instrument portfolio risk checks.

    Complements the per-instrument RiskManager with:
    - Correlation group limits (don't stack 3 L2 longs)
    - Direction concentration limits
    - Margin utilization monitoring
    """

    def __init__(self, config: Optional[PortfolioRiskConfig] = None):
        self.config = config or PortfolioRiskConfig()

    def assess(self, positions: Dict[str, Dict[str, Any]],
               account_state: Optional[Dict] = None) -> PortfolioRiskState:
        """Assess current portfolio risk.

        Args:
            positions: {instrument: {"direction": "long"/"short", "notional": float}}
            account_state: Optional account state from hl.get_account_state()

        Returns:
            PortfolioRiskState with warnings and block status.
        """
        state = PortfolioRiskState(positions=positions)

        if not self.config.enabled:
            return state

        # 1. Build correlation groups
        for inst, pos in positions.items():
            coin = instrument_to_asset(inst)
            group = COIN_TO_GROUP.get(coin, f"ungrouped:{coin}")
            if group not in state.correlated_groups:
                state.correlated_groups[group] = []
            state.correlated_groups[group].append(inst)

        # 2. Check correlation limits
        for group, instruments in state.correlated_groups.items():
            if group.startswith("ungrouped:"):
                continue
            if len(instruments) > self.config.max_correlated_positions:
                state.warnings.append(
                    f"Correlation limit: {len(instruments)} positions in '{group}' group "
                    f"(max {self.config.max_correlated_positions}): {instruments}"
                )

        # 3. Check direction concentration
        longs = [i for i, p in positions.items() if p.get("direction") == "long"]
        shorts = [i for i, p in positions.items() if p.get("direction") == "short"]

        if len(longs) > self.config.max_same_direction_total:
            state.warnings.append(
                f"Direction concentration: {len(longs)} longs (max {self.config.max_same_direction_total})"
            )
        if len(shorts) > self.config.max_same_direction_total:
            state.warnings.append(
                f"Direction concentration: {len(shorts)} shorts (max {self.config.max_same_direction_total})"
            )

        # 4. Margin utilization
        if account_state:
            value = account_state.get("account_value", 0)
            margin = account_state.get("total_margin", 0)
            if value > 0:
                state.margin_utilization = margin / value

                if state.margin_utilization >= self.config.margin_utilization_block:
                    state.blocked = True
                    state.block_reason = f"Margin utilization {state.margin_utilization:.0%} >= {self.config.margin_utilization_block:.0%}"
                    state.warnings.append(state.block_reason)
                elif state.margin_utilization >= self.config.margin_utilization_warn:
                    state.warnings.append(
                        f"High margin utilization: {state.margin_utilization:.0%}"
                    )

        if state.warnings:
            for w in state.warnings:
                log.warning("Portfolio risk: %s", w)

        return state

    def check_entry(self, instrument: str, direction: str,
                    current_positions: Dict[str, Dict[str, Any]],
                    account_state: Optional[Dict] = None) -> Tuple[bool, str]:
        """Check if a new entry would violate portfolio risk limits.

        Returns (ok, reason).
        """
        if not self.config.enabled:
            return True, "ok"

        # Simulate adding the new position
        test_positions = dict(current_positions)
        test_positions[instrument] = {"direction": direction, "notional": 0}

        state = self.assess(test_positions, account_state)

        if state.blocked:
            return False, state.block_reason

        # Check correlation group
        coin = instrument_to_asset(instrument)
        group = COIN_TO_GROUP.get(coin)
        if group and group in state.correlated_groups:
            if len(state.correlated_groups[group]) > self.config.max_correlated_positions:
                return False, f"Would exceed correlation limit for '{group}' group"

        # Check direction concentration
        same_dir = [i for i, p in test_positions.items() if p.get("direction") == direction]
        if len(same_dir) > self.config.max_same_direction_total:
            return False, f"Would exceed {direction} direction limit ({len(same_dir)} > {self.config.max_same_direction_total})"

        return True, "ok"
