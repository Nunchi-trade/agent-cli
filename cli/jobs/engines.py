"""Job execution engines — one per job category.

All method bodies raise ``NotImplementedError`` — this is a design PR skeleton.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field

from cli.jobs.config import JobConfig
from cli.jobs.registry import JobCategory, JobDefinition


# ---------------------------------------------------------------------------
# Status model
# ---------------------------------------------------------------------------


class JobStatus(BaseModel):
    """Snapshot of a running job engine's operational state.

    Attributes
    ----------
    job_id:
        The job type identifier.
    agent_id:
        This agent's unique identifier.
    running:
        Whether the engine event loop is active.
    category:
        Job category string (keeper, operator, cooperative, managed).
    ticks_processed:
        Number of ticks / events processed since start.
    last_heartbeat_block:
        Block number of the most recent heartbeat.
    accumulated_reward_eth:
        Total unclaimed rewards in ETH.
    events_received:
        Total events received from the subscriber.
    txs_submitted:
        Total transactions submitted to the chain.
    txs_succeeded:
        Total transactions that succeeded on-chain.
    txs_failed:
        Total transactions that reverted or failed.
    uptime_seconds:
        Seconds since the engine was started.
    error:
        Most recent error message, or ``None``.
    """

    job_id: str
    agent_id: str
    running: bool = False
    category: str = ""
    ticks_processed: int = 0
    last_heartbeat_block: int = 0
    accumulated_reward_eth: float = 0.0
    events_received: int = 0
    txs_submitted: int = 0
    txs_succeeded: int = 0
    txs_failed: int = 0
    uptime_seconds: float = 0.0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Abstract base engine
# ---------------------------------------------------------------------------


class JobEngine(ABC):
    """Base class for all job execution engines.

    Subclasses implement the event loop, heartbeat, and shutdown logic
    appropriate to their job category.
    """

    def __init__(self, job_def: JobDefinition) -> None:
        """Initialise the engine with a job definition.

        Parameters
        ----------
        job_def:
            The immutable specification for the job this engine will run.
        """
        self._job_def = job_def

    @abstractmethod
    def start(self, config: JobConfig) -> None:
        """Start the engine's event loop.

        Parameters
        ----------
        config:
            Runtime configuration (RPC endpoints, strategy, TEE settings, etc.).
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Gracefully stop the engine and release resources."""
        ...

    @abstractmethod
    def heartbeat(self) -> None:
        """Send a heartbeat to the on-chain JobRegistry contract."""
        ...

    @abstractmethod
    def status(self) -> JobStatus:
        """Return a snapshot of the engine's current operational state."""
        ...


# ---------------------------------------------------------------------------
# Keeper / Operator engine
# ---------------------------------------------------------------------------


class KeeperEngine(JobEngine):
    """Engine for KEEPER and OPERATOR jobs.

    Event-driven execution: subscribes to chain events, evaluates a
    ``KeeperStrategy``, and submits transactions through a ``CustodyGuard``.
    """

    def __init__(self, job_def: JobDefinition) -> None:
        super().__init__(job_def)
        self._subscriber = None  # EventSubscriber — set in start()
        self._strategy = None    # KeeperStrategy — loaded from config
        self._custody_guard = None  # CustodyGuard — built from job_def.custody
        self._status = JobStatus(job_id=job_def.job_id, agent_id="")

    def start(self, config: JobConfig) -> None:
        """Connect event subscriber, load strategy, enter event loop.

        Parameters
        ----------
        config:
            Runtime configuration for this job instance.
        """
        raise NotImplementedError("Implementation deferred — design PR only")

    def stop(self) -> None:
        """Unsubscribe from events and flush any pending state."""
        raise NotImplementedError("Implementation deferred — design PR only")

    def heartbeat(self) -> None:
        """Call JobRegistry.heartbeat() on-chain."""
        raise NotImplementedError("Implementation deferred — design PR only")

    def status(self) -> JobStatus:
        """Return current status with event and transaction counts."""
        raise NotImplementedError("Implementation deferred — design PR only")


