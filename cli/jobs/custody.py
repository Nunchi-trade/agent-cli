"""Custody policy enforcement — defense-in-depth pre-signing guard.

The :class:`CustodyGuard` validates every transaction against the job's
:class:`CustodyPolicy` before it is signed and submitted, enforcing
destination, selector, value, and rate-limit constraints.
"""
from __future__ import annotations

from typing import Optional

from cli.jobs.registry import CustodyPolicy
from cli.jobs.strategy_interfaces import Transaction


class CustodyViolation(Exception):
    """Raised when a transaction violates the job's custody policy.

    Attributes
    ----------
    reason:
        Human-readable description of which constraint was violated.
    tx:
        The offending transaction.
    """

    def __init__(self, reason: str, tx: Optional[Transaction] = None) -> None:
        self.reason = reason
        self.tx = tx
        super().__init__(reason)


class CustodyGuard:
    """Pre-signing custody policy enforcement.

    Acts as a defense-in-depth layer: even though the on-chain
    ``JobRegistry`` contract enforces custody constraints, the guard
    catches violations locally before signing, saving gas and preventing
    accidental mis-use.

    Parameters
    ----------
    policy:
        The :class:`CustodyPolicy` for the active job.
    """

    def __init__(self, policy: CustodyPolicy) -> None:
        self._policy = policy
        self._tx_count_this_block: int = 0
        self._current_block: int = 0

    def validate(self, tx: Transaction) -> bool:
        """Check a transaction against the custody policy.

        Validates four constraints in order:

        1. ``tx.to`` must be in ``policy.destinations``.
        2. The first 4 bytes of ``tx.data`` (function selector) must be
           in ``policy.selectors``.
        3. ``tx.value_wei`` must not exceed ``policy.value_cap_eth * 1e18``.
        4. The per-block rate limit must not be exceeded.

        Parameters
        ----------
        tx:
            The transaction to validate.

        Returns
        -------
        bool
            ``True`` if the transaction passes all checks.

        Raises
        ------
        CustodyViolation
            If any constraint is violated.
        """
        raise NotImplementedError("Implementation deferred — design PR only")

    def reset_rate_limit(self) -> None:
        """Reset the per-block rate-limit counter.

        Call this method at the start of each new block to allow the
        agent to submit transactions for the new block.
        """
        raise NotImplementedError("Implementation deferred — design PR only")
