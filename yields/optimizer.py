"""yields.optimizer — pure ranking and capital allocation.

PURE module: inputs to outputs, no network / chain / clock / env. It imports
only the models and the stdlib, so `/propagate-cli-to-fi` can port it to
TypeScript as a near-mechanical transliteration.

Net APY of a position — the objective the optimizer maximizes::

    net_apy = apy_base + apy_reward - gas_amortized - risk_haircut

    gas_amortized = (gas_cost_usd / ticket_usd) * (365 / holding_period_days)
    risk_haircut  = risk_lambda * risk_score

Gas amortization makes a thin ticket spread over many pools unattractive (gas
eats it); the risk haircut discounts risky pools inside the ranking objective,
not merely as a post-filter.

`optimize()` is greedy-with-constraints: rank by net APY, then fill the highest
first, subject to a per-protocol concentration cap, a position-count cap, and a
minimum ticket. v1 is deliberately greedy and explainable; a convex /
mean-variance upgrade is noted in the plan as v1.1.
"""
from __future__ import annotations

from dataclasses import dataclass

from yields.models import (
    AllocationEntry,
    AllocationPlan,
    Chain,
    YieldKind,
    YieldOpportunity,
)

_DEFAULT_NOTIONAL_USD = 10_000.0


@dataclass(frozen=True)
class OptimizerConstraints:
    """Knobs for `rank()` / `optimize()`. All have conservative defaults."""

    min_net_apy: float = 0.0
    max_risk_score: float = 1.0
    max_protocol_pct: float = 0.40        # cap per protocol, as a fraction of budget
    max_positions: int = 6
    min_ticket_usd: float = 500.0         # do not dust-allocate — gas would eat it
    allowed_chains: tuple[Chain, ...] = (Chain.ethereum, Chain.base)
    allowed_kinds: tuple[YieldKind, ...] | None = None
    excluded_protocols: tuple[str, ...] = ()
    gas_cost_usd: float = 15.0            # amortized entry + exit gas per position
    holding_period_days: float = 30.0
    risk_lambda: float = 0.15             # APY penalty per unit of risk score
    #   (risk 1.0 -> -15% APY; risk 0.3 -> -4.5%). Raise it for more risk-aversion,
    #   or use max_risk_score to hard-exclude.


DEFAULT_CONSTRAINTS = OptimizerConstraints()


def gas_amortized_apy(ticket_usd: float, cons: OptimizerConstraints) -> float:
    """Annualized gas drag for a position of ``ticket_usd``."""
    if ticket_usd <= 0:
        return float("inf")
    return (cons.gas_cost_usd / ticket_usd) * (365.0 / cons.holding_period_days)


def net_apy(
    opp: YieldOpportunity, ticket_usd: float, cons: OptimizerConstraints
) -> float:
    """Risk- and cost-adjusted APY for holding ``opp`` at ``ticket_usd``."""
    gross = opp.apy_base + opp.apy_reward
    return gross - gas_amortized_apy(ticket_usd, cons) - cons.risk_lambda * opp.risk_score


def _passes_filters(opp: YieldOpportunity, cons: OptimizerConstraints) -> bool:
    if opp.chain not in cons.allowed_chains:
        return False
    if cons.allowed_kinds is not None and opp.kind not in cons.allowed_kinds:
        return False
    if opp.protocol.lower() in {p.lower() for p in cons.excluded_protocols}:
        return False
    if opp.risk_score > cons.max_risk_score:
        return False
    return True


def rank(
    opportunities: list[YieldOpportunity],
    cons: OptimizerConstraints = DEFAULT_CONSTRAINTS,
    *,
    notional_usd: float = _DEFAULT_NOTIONAL_USD,
) -> list[tuple[YieldOpportunity, float]]:
    """Filter, then sort opportunities by net APY (descending).

    ``notional_usd`` sizes the gas-amortization term so the ordering is
    comparable; `optimize()` passes budget / max_positions.
    """
    scored: list[tuple[YieldOpportunity, float]] = []
    for opp in opportunities:
        if not _passes_filters(opp, cons):
            continue
        value = net_apy(opp, notional_usd, cons)
        if value < cons.min_net_apy:
            continue
        scored.append((opp, value))
    # sort by net APY desc, then id for a deterministic tie-break
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored


