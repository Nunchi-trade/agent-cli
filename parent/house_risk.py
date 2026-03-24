"""House-level risk aggregation across all wallets.

Aggregates PnL, drawdown, and exposure across all wallet-isolated engines.
Triggers a house-level halt if aggregate limits are breached.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional

from parent.risk_manager import RiskState

log = logging.getLogger("house_risk")
ZERO = Decimal("0")


@dataclass
class HouseRiskState:
    """Mutable aggregate state across all wallets."""
    total_daily_pnl: Decimal = ZERO
    total_daily_drawdown: Decimal = ZERO
    total_exposure: Decimal = ZERO
    halt_triggered: bool = False
    halt_reason: str = ""


class HouseRiskManager:
    """Aggregates risk across all wallets for house-level limits.

    Operates independently from per-wallet RiskManagers.  The MultiWalletEngine
    calls update() after each tick cycle, and should_halt_all() before allowing
    the next cycle.
    """

    def __init__(
        self,
        max_house_drawdown: float = 2000.0,
        max_house_exposure: float = 100_000.0,
    ):
        self.max_house_drawdown = Decimal(str(max_house_drawdown))
        self.max_house_exposure = Decimal(str(max_house_exposure))
        self.state = HouseRiskState()

    def update(self, wallet_states: Dict[str, RiskState]) -> None:
        """Aggregate daily_pnl and daily_drawdown across all wallets.

        Args:
            wallet_states: mapping of wallet_id -> RiskState from each
                           per-wallet RiskManager.
        """
        total_pnl = ZERO
        total_dd = ZERO
        for wid, rs in wallet_states.items():
            total_pnl += rs.daily_pnl
            total_dd += rs.daily_drawdown
        self.state.total_daily_pnl = total_pnl
        self.state.total_daily_drawdown = total_dd

        # Check house drawdown
        if total_dd >= self.max_house_drawdown:
            self.state.halt_triggered = True
            self.state.halt_reason = (
                f"house_drawdown {total_dd} >= limit {self.max_house_drawdown}"
            )
            log.critical("HOUSE HALT: %s", self.state.halt_reason)

    def update_exposure(self, wallet_exposures: Dict[str, Decimal]) -> None:
        """Update total house exposure and check limits.

        Args:
            wallet_exposures: mapping of wallet_id -> total notional exposure.
        """
        total = sum(wallet_exposures.values(), ZERO)
        self.state.total_exposure = total
        if total >= self.max_house_exposure:
            self.state.halt_triggered = True
            self.state.halt_reason = (
                f"house_exposure {total} >= limit {self.max_house_exposure}"
            )
            log.critical("HOUSE HALT: %s", self.state.halt_reason)

    def should_halt_all(self) -> bool:
        """True if house-level limits are breached — all wallets must stop."""
        return self.state.halt_triggered

    def clear_halt(self) -> None:
        """Operator override to clear house halt."""
        self.state.halt_triggered = False
        self.state.halt_reason = ""
        log.info("House halt cleared manually")

    def summary(self) -> Dict[str, Any]:
        """House-level risk summary for logging/telemetry."""
        return {
            "total_daily_pnl": str(self.state.total_daily_pnl),
            "total_daily_drawdown": str(self.state.total_daily_drawdown),
            "total_exposure": str(self.state.total_exposure),
            "halt_triggered": self.state.halt_triggered,
            "halt_reason": self.state.halt_reason,
            "max_house_drawdown": str(self.max_house_drawdown),
            "max_house_exposure": str(self.max_house_exposure),
        }
