"""Job status tracking — heartbeat state, reward accumulation, and persistence."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from cli.jobs.engines import JobStatus


@dataclass
class HeartbeatRecord:
    """A single heartbeat record."""

    block_number: int
    timestamp_ms: int
    success: bool
    tx_hash: Optional[str] = None


@dataclass
class RewardRecord:
    """A single reward claim record."""

    amount_eth: float
    block_number: int
    timestamp_ms: int
    tx_hash: str = ""


@dataclass
class JobStatusTracker:
    """Tracks heartbeat and reward history for a running job.

    Persists state to a JSON file in the job's data directory so that
    status can be read by ``hl jobs status`` even from a separate process.
    """

    job_id: str
    agent_id: str
    data_dir: str = "data/jobs"
    heartbeats: List[HeartbeatRecord] = field(default_factory=list)
    rewards: List[RewardRecord] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    # --- persistence ---

    def _state_path(self) -> Path:
        return Path(self.data_dir) / self.job_id / "status.json"

    def save(self) -> None:
        """Persist current status to disk."""
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "started_at": self.started_at,
            "heartbeat_count": len(self.heartbeats),
            "last_heartbeat": (
                {
                    "block": self.heartbeats[-1].block_number,
                    "ts": self.heartbeats[-1].timestamp_ms,
                    "ok": self.heartbeats[-1].success,
                }
                if self.heartbeats
                else None
            ),
            "total_rewards_eth": sum(r.amount_eth for r in self.rewards),
            "reward_count": len(self.rewards),
        }
        path.write_text(json.dumps(state, indent=2))

    @classmethod
    def load(cls, job_id: str, data_dir: str = "data/jobs") -> Optional["JobStatusTracker"]:
        """Load status from disk. Returns None if no status file exists."""
        path = Path(data_dir) / job_id / "status.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        tracker = cls(
            job_id=data["job_id"],
            agent_id=data["agent_id"],
            data_dir=data_dir,
            started_at=data.get("started_at", 0.0),
        )
        return tracker

    # --- recording ---

    def record_heartbeat(self, block_number: int, success: bool, tx_hash: Optional[str] = None) -> None:
        """Record a heartbeat and auto-save."""
        self.heartbeats.append(
            HeartbeatRecord(
                block_number=block_number,
                timestamp_ms=int(time.time() * 1000),
                success=success,
                tx_hash=tx_hash,
            )
        )
        self.save()

    def record_reward(self, amount_eth: float, block_number: int, tx_hash: str = "") -> None:
        """Record a reward claim and auto-save."""
        self.rewards.append(
            RewardRecord(
                amount_eth=amount_eth,
                block_number=block_number,
                timestamp_ms=int(time.time() * 1000),
                tx_hash=tx_hash,
            )
        )
        self.save()

    # --- queries ---

    def total_rewards(self) -> float:
        """Total ETH rewards claimed."""
        return sum(r.amount_eth for r in self.rewards)

    def uptime_seconds(self) -> float:
        """Seconds since job started."""
        return time.time() - self.started_at

    def last_heartbeat_age_s(self) -> Optional[float]:
        """Seconds since last heartbeat, or None if no heartbeats."""
        if not self.heartbeats:
            return None
        return (time.time() * 1000 - self.heartbeats[-1].timestamp_ms) / 1000


def read_all_job_statuses(data_dir: str = "data/jobs") -> Dict[str, dict]:
    """Read status files for all jobs in the data directory."""
    results: Dict[str, dict] = {}
    base = Path(data_dir)
    if not base.exists():
        return results
    for job_dir in base.iterdir():
        if not job_dir.is_dir():
            continue
        status_file = job_dir / "status.json"
        if status_file.exists():
            try:
                results[job_dir.name] = json.loads(status_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    return results
