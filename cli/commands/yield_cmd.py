"""hl yield — EVM yield discovery, ranking, and optimization.

Surfaces the `yields/` package as a user- and agent-callable CLI group:

    hl yield scan      [--chain ethereum|base|all] [--min-tvl] [--kind] [--source]
    hl yield rank      scan + net-APY ranking under OptimizerConstraints
    hl yield optimize  --budget N    greedy capital allocation (pure, no wallet)

This is EVM on-chain yield (Ethereum + Base) — separate from Hyperliquid.

Discovery / risk / allocation are delegated to `yields.aggregator`,
`yields.risk`, and `yields.optimizer`. All three subcommands here are
READ-ONLY — no wallet, no signing, no transactions.

The execution surface from the source repo (`hl yield position` / `hl yield
route` / `hl yield rebalance`) is intentionally NOT ported yet: it needs the
EVM execution substrate (`common.evm.*` and `trading.dex.*`) which agent-cli
does not yet have. Once that substrate lands, those state-changing commands and
the on-chain adapters can be added as a follow-up — the read-only layer here is
already wired to light them up (see `yields.aggregator`).

Every `yields.*` import is lazy inside a command body so the harness
`discover_tools()` registry walk and `hl --help` stay fast and cycle-free —
same discipline as the other `cli/commands/*` sub-apps.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer

yield_app = typer.Typer(
    name="yield",
    help="EVM yield — scan, rank, and optimize Ethereum + Base yield opportunities",
    no_args_is_help=True,
)


def _boot_cli() -> None:
    """Project-root + logging setup, mirroring the other CLI sub-apps."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


def _resolve_chains(chain: str):
    """Map the `--chain` option onto a tuple of `yields.models.Chain`."""
    from yields.models import Chain

    c = (chain or "all").strip().lower()
    if c in ("all", ""):
        return (Chain.ethereum, Chain.base)
    try:
        return (Chain(c),)
    except ValueError:
        typer.echo(
            f"Error: unknown chain '{chain}'. Use ethereum, base, or all.",
            err=True,
        )
        raise typer.Exit(2)


def _parse_kind(kind: Optional[str]):
    """Map an optional `--kind` string onto a `YieldKind`, or None."""
    if not kind:
        return None
    from yields.models import YieldKind

    try:
        return YieldKind(kind.strip().lower())
    except ValueError:
        typer.echo(
            f"Error: unknown kind '{kind}'. Use lending, staking, vault, or lp.",
            err=True,
        )
        raise typer.Exit(2)


def _build_constraints(
    *,
    min_net_apy: float,
    max_risk: float,
    max_protocol_pct: float | None = None,
    max_positions: int | None = None,
    min_ticket: float | None = None,
    gas_cost: float,
    holding_days: float,
    risk_lambda: float,
    exclude: list[str] | None,
    kind=None,
):
    """Assemble an `OptimizerConstraints` from CLI options.

    Only the fields explicitly supported by a given command are passed; the
    rest fall back to `OptimizerConstraints`' conservative defaults.
    """
    from yields.optimizer import OptimizerConstraints

    kwargs: dict = {
        "min_net_apy": min_net_apy,
        "max_risk_score": max_risk,
        "gas_cost_usd": gas_cost,
        "holding_period_days": holding_days,
        "risk_lambda": risk_lambda,
        "excluded_protocols": tuple(exclude or ()),
    }
    if max_protocol_pct is not None:
        kwargs["max_protocol_pct"] = max_protocol_pct
    if max_positions is not None:
        kwargs["max_positions"] = max_positions
    if min_ticket is not None:
        kwargs["min_ticket_usd"] = min_ticket
    if kind is not None:
        kwargs["allowed_kinds"] = (kind,)
    return OptimizerConstraints(**kwargs)


def _filter_opps(opportunities, *, min_tvl: float, kind, source: str):
    """Apply the `scan`-style post-filters (min TVL, kind, source tier)."""
    from yields.models import SourceTier

    out = list(opportunities)
    if min_tvl > 0:
        out = [o for o in out if o.tvl_usd >= min_tvl]
    if kind is not None:
        out = [o for o in out if o.kind == kind]
    src = (source or "all").strip().lower()
    if src == "onchain":
        out = [o for o in out if o.source_tier == SourceTier.onchain]
    elif src == "defillama":
        out = [o for o in out if o.source_tier == SourceTier.defillama]
    return out


