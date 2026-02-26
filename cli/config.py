"""Configuration loading from YAML files and CLI flags."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class TradingConfig:
    # Strategy
    strategy: str = "avellaneda_mm"
    strategy_params: Dict[str, Any] = field(default_factory=dict)

    # DSL (Dynamic Stop Loss) — optional composable guard
    dsl: Dict[str, Any] = field(default_factory=dict)

    # Anomaly protection — optional MEV protection for YEX markets
    protection: Dict[str, Any] = field(default_factory=dict)

    # Instrument
    instrument: str = "ETH-PERP"

    # Network
    mainnet: bool = False

    # Timing
    tick_interval: float = 10.0

    # Risk limits
    max_position_qty: float = 10.0
    max_notional_usd: float = 25000.0
    max_order_size: float = 5.0
    max_daily_drawdown_pct: float = 2.5
    max_leverage: float = 3.0
    tvl: float = 100000.0

    # Execution
    dry_run: bool = False
    max_ticks: int = 0

    # Persistence
    data_dir: str = "data/cli"

    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = None

    @classmethod
    def from_yaml(cls, path: str) -> "TradingConfig":
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def to_risk_limits(self):
        from parent.risk_manager import RiskLimits
        return RiskLimits(
            max_position_qty=Decimal(str(self.max_position_qty)),
            max_notional_usd=Decimal(str(self.max_notional_usd)),
            max_order_size=Decimal(str(self.max_order_size)),
            max_daily_drawdown_pct=Decimal(str(self.max_daily_drawdown_pct)),
            max_leverage=Decimal(str(self.max_leverage)),
            tvl=Decimal(str(self.tvl)),
        )

    def get_private_key(self) -> str:
        key = os.environ.get("HL_PRIVATE_KEY", "")
        if not key:
            raise RuntimeError(
                "HL_PRIVATE_KEY environment variable not set. "
                "Export it before running: export HL_PRIVATE_KEY=0x..."
            )
        return key
