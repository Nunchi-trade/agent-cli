"""APEX state models — slot tracking, position lifecycle, persistence."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ApexSlot:
    """One position slot in the APEX portfolio."""
    slot_id: int = 0
    status: str = "empty"       # empty, active, closed
    instrument: str = ""
    direction: str = ""         # "long" or "short"
    entry_source: str = ""      # pulse_immediate, pulse_signal, radar
    entry_signal_score: float = 0.0
    wallet_id: str = "default"  # multi-wallet: which wallet owns this slot

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
    def from_dict(cls, d: Dict[str, Any]) -> "ApexSlot":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class ApexState:
    """Full APEX runtime state."""
    slots: List[ApexSlot] = field(default_factory=list)
    tick_count: int = 0
    start_ts: int = 0
    daily_pnl: float = 0.0
    daily_loss_triggered: bool = False
    total_trades: int = 0
    total_pnl: float = 0.0
    entry_queue: List[Dict[str, Any]] = field(default_factory=list)

    def get_empty_slot(self, now_ms: int = 0, cooldown_ms: int = 0) -> Optional[ApexSlot]:
        for slot in self.slots:
            if slot.is_empty():
                # Enforce slot cooldown: skip if closed too recently
                if cooldown_ms > 0 and slot.close_ts > 0 and now_ms > 0:
                    if now_ms - slot.close_ts < cooldown_ms:
                        continue
                return slot
        return None

    def active_slots(self) -> List[ApexSlot]:
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
    def from_dict(cls, d: Dict[str, Any]) -> "ApexState":
        state = cls(
            tick_count=d.get("tick_count", 0),
            start_ts=d.get("start_ts", 0),
            daily_pnl=d.get("daily_pnl", 0.0),
            daily_loss_triggered=d.get("daily_loss_triggered", False),
            total_trades=d.get("total_trades", 0),
            total_pnl=d.get("total_pnl", 0.0),
            entry_queue=d.get("entry_queue", []),
        )
        state.slots = [ApexSlot.from_dict(s) for s in d.get("slots", [])]
        return state

    @classmethod
    def new(cls, max_slots: int) -> "ApexState":
        return cls(
            slots=[ApexSlot(slot_id=i) for i in range(max_slots)],
            start_ts=int(time.time() * 1000),
        )


class ApexStateStore:
    """JSON persistence for APEX state."""

    def __init__(self, path: str = "data/apex/state.json"):
        self.path = path

    def save(self, state: ApexState) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(state.to_dict(), f, indent=2)

    def load(self) -> Optional[ApexState]:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as f:
                return ApexState.from_dict(json.load(f))
        except (json.JSONDecodeError, IOError):
            return None
