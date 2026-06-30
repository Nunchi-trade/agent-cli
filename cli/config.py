"""Configuration loading from YAML files and CLI flags."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_PAIR_AUTHORIZE_URL = "http://localhost:5174/ide/authorize"
DEFAULT_PAIR_API_URL = "http://localhost:8422"
DEFAULT_PAIR_WALLET_URL = "https://web-auth-opal.vercel.app/"
DEFAULT_PAIRING_PATH = "~/.hl-agent/pairing.json"

ARBITRUM_CHAIN_ID = 42161
ARBITRUM_SEPOLIA_CHAIN_ID = 421614
ARBITRUM_USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
HL_BRIDGE2_MAINNET_ADDRESS = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"
HL_BRIDGE2_TESTNET_ADDRESS = "0x08cfc1B6b2dCF36A1480b99353A354AA8AC56f89"


@dataclass
class TradingConfig:
    # Strategy
    strategy: str = "avellaneda_mm"
    strategy_params: Dict[str, Any] = field(default_factory=dict)

    # Guard (Dynamic Stop Loss) — optional composable guard
    guard: Dict[str, Any] = field(default_factory=dict)

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

    # Builder fee
    builder: Dict[str, Any] = field(default_factory=dict)

    # web-auth pairing and wallet relay
    web_auth_authorize_url: str = field(
        default_factory=lambda: os.environ.get(
            "HL_WEB_AUTH_AUTHORIZE_URL",
            os.environ.get("VITE_PAIR_AUTHORIZE_URL", DEFAULT_PAIR_AUTHORIZE_URL),
        )
    )
    web_auth_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "HL_WEB_AUTH_API_URL",
            os.environ.get("VITE_PAIR_API_URL", DEFAULT_PAIR_API_URL),
        )
    )
    web_auth_wallet_url: str = field(
        default_factory=lambda: os.environ.get(
            "HL_WEB_AUTH_WALLET_URL",
            os.environ.get("VITE_PAIR_WALLET_URL", DEFAULT_PAIR_WALLET_URL),
        )
    )
    web_auth_pairing_path: str = field(
        default_factory=lambda: os.environ.get("HL_WEB_AUTH_PAIRING_PATH", DEFAULT_PAIRING_PATH)
    )

    # On-chain deposit config
    arbitrum_chain_id: int = field(
        default_factory=lambda: int(os.environ.get("HL_ARBITRUM_CHAIN_ID", str(ARBITRUM_CHAIN_ID)))
    )
    arbitrum_testnet_chain_id: int = field(
        default_factory=lambda: int(os.environ.get("HL_ARBITRUM_TESTNET_CHAIN_ID", str(ARBITRUM_SEPOLIA_CHAIN_ID)))
    )
    arbitrum_usdc_address: str = field(
        default_factory=lambda: os.environ.get("HL_ARBITRUM_USDC_ADDRESS", ARBITRUM_USDC_ADDRESS)
    )
    arbitrum_testnet_usdc_address: Optional[str] = field(
        default_factory=lambda: os.environ.get("HL_ARBITRUM_TESTNET_USDC_ADDRESS")
    )
    hl_bridge2_mainnet_address: str = field(
        default_factory=lambda: os.environ.get("HL_BRIDGE2_MAINNET_ADDRESS", HL_BRIDGE2_MAINNET_ADDRESS)
    )
    hl_bridge2_testnet_address: str = field(
        default_factory=lambda: os.environ.get("HL_BRIDGE2_TESTNET_ADDRESS", HL_BRIDGE2_TESTNET_ADDRESS)
    )

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
        # If mainnet and using default testnet values, switch to mainnet defaults
        if self.mainnet and self._is_default_risk():
            return RiskLimits.mainnet_defaults()
        return RiskLimits(
            max_position_qty=Decimal(str(self.max_position_qty)),
            max_notional_usd=Decimal(str(self.max_notional_usd)),
            max_order_size=Decimal(str(self.max_order_size)),
            max_daily_drawdown_pct=Decimal(str(self.max_daily_drawdown_pct)),
            max_leverage=Decimal(str(self.max_leverage)),
            tvl=Decimal(str(self.tvl)),
        )

    def _is_default_risk(self) -> bool:
        """Check if risk params are still at testnet defaults (not user-configured)."""
        return (
            self.max_position_qty == 10.0
            and self.max_notional_usd == 25000.0
            and self.max_order_size == 5.0
            and self.tvl == 100000.0
        )

    def get_builder_config(self):
        from cli.builder_fee import BuilderFeeConfig
        if self.builder:
            return BuilderFeeConfig.from_dict(self.builder)
        return BuilderFeeConfig.from_env()

    def get_private_key(self) -> str:
        from common.credentials import resolve_private_key
        return resolve_private_key(venue="hl")
