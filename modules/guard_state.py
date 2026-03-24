"""Guard runtime state — serializable to/from JSON for persistence."""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class GuardState:
    """Mutable runtime state for one Guard-managed position."""

    # Position identity
    instrument: str = ""
    position_id: str = ""
    entry_price: float = 0.0
    position_size: float = 0.0
    direction: str = "long"

    # Runtime Guard state
    high_water: float = 0.0
    high_water_ts: int = 0          # ms when HW was last updated
    current_tier_index: int = -1    # -1 = Phase 1 (no tier yet)
    breach_count: int = 0
    current_roe: float = 0.0

    # Exchange-level stop loss
    exchange_sl_oid: str = ""

    # Phase 1 timing
    phase1_start_ts: int = 0           # ms when Phase 1 started (set at creation)

    # Lifecycle
    created_ts: int = 0
    last_check_ts: int = 0
    closed: bool = False
    close_reason: str = ""
    close_price: float = 0.0
    close_ts: int = 0

    def copy(self) -> GuardState:
        return copy.copy(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument": self.instrument,
            "position_id": self.position_id,
            "entry_price": self.entry_price,
            "position_size": self.position_size,
            "direction": self.direction,
            "high_water": self.high_water,
            "high_water_ts": self.high_water_ts,
            "current_tier_index": self.current_tier_index,
            "breach_count": self.breach_count,
            "current_roe": self.current_roe,
            "exchange_sl_oid": self.exchange_sl_oid,
            "phase1_start_ts": self.phase1_start_ts,
            "created_ts": self.created_ts,
            "last_check_ts": self.last_check_ts,
            "closed": self.closed,
            "close_reason": self.close_reason,
            "close_price": self.close_price,
            "close_ts": self.close_ts,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GuardState:
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    @classmethod
    def new(
        cls,
        instrument: str,
        entry_price: float,
        position_size: float,
        direction: str = "long",
        position_id: str = "",
    ) -> GuardState:
        now = int(time.time() * 1000)
        return cls(
            instrument=instrument,
            position_id=position_id or f"{instrument}-{now}",
            entry_price=entry_price,
            position_size=position_size,
            direction=direction,
            high_water=entry_price,
            high_water_ts=now,
            phase1_start_ts=now,
            created_ts=now,
        )


class GuardStateStore:
    """JSON file-per-position persistence for Guard state."""

    def __init__(self, data_dir: str = "data/guard"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def save(self, state: GuardState, config_dict: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"state": state.to_dict()}
        if config_dict:
            payload["config"] = config_dict
        path = self.data_dir / f"{state.position_id}.json"
        content = json.dumps(payload, indent=2, default=str)
        # Atomic write: write to temp file, then rename (prevents corruption on crash)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(content)
        tmp_path.replace(path)

    def load(self, position_id: str) -> Optional[Dict[str, Any]]:
        path = self.data_dir / f"{position_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def load_state(self, position_id: str) -> Optional[GuardState]:
        data = self.load(position_id)
        if data is None:
            return None
        return GuardState.from_dict(data["state"])

    def list_active(self) -> List[str]:
        active = []
        for path in self.data_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                if not data.get("state", {}).get("closed", False):
                    active.append(path.stem)
            except Exception:
                continue
        return active

    def list_all(self) -> List[str]:
        return [p.stem for p in self.data_dir.glob("*.json")]
