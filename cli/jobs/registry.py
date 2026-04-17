"""Job registry — canonical definitions for all Perpetual Agent Jobs."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class JobCategory(str, Enum):
    """High-level job classification."""

    KEEPER = "keeper"
    OPERATOR = "operator"
    COOPERATIVE = "cooperative"
    MANAGED = "managed"


class TriggerType(str, Enum):
    """What event starts a job tick."""

    NEW_BLOCK = "new_block"
    ORACLE_UPDATE = "oracle_update"
    EVENT = "event"
    CLEARING_ROUND = "clearing_round"
    TIMER = "timer"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CustodyPolicy(BaseModel):
    """On-chain custody constraints enforced by JobRegistry and CustodyGuard.

    Attributes
    ----------
    destinations:
        Allowed contract addresses the agent may call.
    selectors:
        Allowed 4-byte function selectors (hex, e.g. ``"0x12345678"``).
    value_cap_eth:
        Maximum ETH value per transaction.
    rate_limit_per_block:
        Maximum number of transactions the agent may submit per block.
    """

    destinations: List[str] = Field(default_factory=list)
    selectors: List[str] = Field(default_factory=list)
    value_cap_eth: float = 0.0
    rate_limit_per_block: int = 1


class JobDefinition(BaseModel):
    """Immutable specification for a job type in the registry.

    Attributes
    ----------
    job_id:
        Unique slug (e.g. ``"oracle_updater"``).
    name:
        Human-readable display name.
    description:
        One-line summary of the job's purpose.
    category:
        Job category (keeper, operator, cooperative, managed).
    trigger:
        The event type that initiates a job tick.
    trigger_config:
        Extra parameters for the trigger (e.g. event name, timer interval).
    required_role:
        On-chain role the agent must hold, or ``None`` for permissionless.
    requires_tee:
        Whether TEE attestation is mandatory.
    min_stake_eth:
        Minimum stake in ETH to register for this job.
    custody:
        Custody policy constraining the agent's transaction scope.
    strategy_interface:
        Dotted path to the abstract strategy class the agent must implement.
    context_template:
        Name of the context model passed to the strategy each tick.
    engine_type:
        Which engine class handles execution (``"keeper"``, ``"cooperative"``,
        ``"managed"``).
    """

    job_id: str
    name: str
    description: str = ""
    category: JobCategory
    trigger: TriggerType
    trigger_config: Dict[str, Any] = Field(default_factory=dict)
    required_role: Optional[str] = None
    requires_tee: bool = False
    min_stake_eth: float = 0.0
    custody: CustodyPolicy = Field(default_factory=CustodyPolicy)
    strategy_interface: str = ""
    context_template: str = ""
    engine_type: str = ""


# ---------------------------------------------------------------------------
# Pre-registered jobs
# ---------------------------------------------------------------------------

JOB_REGISTRY: Dict[str, JobDefinition] = {
    # 1. Oracle Updater — KEEPER
    "oracle_updater": JobDefinition(
        job_id="oracle_updater",
        name="Oracle Updater",
        description="Push fresh oracle prices on each new block.",
        category=JobCategory.KEEPER,
        trigger=TriggerType.NEW_BLOCK,
        trigger_config={},
        required_role=None,
        requires_tee=False,
        min_stake_eth=0.0,
        custody=CustodyPolicy(
            destinations=["TODO: deploy address — OracleManager", "TODO: deploy address — PythFeeds"],
            selectors=["0x00000000"],  # TODO: real selector after ABI finalised
            value_cap_eth=0.0,
            rate_limit_per_block=2,
        ),
        strategy_interface="cli.jobs.strategy_interfaces.KeeperStrategy",
        context_template="KeeperContext",
        engine_type="keeper",
    ),
    # 2. Funding Keeper — OPERATOR
    "funding_keeper": JobDefinition(
        job_id="funding_keeper",
        name="Funding Keeper",
        description="Settle funding rates on perpetual markets each block.",
        category=JobCategory.OPERATOR,
        trigger=TriggerType.NEW_BLOCK,
        trigger_config={},
        required_role="AUTHORIZED",
        requires_tee=False,
        min_stake_eth=10.0,
        custody=CustodyPolicy(
            destinations=["TODO: deploy address — MarketRegistry"],
            selectors=["0x00000000"],  # TODO: real selector after ABI finalised
            value_cap_eth=0.0,
            rate_limit_per_block=1,
        ),
        strategy_interface="cli.jobs.strategy_interfaces.KeeperStrategy",
        context_template="KeeperContext",
        engine_type="keeper",
    ),
    # 3. Liquidation Flagger — KEEPER
    "liq_flagger": JobDefinition(
        job_id="liq_flagger",
        name="Liquidation Flagger",
        description="Flag under-collateralised positions for liquidation.",
        category=JobCategory.KEEPER,
        trigger=TriggerType.ORACLE_UPDATE,
        trigger_config={},
        required_role=None,
        requires_tee=False,
        min_stake_eth=0.0,
        custody=CustodyPolicy(
            destinations=["TODO: deploy address — LiquidationModule"],
            selectors=["0x00000000", "0x00000001"],  # flagPosition, flagAccount
            value_cap_eth=0.0,
            rate_limit_per_block=5,
        ),
        strategy_interface="cli.jobs.strategy_interfaces.KeeperStrategy",
        context_template="KeeperContext",
        engine_type="keeper",
    ),
    # 4. Liquidation Executor — OPERATOR
    "liq_executor": JobDefinition(
        job_id="liq_executor",
        name="Liquidation Executor",
        description="Execute liquidations on flagged positions.",
        category=JobCategory.OPERATOR,
        trigger=TriggerType.EVENT,
        trigger_config={"event_name": "PositionFlagged"},
        required_role="OPERATOR_ROLE",
        requires_tee=False,
        min_stake_eth=50.0,
        custody=CustodyPolicy(
            destinations=["TODO: deploy address — LiquidationModule"],
            selectors=[
                "0x00000000",  # liquidatePosition
                "0x00000001",  # liquidateAccount
                "0x00000002",  # liquidatePositions
            ],
            value_cap_eth=0.0,
            rate_limit_per_block=10,
        ),
        strategy_interface="cli.jobs.strategy_interfaces.KeeperStrategy",
        context_template="KeeperContext",
        engine_type="keeper",
    ),
    # 5. TP/SL Agent — KEEPER
    "tpsl_agent": JobDefinition(
        job_id="tpsl_agent",
        name="TP/SL Agent",
        description="Execute take-profit / stop-loss orders on behalf of delegators.",
        category=JobCategory.KEEPER,
        trigger=TriggerType.ORACLE_UPDATE,
        trigger_config={},
        required_role=None,  # delegated via passport
        requires_tee=False,
        min_stake_eth=1.0,
        custody=CustodyPolicy(
            destinations=["TODO: deploy address — Orderbook"],
            selectors=[
                "0x00000000",  # placeOrder REDUCE_ONLY
                "0x00000001",  # closeOrder
            ],
            value_cap_eth=0.0,
            rate_limit_per_block=3,
        ),
        strategy_interface="cli.jobs.strategy_interfaces.KeeperStrategy",
        context_template="KeeperContext",
        engine_type="keeper",
    ),
    # 6. Market Maker — COOPERATIVE
    "market_maker": JobDefinition(
        job_id="market_maker",
        name="Market Maker",
        description="Provide two-sided liquidity via TEE-cleared cooperative rounds.",
        category=JobCategory.COOPERATIVE,
        trigger=TriggerType.CLEARING_ROUND,
        trigger_config={},
        required_role=None,  # TEE + Stake gated
        requires_tee=True,
        min_stake_eth=100.0,
        custody=CustodyPolicy(
            destinations=["TODO: deploy address — ClearingHouse (via enclave)"],
            selectors=["0x00000000"],  # enclave-mediated
            value_cap_eth=0.0,
            rate_limit_per_block=1,
        ),
        strategy_interface="cli.jobs.strategy_interfaces.CooperativeStrategy",
        context_template="StrategyContext",
        engine_type="cooperative",
    ),
    # 7. ABM Agent — COOPERATIVE
    "abm_agent": JobDefinition(
        job_id="abm_agent",
        name="ABM Agent",
        description="Automated bin management for concentrated liquidity via TEE.",
        category=JobCategory.COOPERATIVE,
        trigger=TriggerType.ORACLE_UPDATE,
        trigger_config={"deviation_threshold_bps": 50},
        required_role=None,  # TEE + Stake gated
        requires_tee=True,
        min_stake_eth=50.0,
        custody=CustodyPolicy(
            destinations=[
                "TODO: deploy address — BinManager",
                "TODO: deploy address — AMM",
            ],
            selectors=["0x00000000"],  # enclave-mediated
            value_cap_eth=0.0,
            rate_limit_per_block=1,
        ),
        strategy_interface="cli.jobs.strategy_interfaces.CooperativeStrategy",
        context_template="StrategyContext",
        engine_type="cooperative",
    ),
    # 8. GLV Manager — MANAGED
    "glv_manager": JobDefinition(
        job_id="glv_manager",
        name="GLV Capital Manager",
        description="Manage vault capital allocation, deposits, withdrawals, and harvesting.",
        category=JobCategory.MANAGED,
        trigger=TriggerType.TIMER,
        trigger_config={"interval_s": 60, "also_on_event": "WithdrawRequested"},
        required_role="MANAGER_ROLE",
        requires_tee=False,
        min_stake_eth=100.0,
        custody=CustodyPolicy(
            destinations=["TODO: deploy address — Glv"],
            selectors=["0x00000000"],  # TODO: real selectors after ABI finalised
            value_cap_eth=10.0,
            rate_limit_per_block=2,
        ),
        strategy_interface="cli.jobs.strategy_interfaces.ManagedStrategy",
        context_template="ManagedContext",
        engine_type="managed",
    ),
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_job(job_id: str) -> JobDefinition:
    """Look up a job definition by its identifier.

    Parameters
    ----------
    job_id:
        The unique slug of the job (e.g. ``"oracle_updater"``).

    Returns
    -------
    JobDefinition

    Raises
    ------
    KeyError
        If *job_id* is not found in the registry.
    """
    if job_id not in JOB_REGISTRY:
        raise KeyError(f"Unknown job: {job_id!r}. Available: {', '.join(JOB_REGISTRY)}")
    return JOB_REGISTRY[job_id]


def list_jobs() -> List[JobDefinition]:
    """Return all registered job definitions.

    Returns
    -------
    list[JobDefinition]
        Every job in the registry, in insertion order.
    """
    return list(JOB_REGISTRY.values())


def list_jobs_by_category(category: JobCategory) -> List[JobDefinition]:
    """Return job definitions filtered by category.

    Parameters
    ----------
    category:
        The :class:`JobCategory` to filter on.

    Returns
    -------
    list[JobDefinition]
        Jobs matching the requested category.
    """
    return [j for j in JOB_REGISTRY.values() if j.category == category]
