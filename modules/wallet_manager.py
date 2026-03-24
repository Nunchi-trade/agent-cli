"""Wallet manager — per-strategy wallet isolation with independent budgets.

Maps strategy labels to wallet addresses, budgets, guard presets, and risk
configs.  Default config uses a single wallet (backward compatible).
Multi-wallet is opt-in via YAML/JSON config.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

log = logging.getLogger("wallet_manager")


@dataclass
class WalletConfig:
    """Configuration for a single strategy wallet."""

    wallet_id: str = "default"
    address: str = ""                  # 0x... wallet address
    budget: float = 10_000.0           # per-wallet budget
    leverage: float = 10.0
    guard_preset: str = "tight"        # guard preset for this wallet
    max_slots: int = 3
    daily_loss_limit: float = 500.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wallet_id": self.wallet_id,
            "address": self.address,
            "budget": self.budget,
            "leverage": self.leverage,
            "guard_preset": self.guard_preset,
            "max_slots": self.max_slots,
            "daily_loss_limit": self.daily_loss_limit,
        }

    def to_risk_limits(self) -> "RiskLimits":
        """Create per-wallet RiskLimits from this wallet config.

        Imports RiskLimits locally to avoid circular dependency.
        """
        from parent.risk_manager import RiskLimits

        return RiskLimits(
            max_notional_usd=Decimal(str(self.budget)),
            max_daily_drawdown_pct=Decimal("2.5"),
            max_leverage=Decimal(str(self.leverage)),
            max_position_qty=Decimal(str(self.budget / max(self.leverage, 1.0))),
            max_order_size=Decimal(str(self.budget / max(self.leverage, 1.0) / 2)),
            tvl=Decimal(str(self.budget)),
            reserve_factor_pct=Decimal("10"),
        )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WalletConfig":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


class WalletManager:
    """Registry mapping strategy labels to wallet configurations.

    Single-wallet mode (default): one "default" wallet used for everything.
    Multi-wallet mode: each strategy_id maps to its own WalletConfig.
    """

    def __init__(self, wallets: Optional[Dict[str, WalletConfig]] = None):
        self._wallets: Dict[str, WalletConfig] = wallets or {}

    @property
    def is_multi_wallet(self) -> bool:
        """True if more than one wallet is configured."""
        return len(self._wallets) > 1

    @property
    def wallet_ids(self) -> List[str]:
        return list(self._wallets.keys())

    def get(self, wallet_id: str) -> Optional[WalletConfig]:
        """Get wallet config by ID."""
        return self._wallets.get(wallet_id)

    def get_default(self) -> WalletConfig:
        """Get the default wallet. Falls back to a bare WalletConfig."""
        return self._wallets.get("default", WalletConfig())

    def get_by_address(self, address: str) -> Optional[WalletConfig]:
        """Look up wallet config by address."""
        addr = address.lower()
        for wc in self._wallets.values():
            if wc.address.lower() == addr:
                return wc
        return None

    def register(self, wallet_id: str, config: WalletConfig) -> None:
        """Register or update a wallet configuration.

        Raises ValueError if the address is already registered under a
        different wallet_id (prevents non-deterministic get_by_address).
        """
        if config.address:
            for wid, existing in self._wallets.items():
                if wid != wallet_id and existing.address and existing.address.lower() == config.address.lower():
                    raise ValueError(
                        f"Address {config.address} already registered under wallet '{wid}'. "
                        f"Each wallet must have a unique address."
                    )
        config.wallet_id = wallet_id
        self._wallets[wallet_id] = config

    def total_budget(self) -> float:
        """Sum of all wallet budgets (house-level aggregate)."""
        return sum(w.budget for w in self._wallets.values())

    def total_daily_loss_limit(self) -> float:
        """Sum of all wallet daily loss limits."""
        return sum(w.daily_loss_limit for w in self._wallets.values())

    def to_dict(self) -> Dict[str, Any]:
        return {wid: wc.to_dict() for wid, wc in self._wallets.items()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WalletManager":
        wallets = {wid: WalletConfig.from_dict(wc) for wid, wc in data.items()}
        return cls(wallets=wallets)

    @classmethod
    def from_single(cls, address: str = "", budget: float = 10_000.0,
                    leverage: float = 10.0, guard_preset: str = "tight",
                    max_slots: int = 3, daily_loss_limit: float = 500.0) -> "WalletManager":
        """Create a single-wallet manager (backward-compatible default)."""
        wc = WalletConfig(
            wallet_id="default",
            address=address,
            budget=budget,
            leverage=leverage,
            guard_preset=guard_preset,
            max_slots=max_slots,
            daily_loss_limit=daily_loss_limit,
        )
        return cls(wallets={"default": wc})

    @classmethod
    def from_yaml_section(cls, data: Dict[str, Any]) -> "WalletManager":
        """Parse from YAML 'wallets' section.

        Format:
          wallets:
            aggressive_eth:
              address: "0xABC..."
              budget: 5000
              leverage: 15
              guard_preset: tight
            conservative_btc:
              address: "0xDEF..."
              budget: 10000
              leverage: 5
              guard_preset: wide
        """
        if not data:
            return cls.from_single()
        return cls.from_dict(data)