def _opp_json(opp) -> dict:
    """A compact, stable JSON shape for one opportunity (used by `--json`)."""
    return {
        "id": opp.id,
        "protocol": opp.protocol,
        "chain": opp.chain.value,
        "kind": opp.kind.value,
        "apy_base": opp.apy_base,
        "apy_reward": opp.apy_reward,
        "apy_total": opp.apy_total,
        "tvl_usd": opp.tvl_usd,
        "risk_score": opp.risk_score,
        "source_tier": opp.source_tier.value,
        "has_onchain_adapter": opp.has_onchain_adapter,
        "pool_address": opp.pool_address,
        "underlying": [t.symbol for t in opp.underlying],
    }


# ─── scan ────────────────────────────────────────────────────────────────────


@yield_app.command("scan")
def scan_cmd(
    chain: str = typer.Option(
        "all", "--chain", help="Chain to scan: ethereum, base, or all"
    ),
    min_tvl: float = typer.Option(
        0.0, "--min-tvl", help="Drop opportunities below this TVL (USD)"
    ),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="Filter by kind: lending, staking, vault, lp"
    ),
    source: str = typer.Option(
        "all", "--source", help="Discovery tier: all, defillama, onchain"
    ),
    json_out: bool = typer.Option(
        False, "--json/--text", help="Emit JSON instead of a table"
    ),
):
    """Scan Ethereum + Base for yield opportunities across every discovery source.

    Runs the two-tier aggregator (DeFiLlama discovery + the curated on-chain
    adapters, when present), merges duplicate rows, risk-scores each, and
    renders the result ordered by gross APY. Read-only — no wallet, no signing.
    """
    _boot_cli()

    from yields import aggregator
    from cli.display import yield_table

    chains = _resolve_chains(chain)
    parsed_kind = _parse_kind(kind)

    opps = aggregator.aggregate(chains)
    opps = _filter_opps(opps, min_tvl=min_tvl, kind=parsed_kind, source=source)

    if json_out:
        typer.echo(json.dumps([_opp_json(o) for o in opps], indent=2))
        return

    if not opps:
        typer.echo("No yield opportunities matched the filters.")
        return
    typer.echo(yield_table(opps))


# ─── aggregate (alias of scan) ───────────────────────────────────────────────


@yield_app.command("aggregate")
def aggregate_cmd(
    chain: str = typer.Option(
        "all", "--chain", help="Chain to scan: ethereum, base, or all"
    ),
    min_tvl: float = typer.Option(
        0.0, "--min-tvl", help="Drop opportunities below this TVL (USD)"
    ),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="Filter by kind: lending, staking, vault, lp"
    ),
    source: str = typer.Option(
        "all", "--source", help="Discovery tier: all, defillama, onchain"
    ),
    json_out: bool = typer.Option(
        False, "--json/--text", help="Emit JSON instead of a table"
    ),
):
    """Aggregate Ethereum + Base yield opportunities (alias of `scan`).

    Identical to `scan`: runs the two-tier aggregator, merges duplicate rows,
    risk-scores each, and renders ordered by gross APY. Read-only.
    """
    scan_cmd(
        chain=chain, min_tvl=min_tvl, kind=kind, source=source, json_out=json_out
    )


# ─── rank ────────────────────────────────────────────────────────────────────


@yield_app.command("rank")
def rank_cmd(
    chain: str = typer.Option(
        "all", "--chain", help="Chain to scan: ethereum, base, or all"
    ),
    min_tvl: float = typer.Option(
        0.0, "--min-tvl", help="Drop opportunities below this TVL (USD)"
    ),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="Filter by kind: lending, staking, vault, lp"
    ),
    source: str = typer.Option(
        "all", "--source", help="Discovery tier: all, defillama, onchain"
    ),
    min_net_apy: float = typer.Option(
        0.0, "--min-net-apy", help="Drop opportunities below this net APY (fraction)"
    ),
    max_risk: float = typer.Option(
        1.0, "--max-risk", help="Drop opportunities above this risk score (0..1)"
    ),
    gas_cost: float = typer.Option(
        15.0, "--gas-cost", help="Amortized entry+exit gas per position (USD)"
    ),
    holding_days: float = typer.Option(
        30.0, "--holding-days", help="Holding period for gas amortization (days)"
    ),
    risk_lambda: float = typer.Option(
        0.15, "--risk-lambda", help="APY penalty per unit of risk score"
    ),
    notional: float = typer.Option(
        10_000.0, "--notional", help="Ticket size used to amortize gas in the ranking"
    ),
    exclude: list[str] = typer.Option(
        [], "--exclude", help="Protocol slug to exclude (repeatable)"
    ),
    json_out: bool = typer.Option(
        False, "--json/--text", help="Emit JSON instead of a table"
    ),
):
    """Scan, then rank yield opportunities by risk- and gas-adjusted net APY.

    `net_apy = apy_base + apy_reward - gas_amortized - risk_lambda*risk`. The
    ranking is the pure `yields.optimizer.rank()` — deterministic, no wallet.
    """
    _boot_cli()

    from yields import aggregator
    from yields.optimizer import rank
    from cli.display import yield_table

    chains = _resolve_chains(chain)
    parsed_kind = _parse_kind(kind)
    cons = _build_constraints(
        min_net_apy=min_net_apy,
        max_risk=max_risk,
        gas_cost=gas_cost,
        holding_days=holding_days,
        risk_lambda=risk_lambda,
        exclude=exclude,
        kind=parsed_kind,
    )

    opps = aggregator.aggregate(chains)
    opps = _filter_opps(opps, min_tvl=min_tvl, kind=parsed_kind, source=source)
    ranked = rank(opps, cons, notional_usd=notional)

    if json_out:
        payload = [
            {**_opp_json(opp), "net_apy": value} for opp, value in ranked
        ]
        typer.echo(json.dumps(payload, indent=2))
        return

    if not ranked:
        typer.echo("No yield opportunities passed the ranking filters.")
        return
    ordered = [opp for opp, _ in ranked]
    net_apy_by_id = {opp.id: value for opp, value in ranked}
    typer.echo(yield_table(ordered, net_apy_by_id=net_apy_by_id))


