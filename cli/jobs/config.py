"""Job configuration — loads from YAML, validates, and exposes typed fields."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel


class RiskLimits(BaseModel):
    """Risk limits for cooperative jobs. Mirrors the clearing-layer RiskLimits."""

    max_position_notional_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    max_leverage: float = 1.0
    daily_loss_limit_usd: float = 0.0
    max_open_orders: int = 0
    concentration_limit_pct: float = 100.0


@dataclass
class JobConfig:
    """Configuration for a single agent job instance.

    Can be constructed directly or loaded from a YAML file via ``from_yaml``.
    """

    job_id: str = ""
    agent_id: str = field(default_factory=lambda: f"agent-{uuid.uuid4().hex[:8]}")
    mainnet: bool = False
    chain_rpc: str = ""
    event_bus_ws: str = ""
    relay_url: str = ""
    strategy: str = ""
    strategy_params: Dict[str, Any] = field(default_factory=dict)
    stake_amount: float = 0.0
    tee_enabled: bool = False
    pcr_whitelist: List[str] = field(default_factory=list)
    custody: Dict[str, Any] = field(default_factory=dict)
    risk: Dict[str, Any] = field(default_factory=dict)
    data_dir: str = "data/jobs"

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str) -> JobConfig:
        """Load a ``JobConfig`` from a YAML file.

        Parameters
        ----------
        path:
            Filesystem path to the YAML configuration file.

        Returns
        -------
        JobConfig
            A fully-populated configuration instance.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        yaml.YAMLError
            If the file contains invalid YAML.
        """
        raw = Path(path).read_text()
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError(f"Expected a YAML mapping at top level, got {type(data).__name__}")
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    # ------------------------------------------------------------------
    # Converters
    # ------------------------------------------------------------------

    def to_risk_limits(self) -> RiskLimits:
        """Convert the ``risk`` dict into a typed :class:`RiskLimits` object.

        Returns
        -------
        RiskLimits
            Validated risk limits ready for the cooperative engine.
        """
        return RiskLimits(**self.risk)
