"""Dataclasses for the HOUSE-mode fleet launcher.

Ported from agent-command-center's TypeScript fleet subsystem
(server/src/fleet-supervisor.ts, server/src/standing-aggregator.ts).

The fleet is GENERIC: it spawns `cli.main` subprocesses. These models carry no
strategy-specific logic and have no dependency on cfi_hedge or the strategy-load
runner — fleet members that reference those simply activate once those land.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# Mirrors FleetAgentStatus in fleet-supervisor.ts:
#   "starting" | "active" | "exited" | "errored" | "killed"
FLEET_STATUSES = ("starting", "active", "exited", "errored", "killed")


@dataclass
class FleetMemberSpec:
    """Declarative spec for one fleet member.

    Mirrors FleetMemberSpec in fleet-supervisor.ts.

    `strategy` is either a registry name (e.g. "engine_mm") spawned via
    `run <strategy>`, or the sentinel "__load__" which spawns
    `strategy load <name>` instead (the strategy-load runner lands in a sibling
    PR; until then a "__load__" member exits non-zero, which is reported
    honestly as "errored" — no code dependency is introduced here).
    """

    name: str
    strategy: str
    market: Optional[str] = None
    wallet: Optional[str] = None
    preset: Optional[str] = None
    extra_args: List[str] = field(default_factory=list)


@dataclass
class FleetAgentState:
    """Live (or terminal) state of one spawned fleet member.

    Mirrors FleetAgentState in fleet-supervisor.ts. Times are epoch
    milliseconds to match the TS dashboard contract.
    """

    id: str
    name: str
    strategy: str
    market: Optional[str]
    status: str  # one of FLEET_STATUSES
    pid: Optional[int] = None
    started_at: int = 0
    exited_at: Optional[int] = None
    exit_code: Optional[int] = None
    recent_logs: List[str] = field(default_factory=list)
    error_logs: List[str] = field(default_factory=list)


@dataclass
class FleetStandingMarket:
    """Per-market Standing row.

    Mirrors FleetStandingMarket in standing-aggregator.ts.
    """

    market: str
    notional_24h: float = 0.0
    notional_7d: float = 0.0
    fill_count_24h: int = 0
    fill_count_7d: int = 0
    bc_accrued_24h: float = 0.0
    bc_accrued_7d: float = 0.0


@dataclass
class FleetStandingResult:
    """Aggregate Standing board derived from data/cli/trades.jsonl.

    Mirrors FleetStandingResult in standing-aggregator.ts.
    """

    total_fills: int = 0
    total_notional_24h: float = 0.0
    total_notional_7d: float = 0.0
    total_bc_accrued_24h: float = 0.0
    total_bc_accrued_7d: float = 0.0
    markets: List[FleetStandingMarket] = field(default_factory=list)
    fee_rate_tenths_bps: int = 0
    source: str = "data/cli/trades.jsonl"
    as_of_ms: int = 0
    empty: bool = True
    error: Optional[str] = None
