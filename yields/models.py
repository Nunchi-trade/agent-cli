"""Data models for the yields package — pydantic v2 DTOs.

Conventions:
- token amounts in raw integer base units (wei-like) are ``int`` — no float drift;
- USD values and APYs are ``float`` (fractions: 0.043 == 4.3%);
- these models carry data only — no network or chain I/O — so the pure optimizer
  and `risk.py` that consume them stay trivially portable.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, Field


class Chain(str, Enum):
    ethereum = "ethereum"
    base = "base"


class YieldKind(str, Enum):
    lending = "lending"   # supply to a money market (Aave, Compound, Moonwell)
    staking = "staking"   # liquid staking (Lido)
    vault = "vault"       # ERC-4626 / savings vault (Sky sDAI / sUSDS)
    lp = "lp"             # AMM liquidity position
    other = "other"


class SourceTier(str, Enum):
    defillama = "defillama"  # broad discovery, read-only
    onchain = "onchain"      # backed by an executable on-chain adapter


def canonical_id(
    chain: str,
    protocol: str,
    underlying_addresses: Sequence[str],
    pool_address: Optional[str],
    kind: str,
) -> str:
    """Deterministic dedup key for a YieldOpportunity.

    The same pool seen from two sources resolves to the same id. Keyed on
    chain + normalized protocol slug + the sorted underlying token addresses +
    (the pool address if known, else the kind). Pass ``chain``/``kind`` as the
    enum *values* (strings).
    """
    under = ",".join(sorted((a or "").lower() for a in underlying_addresses if a))
    tail = (pool_address or "").lower() or (kind or "")
    return f"{chain}:{protocol}:{under}:{tail}"


class TokenRef(BaseModel):
    """A reference to an ERC20 token. ``address``/``decimals`` may be absent on
    DeFiLlama-only rows (the API gives symbols, not addresses)."""

    symbol: str
    chain: Chain
    address: Optional[str] = None        # checksummed when known
    decimals: Optional[int] = None


class YieldOpportunity(BaseModel):
    """One yield opportunity, normalized across discovery sources."""

    id: str                              # canonical_id(...) — the dedup key
    protocol: str                        # normalized slug: "aave-v3", "lido", ...
    chain: Chain
    kind: YieldKind
    pool_address: Optional[str] = None   # the deposit-target contract (None for DeFiLlama-only)
    underlying: list[TokenRef] = Field(default_factory=list)
    receipt_token: Optional[TokenRef] = None  # aToken / wstETH / sDAI / mToken
    apy_base: float = 0.0                # organic supply/staking APY (fraction)
    apy_reward: float = 0.0              # incentive APY (fraction; volatile)
    tvl_usd: float = 0.0
    source_tier: SourceTier
    has_onchain_adapter: bool = False    # True => routable for execution
    risk_score: float = 0.0              # 0..1 (lower = safer); filled by risk.py in Phase 3
    fetched_at_ms: int = 0               # when this row's data was sourced
    raw: dict[str, Any] = Field(default_factory=dict)  # provenance / original payload

    @property
    def apy_total(self) -> float:
        """Gross APY before cost and risk adjustment."""
        return self.apy_base + self.apy_reward


class Position(BaseModel):
    """A wallet's open position in a yield opportunity."""

    opportunity_id: str
    protocol: str
    chain: Chain
    wallet: str
    receipt_token: Optional[TokenRef] = None
    receipt_balance: int = 0             # raw base units of the receipt token
    underlying_value_usd: float = 0.0
    accrued_reward_usd: float = 0.0


class RouteStep(BaseModel):
    """One unsigned step of an execution route. Built by sources / the router;
    the calldata is handed to ``common.evm.TxExecutor`` — never sent here."""

    kind: str                            # "approve" | "swap" | "deposit" | "withdraw"
    chain: Chain
    target: str                          # contract address the calldata is sent to
    description: str = ""
    calldata: str = "0x"                 # 0x-hex
    value_wei: int = 0                   # native value attached to the call
    gas_estimate: Optional[int] = None
    simulated_ok: Optional[bool] = None
    sim_error: Optional[str] = None


class RouteResult(BaseModel):
    """The router's output — an assembled (and possibly executed) route.

    Carries the ordered ``RouteStep``s plus the lifecycle flags the CLI / a
    harness reads: whether the route was simulated, whether it was broadcast,
    the resulting on-chain tx hashes, and any error. ``ok=False`` with a set
    ``error`` marks a non-executable route (e.g. a DeFiLlama-only opportunity
    with no on-chain adapter)."""

    opportunity_id: str
    action: str                          # "deposit" | "withdraw"
    steps: list[RouteStep] = Field(default_factory=list)
    simulated: bool = False              # preview() ran a dry-run over the steps
    broadcast: bool = False              # the steps were sent on-chain
    tx_hashes: list[str] = Field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None


class AllocationEntry(BaseModel):
    """One line of an allocation plan — capital assigned to one opportunity."""

    opportunity_id: str
    protocol: str
    chain: Chain
    amount_usd: float
    expected_net_apy: float              # APY after gas amortization + risk haircut
    risk_score: float


class AllocationPlan(BaseModel):
    """The optimizer's output — how to spread a budget across opportunities."""

    budget_usd: float
    asset: str = "USDC"
    entries: list[AllocationEntry] = Field(default_factory=list)
    unallocated_usd: float = 0.0
    blended_net_apy: float = 0.0
    blended_risk: float = 0.0
    constraints: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)  # human-readable: which constraints bound