# ---------------------------------------------------------------------------
# Cooperative engine
# ---------------------------------------------------------------------------


class CooperativeEngine(JobEngine):
    """Engine for COOPERATIVE jobs.

    Wraps the tee-work-llm ``AgentClient`` to participate in TEE-cleared
    cooperative rounds.  The AgentClient is imported lazily since the
    ``tee-work-llm`` package may not be installed.
    """

    def __init__(self, job_def: JobDefinition) -> None:
        super().__init__(job_def)
        self._client = None   # AgentClient — created in start()
        self._strategy = None  # CooperativeStrategy — loaded from config
        self._status = JobStatus(job_id=job_def.job_id, agent_id="")

    def start(self, config: JobConfig) -> None:
        """Import AgentClient, create client, optionally attest, enter round loop.

        Parameters
        ----------
        config:
            Runtime configuration including relay URL and TEE settings.
        """
        # Lazy import — tee-work-llm may not be installed
        try:
            from agent.client import AgentClient  # noqa: F401
        except ImportError:
            raise ImportError(
                "tee-work-llm is required for cooperative jobs. "
                "Install with: pip install tee-work-llm"
            )
        raise NotImplementedError("Implementation deferred — design PR only")

    def stop(self) -> None:
        """Gracefully shut down the AgentClient."""
        raise NotImplementedError("Implementation deferred — design PR only")

    def heartbeat(self) -> None:
        """Send heartbeat between clearing rounds."""
        raise NotImplementedError("Implementation deferred — design PR only")

    def status(self) -> JobStatus:
        """Return status with round count, participation rate, and PnL."""
        raise NotImplementedError("Implementation deferred — design PR only")


# ---------------------------------------------------------------------------
# Managed engine
# ---------------------------------------------------------------------------


class ManagedEngine(JobEngine):
    """Engine for MANAGED jobs.

    Timer + event hybrid: runs a periodic evaluation cycle and also
    reacts to specific on-chain events (e.g. ``WithdrawRequested``).
    """

    def __init__(self, job_def: JobDefinition) -> None:
        super().__init__(job_def)
        self._timer = None      # asyncio timer handle
        self._subscriber = None  # EventSubscriber — for reactive events
        self._strategy = None    # ManagedStrategy — loaded from config
        self._status = JobStatus(job_id=job_def.job_id, agent_id="")

    def start(self, config: JobConfig) -> None:
        """Set up periodic timer and event subscriber, enter evaluation loop.

        Parameters
        ----------
        config:
            Runtime configuration for this managed job instance.
        """
        raise NotImplementedError("Implementation deferred — design PR only")

    def stop(self) -> None:
        """Cancel timer and unsubscribe from events."""
        raise NotImplementedError("Implementation deferred — design PR only")

    def heartbeat(self) -> None:
        """Send heartbeat on each evaluation cycle."""
        raise NotImplementedError("Implementation deferred — design PR only")

    def status(self) -> JobStatus:
        """Return status with actions taken and vault state."""
        raise NotImplementedError("Implementation deferred — design PR only")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class JobEngineFactory:
    """Routes job definitions to the correct engine implementation."""

    @staticmethod
    def create(job_def: JobDefinition) -> JobEngine:
        """Create the appropriate engine for a given job definition.

        Parameters
        ----------
        job_def:
            The job definition specifying the category and configuration.

        Returns
        -------
        JobEngine
            A concrete engine instance ready to be started.

        Raises
        ------
        ValueError
            If the job category is not recognised.
        """
        if job_def.category in (JobCategory.KEEPER, JobCategory.OPERATOR):
            return KeeperEngine(job_def)
        elif job_def.category == JobCategory.COOPERATIVE:
            return CooperativeEngine(job_def)
        elif job_def.category == JobCategory.MANAGED:
            return ManagedEngine(job_def)
        else:
            raise ValueError(f"Unknown job category: {job_def.category}")
