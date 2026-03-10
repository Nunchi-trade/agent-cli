"""Agent memory engine — pure computation, zero I/O.

Persistent memory system for WOLF. Tracks parameter changes, session events,
REFLECT reviews, notable trades, and judge findings. Maintains a playbook of
accumulated knowledge about what works per instrument and signal source.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MemoryEvent:
    """A single memory event."""
    event_type: str          # param_change, session_start, session_end,
                             # reflect_review, notable_trade, judge_finding
    timestamp_ms: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "timestamp_ms": self.timestamp_ms,
            "payload": self.payload,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MemoryEvent:
        return cls(
            event_type=d.get("event_type", ""),
            timestamp_ms=d.get("timestamp_ms", 0),
            payload=d.get("payload", {}),
            summary=d.get("summary", ""),
        )


@dataclass
class PlaybookEntry:
    """Accumulated knowledge about an instrument + signal source combo."""
    instrument: str = ""
    signal_source: str = ""
    trade_count: int = 0
    win_count: int = 0
    total_pnl: float = 0.0
    total_roe: float = 0.0
    avg_holding_ms: float = 0.0
    last_updated_ms: int = 0

    @property
    def win_rate(self) -> float:
        return (self.win_count / self.trade_count * 100) if self.trade_count else 0.0

    @property
    def avg_roe(self) -> float:
        return (self.total_roe / self.trade_count) if self.trade_count else 0.0

    @property
    def avg_pnl(self) -> float:
        return (self.total_pnl / self.trade_count) if self.trade_count else 0.0

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "signal_source": self.signal_source,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "total_pnl": round(self.total_pnl, 4),
            "total_roe": round(self.total_roe, 4),
            "avg_holding_ms": self.avg_holding_ms,
            "last_updated_ms": self.last_updated_ms,
            "win_rate": round(self.win_rate, 1),
            "avg_roe": round(self.avg_roe, 2),
            "avg_pnl": round(self.avg_pnl, 4),
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlaybookEntry:
        return cls(
            instrument=d.get("instrument", ""),
            signal_source=d.get("signal_source", ""),
            trade_count=d.get("trade_count", 0),
            win_count=d.get("win_count", 0),
            total_pnl=d.get("total_pnl", 0.0),
            total_roe=d.get("total_roe", 0.0),
            avg_holding_ms=d.get("avg_holding_ms", 0.0),
            last_updated_ms=d.get("last_updated_ms", 0),
        )


@dataclass
class Playbook:
    """Accumulated 'what works' knowledge."""
    entries: Dict[str, PlaybookEntry] = field(default_factory=dict)

    @staticmethod
    def _key(instrument: str, source: str) -> str:
        return f"{instrument}:{source}"

    def get(self, instrument: str, source: str) -> Optional[PlaybookEntry]:
        return self.entries.get(self._key(instrument, source))

    def to_dict(self) -> dict:
        return {k: v.to_dict() for k, v in self.entries.items()}

    @classmethod
    def from_dict(cls, d: dict) -> Playbook:
        entries = {k: PlaybookEntry.from_dict(v) for k, v in d.items()}
        return cls(entries=entries)


# ---------------------------------------------------------------------------
# Pure engine
# ---------------------------------------------------------------------------

class MemoryEngine:
    """Pure factory methods for creating memory events. Zero I/O."""

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def create_param_change_event(
        self,
        adjustments: list,
        metrics_summary: str = "",
    ) -> MemoryEvent:
        adj_list = []
        for a in adjustments:
            adj_list.append({
                "param": a.param,
                "old": a.old_value,
                "new": a.new_value,
                "reason": a.reason,
            })
        summary_parts = [f"{a.param}: {a.old_value}->{a.new_value}" for a in adjustments]
        return MemoryEvent(
            event_type="param_change",
            timestamp_ms=self._now_ms(),
            payload={"adjustments": adj_list, "metrics_summary": metrics_summary},
            summary=f"Config adjusted: {'; '.join(summary_parts)}",
        )

    def create_session_event(
        self,
        event_type: str,
        tick_count: int = 0,
        total_pnl: float = 0.0,
        active_slots: int = 0,
        total_trades: int = 0,
    ) -> MemoryEvent:
        return MemoryEvent(
            event_type=event_type,
            timestamp_ms=self._now_ms(),
            payload={
                "tick_count": tick_count,
                "total_pnl": round(total_pnl, 4),
                "active_slots": active_slots,
                "total_trades": total_trades,
            },
            summary=f"Session {event_type.split('_')[-1]}: {tick_count} ticks, "
                    f"PnL ${total_pnl:+.2f}, {total_trades} trades",
        )

    def create_reflect_event(
        self,
        win_rate: float = 0.0,
        net_pnl: float = 0.0,
        fdr: float = 0.0,
        round_trips: int = 0,
        distilled: str = "",
    ) -> MemoryEvent:
        return MemoryEvent(
            event_type="reflect_review",
            timestamp_ms=self._now_ms(),
            payload={
                "win_rate": round(win_rate, 1),
                "net_pnl": round(net_pnl, 4),
                "fdr": round(fdr, 1),
                "round_trips": round_trips,
            },
            summary=distilled or f"REFLECT: {round_trips} RTs, {win_rate:.0f}% WR, "
                                 f"${net_pnl:+.2f} net, FDR {fdr:.0f}%",
        )

    def create_notable_trade_event(
        self,
        instrument: str,
        direction: str,
        pnl: float,
        roe_pct: float,
        entry_source: str,
        close_reason: str,
    ) -> MemoryEvent:
        return MemoryEvent(
            event_type="notable_trade",
            timestamp_ms=self._now_ms(),
            payload={
                "instrument": instrument,
                "direction": direction,
                "pnl": round(pnl, 4),
                "roe_pct": round(roe_pct, 2),
                "entry_source": entry_source,
                "close_reason": close_reason,
            },
            summary=f"Notable: {instrument} {direction} via {entry_source}, "
                    f"PnL ${pnl:+.2f} ({roe_pct:+.1f}%), exit={close_reason}",
        )

    def create_judge_event(
        self,
        findings_count: int,
        false_positive_rates: Dict[str, float],
        recommendations: List[str],
    ) -> MemoryEvent:
        return MemoryEvent(
            event_type="judge_finding",
            timestamp_ms=self._now_ms(),
            payload={
                "findings_count": findings_count,
                "false_positive_rates": {
                    k: round(v, 1) for k, v in false_positive_rates.items()
                },
                "recommendations": recommendations,
            },
            summary=f"Judge: {findings_count} findings, "
                    f"FP rates: {', '.join(f'{k}={v:.0f}%' for k, v in false_positive_rates.items())}",
        )

    @staticmethod
    def update_playbook(
        playbook: Playbook,
        closed_slots: list,
        now_ms: int = 0,
    ) -> Playbook:
        """Update playbook from closed WolfSlot-like dicts.

        Each slot dict should have: instrument, entry_source, close_pnl,
        current_roe (at close), entry_ts, close_ts.
        """
        now_ms = now_ms or int(time.time() * 1000)
        for slot in closed_slots:
            instrument = slot.get("instrument", "")
            source = slot.get("entry_source", "unknown")
            pnl = slot.get("close_pnl", 0.0)
            roe = slot.get("current_roe", 0.0)
            holding = slot.get("close_ts", 0) - slot.get("entry_ts", 0)

            key = Playbook._key(instrument, source)
            entry = playbook.entries.get(key) or PlaybookEntry(
                instrument=instrument, signal_source=source,
            )

            entry.trade_count += 1
            if pnl > 0:
                entry.win_count += 1
            entry.total_pnl += pnl
            entry.total_roe += roe
            # Running average for holding time
            if entry.trade_count == 1:
                entry.avg_holding_ms = float(holding)
            else:
                entry.avg_holding_ms += (holding - entry.avg_holding_ms) / entry.trade_count
            entry.last_updated_ms = now_ms

            playbook.entries[key] = entry

        return playbook

    @staticmethod
    def query(
        events: List[MemoryEvent],
        event_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[MemoryEvent]:
        """Filter and limit memory events. Returns most recent first."""
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        # Most recent first
        events = sorted(events, key=lambda e: e.timestamp_ms, reverse=True)
        return events[:limit]
