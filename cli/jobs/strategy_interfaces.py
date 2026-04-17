"""Strategy interfaces for Perpetual Agent Jobs.

Defines the abstract contracts that job strategies must implement:

- :class:`KeeperStrategy` — event-driven, stateless (keepers and operators).
- :class:`CooperativeStrategy` — round-based, extends :class:`BaseStrategy`.
- :class:`ManagedStrategy` — capital-management for vaults.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from sdk.strategy_sdk.base import BaseStrategy


# ---------------------------------------------------------------------------
# Event / context models
# ---------------------------------------------------------------------------


class ChainEvent(BaseModel):
    """An on-chain event received from the event bus.

    Attributes
    ----------
    event_type:
        Discriminator string, e.g. ``"NewBlock"``, ``"OracleUpdate"``,
        ``"PositionFlagged"``.
    block_number:
        Block height at which the event was emitted.
    tx_hash:
        Originating transaction hash, if applicable.
    data:
        Arbitrary decoded event data.
    timestamp_ms:
        Unix timestamp in milliseconds.
    """

    event_type: str
    block_number: int
    tx_hash: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp_ms: int = 0


class KeeperContext(BaseModel):
    """Context provided to keeper / operator strategies per event.

    Attributes
    ----------
    event:
        The triggering chain event.
    chain_state:
        Job-specific on-chain state snapshot (e.g. oracle prices, positions).
    gas_price_gwei:
        Current gas price for profitability checks.
    agent_balance_eth:
        Agent wallet balance in ETH.
    """

    event: ChainEvent
    chain_state: Dict[str, Any] = Field(default_factory=dict)
    gas_price_gwei: float = 0.0
    agent_balance_eth: float = 0.0


class Transaction(BaseModel):
    """A transaction to submit to the chain.

    Attributes
    ----------
    to:
        Target contract address (checksummed hex).
    data:
        ABI-encoded calldata (hex string, ``"0x..."``).
    value_wei:
        ETH value to send, in wei.
    gas_limit:
        Gas limit. ``0`` means the engine should estimate.
    """

    to: str
    data: str
    value_wei: int = 0
    gas_limit: int = 0


# ---------------------------------------------------------------------------
# Keeper / Operator strategy
# ---------------------------------------------------------------------------


class KeeperStrategy(ABC):
    """Strategy interface for Keeper and Operator jobs.

    Event-driven and stateless: receives a chain event with context,
    decides whether to submit a transaction.
    """

    @abstractmethod
    def should_execute(
        self, event: ChainEvent, context: KeeperContext
    ) -> Optional[Transaction]:
        """Evaluate a chain event and optionally produce a transaction.

        Parameters
        ----------
        event:
            The chain event that triggered this evaluation.
        context:
            Contextual data including on-chain state and gas info.

        Returns
        -------
        Transaction or None
            A transaction to submit, or ``None`` to skip.
        """
        ...

    def on_execution_result(
        self, tx_hash: str, success: bool, gas_used: int
    ) -> None:
        """Optional callback invoked after a transaction is executed.

        Override to implement logging, metrics, or adaptive behaviour.

        Parameters
        ----------
        tx_hash:
            Hash of the submitted transaction.
        success:
            Whether the transaction succeeded on-chain.
        gas_used:
            Actual gas consumed.
        """
        pass


# ---------------------------------------------------------------------------
# Managed infrastructure strategy
# ---------------------------------------------------------------------------


class CapitalAction(BaseModel):
    """A capital-management action emitted by a managed strategy.

    Attributes
    ----------
    action_type:
        One of ``"deposit"``, ``"withdraw"``, ``"harvest"``.
    target_contract:
        Address of the contract to interact with.
    calldata:
        ABI-encoded calldata (hex string).
    value_wei:
        ETH value to send, in wei.
    priority:
        Execution priority (higher = more urgent).
    """

    action_type: str
    target_contract: str
    calldata: str
    value_wei: int = 0
    priority: int = 0


class ManagedContext(BaseModel):
    """Context provided to managed infrastructure strategies.

    Attributes
    ----------
    vault_balance_usd:
        Current USD balance held in the vault contract.
    pending_withdrawals:
        List of pending withdrawal requests with amounts and deadlines.
    total_assets_usd:
        Total assets under management in USD.
    clearing_account_balance_usd:
        Balance in the clearing account used for active trading.
    last_harvest_timestamp:
        Unix timestamp of the last harvest operation.
    block_number:
        Current block height.
    """

    vault_balance_usd: float = 0.0
    pending_withdrawals: List[Dict[str, Any]] = Field(default_factory=list)
    total_assets_usd: float = 0.0
    clearing_account_balance_usd: float = 0.0
    last_harvest_timestamp: int = 0
    block_number: int = 0


class ManagedStrategy(ABC):
    """Strategy interface for Managed Infrastructure jobs (e.g. GLV Capital Manager).

    Periodically evaluates vault state and returns a list of capital actions.
    """

    @abstractmethod
    def evaluate(self, context: ManagedContext) -> List[CapitalAction]:
        """Evaluate current vault state and return capital management actions.

        Parameters
        ----------
        context:
            Current vault and clearing account state.

        Returns
        -------
        list[CapitalAction]
            Zero or more actions to execute, ordered by priority.
        """
        ...


# ---------------------------------------------------------------------------
# Cooperative strategy (extends BaseStrategy)
# ---------------------------------------------------------------------------


class CooperativeStrategy(BaseStrategy):
    """Strategy for House Cooperative jobs.

    Extends :class:`BaseStrategy` (which provides ``on_tick``) with
    post-clearing round feedback for adaptive learning.
    """

    def on_round_result(self, result: Any) -> None:
        """Receive post-clearing feedback after a round completes.

        Override this method to implement adaptive learning based on
        clearing outcomes, fills, and PnL attribution.

        Parameters
        ----------
        result:
            Clearing result data (format TBD by clearing layer).
        """
        pass
