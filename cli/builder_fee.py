"""Builder fee configuration and construction.

Hyperliquid builder fees: collected natively per-order via BuilderInfo.
Fee field 'f' is in tenths of basis points (e.g., f=10 = 1 bps = 0.01%).
Users must approve the builder fee via approve_builder_fee() before orders work.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

NUNCHI_BUILDER_ADDRESS = "0x0D1DB1C800184A203915757BbbC0ee3A8E12FfB0"


@dataclass
class BuilderFeeConfig:
    """Builder fee settings. Loaded from env vars or YAML config."""

    builder_address: str = NUNCHI_BUILDER_ADDRESS
    fee_rate_tenths_bps: int = 100  # 10 bps (0.1%)

    @property
    def enabled(self) -> bool:
        return bool(self.builder_address) and self.fee_rate_tenths_bps > 0

    @property
    def fee_bps(self) -> float:
        """Fee in basis points (human-readable)."""
        return self.fee_rate_tenths_bps / 10.0

    @property
    def max_fee_rate_str(self) -> str:
        """Max fee rate string for approve_builder_fee (e.g., '0.01%' for 1 bps)."""
        pct = self.fee_rate_tenths_bps / 1000.0  # tenths of bps -> percent
        return f"{pct}%"

    def to_builder_info(self) -> Optional[Dict[str, Any]]:
        """Return BuilderInfo dict for SDK, or None if disabled."""
        if not self.enabled:
            return None
        return {"b": self.builder_address, "f": self.fee_rate_tenths_bps}

    def metadata(self) -> Dict[str, Any]:
        """Return ledger-safe builder-code metadata."""
        return {
            "builder_code_required": True,
            "builder_address": self.builder_address or None,
            "builder_fee_tenths_bps": self.fee_rate_tenths_bps,
            "builder_fee_bps": self.fee_bps,
            "builder_fee_enabled": self.enabled,
        }

    def validate_for_broadcast(self) -> None:
        """Fail closed before a live order can leave agent-cli."""
        if not self.enabled:
            raise RuntimeError(
                "builder-code validation failed: BUILDER_ADDRESS and BUILDER_FEE_TENTHS_BPS must configure a positive Nunchi builder fee"
            )
        if not isinstance(self.builder_address, str) or not self.builder_address.startswith("0x") or len(self.builder_address) != 42:
            raise RuntimeError("builder-code validation failed: builder address must be a 20-byte hex address")
        if self.fee_rate_tenths_bps <= 0:
            raise RuntimeError("builder-code validation failed: builder fee must be positive")

    @classmethod
    def from_env(cls) -> "BuilderFeeConfig":
        """Load from env vars, falling back to hardcoded defaults."""
        default = cls()
        addr = os.environ.get("BUILDER_ADDRESS", default.builder_address)
        fee = int(os.environ.get("BUILDER_FEE_TENTHS_BPS", str(default.fee_rate_tenths_bps)))
        return cls(builder_address=addr, fee_rate_tenths_bps=fee)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BuilderFeeConfig":
        """Load from a config dict (e.g., YAML builder: section)."""
        return cls(
            builder_address=str(d.get("builder_address", "")),
            fee_rate_tenths_bps=int(d.get("fee_rate_tenths_bps", 0)),
        )