# ─── optimize ────────────────────────────────────────────────────────────────


@yield_app.command("optimize")
def optimize_cmd(
    budget: float = typer.Option(
        ..., "--budget", help="Total capital to allocate (USD) — required"
    ),
    chain: str = typer.Option(
        "all", "--chain", help="Chain to scan: ethereum, base, or all"
    ),
    asset: str = typer.Option(
        "USDC", "--asset", help="The asset being allocated (display label)"
    ),
    min_tvl: float = typer.Option(
        0.0, "--min-tvl", help="Drop opportunities below this TVL (USD)"
    ),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="Filter by kind: lending, staking, vault, lp"
    ),
    source: str = typer.Option(
        "all", "--source", help="Discovery tier: all, defillama, onchain"
    ),
    max_risk: float = typer.Option(
        1.0, "--max-risk", help="Drop opportunities above this risk score (0..1)"
    ),
    max_protocol_pct: float = typer.Option(
        0.40, "--max-protocol-pct", help="Per-protocol cap as a fraction of budget"
    ),
    max_positions: int = typer.Option(
        6, "--max-positions", help="Maximum number of positions in the plan"
    ),
    min_net_apy: float = typer.Option(
        0.0, "--min-net-apy", help="Drop opportunities below this net APY (fraction)"
    ),
    min_ticket: float = typer.Option(
        500.0, "--min-ticket", help="Minimum allocation per position (USD)"
    ),
    gas_cost: float = typer.Option(
        15.0, "--gas-cost", help="Amortized entry+exit gas per position (USD)"
    ),
    holding_days: float = typer.Option(
        30.0, "--holding-days", help="Holding period for gas amortization (days)"
    ),
    risk_lambda: float = typer.Option(
        0.15, "--risk-lambda", help="APY penalty per unit of risk score"
    ),
    exclude: list[str] = typer.Option(
        [], "--exclude", help="Protocol slug to exclude (repeatable)"
    ),
    json_out: bool = typer.Option(
        False, "--json/--text", help="Emit JSON instead of a table"
    ),
):
    """Greedily allocate a budget across yield opportunities under constraints.

    Walks the net-APY ranking and fills the highest first, subject to a
    per-protocol concentration cap, a position-count cap, and a minimum ticket.
    Pure — no wallet, no signing. Prints the `AllocationPlan`.
    """
    _boot_cli()

    from yields import aggregator
    from yields.optimizer import optimize
    from cli.display import allocation_plan_block

    chains = _resolve_chains(chain)
    parsed_kind = _parse_kind(kind)
    cons = _build_constraints(
        min_net_apy=min_net_apy,
        max_risk=max_risk,
        max_protocol_pct=max_protocol_pct,
        max_positions=max_positions,
        min_ticket=min_ticket,
        gas_cost=gas_cost,
        holding_days=holding_days,
        risk_lambda=risk_lambda,
        exclude=exclude,
        kind=parsed_kind,
    )

    opps = aggregator.aggregate(chains)
    opps = _filter_opps(opps, min_tvl=min_tvl, kind=parsed_kind, source=source)
    plan = optimize(opps, budget, cons, asset=asset)

    if json_out:
        typer.echo(plan.model_dump_json(indent=2))
        return
    typer.echo(allocation_plan_block(plan))