def optimize(
    opportunities: list[YieldOpportunity],
    budget_usd: float,
    cons: OptimizerConstraints = DEFAULT_CONSTRAINTS,
    *,
    asset: str = "USDC",
) -> AllocationPlan:
    """Greedy-with-constraints allocation of ``budget_usd`` across opportunities.

    Walks the net-APY ranking, giving each opportunity as much as the
    per-protocol cap and remaining budget allow. With the default 40% cap a
    full allocation needs >=3 protocols — diversification by construction.
    """
    notes: list[str] = []
    if budget_usd <= 0:
        return AllocationPlan(
            budget_usd=budget_usd, asset=asset,
            constraints=_constraints_dict(cons), notes=["budget must be positive"],
        )

    notional = budget_usd / max(cons.max_positions, 1)
    ranked = rank(opportunities, cons, notional_usd=notional)
    if not ranked:
        return AllocationPlan(
            budget_usd=budget_usd, asset=asset, unallocated_usd=budget_usd,
            constraints=_constraints_dict(cons),
            notes=["no opportunity passed the filters / minimum net APY"],
        )

    protocol_cap = cons.max_protocol_pct * budget_usd
    protocol_used: dict[str, float] = {}
    remaining = budget_usd
    entries: list[AllocationEntry] = []
    capped: set[str] = set()

    for opp, _ in ranked:
        if len(entries) >= cons.max_positions:
            notes.append(f"stopped at the {cons.max_positions}-position cap")
            break
        if remaining < cons.min_ticket_usd:
            break
        proto = opp.protocol.lower()
        room = protocol_cap - protocol_used.get(proto, 0.0)
        if room < cons.min_ticket_usd:
            continue  # this protocol is already at its cap
        ticket = min(remaining, room)
        if remaining > room:
            capped.add(proto)  # the per-protocol cap (not the budget) bound this
        entries.append(
            AllocationEntry(
                opportunity_id=opp.id,
                protocol=opp.protocol,
                chain=opp.chain,
                amount_usd=ticket,
                expected_net_apy=net_apy(opp, ticket, cons),
                risk_score=opp.risk_score,
            )
        )
        protocol_used[proto] = protocol_used.get(proto, 0.0) + ticket
        remaining -= ticket

    for proto in sorted(capped):
        notes.append(
            f"{proto} capped at {cons.max_protocol_pct:.0%} of budget "
            f"(${protocol_cap:,.0f})"
        )
    if remaining > cons.min_ticket_usd:
        notes.append(
            f"${remaining:,.0f} unallocated — ran out of eligible opportunities"
        )

    allocated = sum(e.amount_usd for e in entries)
    blended_apy = (
        sum(e.amount_usd * e.expected_net_apy for e in entries) / allocated
        if allocated > 0 else 0.0
    )
    blended_risk = (
        sum(e.amount_usd * e.risk_score for e in entries) / allocated
        if allocated > 0 else 0.0
    )
    return AllocationPlan(
        budget_usd=budget_usd,
        asset=asset,
        entries=entries,
        unallocated_usd=remaining,
        blended_net_apy=blended_apy,
        blended_risk=blended_risk,
        constraints=_constraints_dict(cons),
        notes=notes,
    )


def _constraints_dict(cons: OptimizerConstraints) -> dict:
    return {
        "min_net_apy": cons.min_net_apy,
        "max_risk_score": cons.max_risk_score,
        "max_protocol_pct": cons.max_protocol_pct,
        "max_positions": cons.max_positions,
        "min_ticket_usd": cons.min_ticket_usd,
        "gas_cost_usd": cons.gas_cost_usd,
        "holding_period_days": cons.holding_period_days,
        "risk_lambda": cons.risk_lambda,
    }
