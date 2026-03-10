"""WOLF state models — slot tracking, position lifecycle, persistence."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WolfSlot:
    """One position slot in the WOLF portfolio."""
    slot_id: int = 0
    status: str = "empty"       # empty, active, closed
    instrument: str = ""
    direction: str = ""         # "long" or "short"
    entry_source: str = ""      # movers_immediate, movers_signal, radar
    entry_signal_score: float = 0.0

    # Position data
    entry_price: float = 0.0
    entry_size: float = 0.0
    margin_allocated: float = 0.0

    # Tracking
    current_price: float = 0.0
    current_roe: float = 0.0
    high_water_roe: float = 0.0
    last_progress_ts: int = 0
    entry_ts: int = 0
    close_ts: int = 0
    close_reason: str = ""
    close_pnl: float = 0.0

    # Signal tracking
    last_signal_seen_ts: int = 0
    signal_disappeared_ts: int = 0

    def is_empty(self) -> bool:
        return self.status == "empty"

    def is_active(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WolfSlot":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class WolfState:
    """Full WOLF runtime state."""
    slots: List[WolfSlot] = field(default_factory=list)
    tick_count: int = 0
    start_ts: int = 0
    daily_pnl: float = 0.0
    daily_loss_triggered: bool = False
    total_trades: int = 0
    total_pnl: float = 0.0
    entry_queue: List[Dict[str, Any]] = field(default_factory=list)

    def get_empty_slot(self) -> Optional[WolfSlot]:
        for slot in self.slots:
            if slot.is_empty():
                return slot
        return None

    def active_slots(self) -> List[WolfSlot]:
        return [s for s in self.slots if s.is_active()]

    def active_instruments(self) -> set:
        return {s.instrument for s in self.active_slots()}

    def direction_count(self, direction: str) -> int:
        return sum(1 for s in self.active_slots() if s.direction == direction)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slots": [s.to_dict() for s in self.slots],
            "tick_count": self.tick_count,
            "start_ts": self.start_ts,
            "daily_pnl": self.daily_pnl,
            "daily_loss_triggered": self.daily_loss_triggered,
            "total_trades": self.total_trades,
            "total_pnl": self.total_pnl,
            "entry_queue": self.entry_queue,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WolfState":
        state = cls(
            tick_count=d.get("tick_count", 0),
            start_ts=d.get("start_ts", 0),
            daily_pnl=d.get("daily_pnl", 0.0),
            daily_loss_triggered=d.get("daily_loss_triggered", False),
            total_trades=d.get("total_trades", 0),
            total_pnl=d.get("total_pnl", 0.0),
            entry_queue=d.get("entry_queue", []),
        )
        state.slots = [WolfSlot.from_dict(s) for s in d.get("slots", [])]
        return state

    @classmethod
    def new(cls, max_slots: int) -> "WolfState":
        return cls(
            slots=[WolfSlot(slot_id=i) for i in range(max_slots)],
            start_ts=int(time.time() * 1000),
        )


class WolfStateStore:
    """JSON persistence for WOLF state."""

    def __init__(self, path: str = "data/wolf/state.json"):
        self.path = path

    def save(self, state: WolfState) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(state.to_dict(), f, indent=2)

    def load(self) -> Optional[WolfState]:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as f:
                return WolfState.from_dict(json.load(f))
        except (json.JSONDecodeError, IOError):
            return None
