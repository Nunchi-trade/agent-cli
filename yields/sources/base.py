"""The `YieldSource` abstract base — the contract every discovery source obeys.

A source's `discover()` MUST be defensive: it catches its own network/parse
errors and returns whatever it could (possibly an empty list) — it never
raises to the aggregator, so one flaky source cannot break a scan.

Read-only sources (DeFiLlama) implement only `discover()`. On-chain adapters
additionally implement `get_position` / `build_deposit` / `build_withdraw` and
report `supports() == True` for the opportunities they can route.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Optional, Sequence

from yields.models import Chain, Position, RouteStep, SourceTier, YieldOpportunity


class NotSupported(RuntimeError):
    """Raised when a YieldSource is asked for an operation it does not implement."""


class YieldSource(ABC):
    """Base class for every yield-discovery source."""

    #: discovery tier — set by each concrete source
    tier: ClassVar[SourceTier]
    #: short stable identifier, e.g. "defillama" or "aave-v3"
    name: ClassVar[str]

    @abstractmethod
    def discover(self, chains: Sequence[Chain]) -> list[YieldOpportunity]:
        """Return opportunities on ``chains``.

        Implementations MUST catch their own network and parse errors and
        return a (possibly empty, possibly partial) list — never raise.
        """

    def supports(self, opp: YieldOpportunity) -> bool:
        """True when this source can route (deposit/withdraw) ``opp``.

        Read-only sources leave this False; on-chain adapters override it.
        """
        return False

    # --- execution surface — on-chain adapters override these ---------------
    def get_position(self, wallet: str, opp: YieldOpportunity) -> Optional[Position]:
        raise NotSupported(f"{self.name} does not implement get_position")

    def build_deposit(
        self, opp: YieldOpportunity, amount: int, wallet: str
    ) -> list[RouteStep]:
        """Build the unsigned deposit route for ``amount`` (raw base units of
        the opportunity's underlying)."""
        raise NotSupported(f"{self.name} does not implement build_deposit")

    def build_withdraw(
        self, opp: YieldOpportunity, position: Position, wallet: str
    ) -> list[RouteStep]:
        """Build the unsigned withdraw route for an open ``position``."""
        raise NotSupported(f"{self.name} does not implement build_withdraw")

    # --- approval surface — adapters override only when non-standard --------
    def required_deposit_approvals(
        self, opp: YieldOpportunity, amount: int
    ) -> list[tuple[str, str, int]]:
        """ERC20 approvals a deposit needs before ``build_deposit`` will succeed.

        Each tuple is ``(token_address, spender_address, amount)``. The default
        covers the common case — supply/deposit a single ERC20 underlying to
        the opportunity's ``pool_address`` — and returns ``[]`` when the
        underlying token address or the pool address is unknown (a
        DeFiLlama-only row, or a native-ETH deposit). Adapters whose deposit
        approves a *different* token (Lido wraps stETH, not the ETH underlying)
        override this.
        """
        if (
            opp.underlying
            and opp.underlying[0].address
            and opp.pool_address
        ):
            return [(opp.underlying[0].address, opp.pool_address, int(amount))]
        return []

    def required_withdraw_approvals(
        self, opp: YieldOpportunity, position: Position
    ) -> list[tuple[str, str, int]]:
        """ERC20 approvals a withdraw needs before ``build_withdraw`` succeeds.

        The default is ``[]`` — money-market and ERC-4626 withdrawals burn a
        receipt token the protocol already controls, so no approval is needed.
        Adapters that must approve a token to a withdrawal contract (Lido
        approves wstETH to the WithdrawalQueue) override this.
        """
        return []
