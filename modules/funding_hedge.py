"""Pure-math funding-rate hedge proposal helpers.

This module intentionally does not talk to Hyperliquid or sign orders. It gives
agents a deterministic way to size the public BTCSWP hedge slice.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional


Side = Literal["long", "short"]

BTCSWP_PROFILE = {
    "asset": "BTC",
    "hedge_market": "BTCSWP-USDYP",
    "hl_coin": "yex:BTCSWP",
    "vol_multiplier": 15.0,
    "status": "deployed",
}

ROADMAP_PROFILES = [
    {"asset": "ETH", "hedge_market": "ETHSWP-USDYP", "status": "roadmap"},
    {"asset": "HYPE", "hedge_market": "HYPESWP-USDYP", "status": "roadmap"},
    {"asset": "SPCX", "hedge_market": "SPCXSWP-USDYP", "status": "roadmap"},
]


@dataclass(frozen=True)
class FundingHedgeProposal:
    asset: str
    perp_side: Side
    perp_notional_usd: float
    funding_apr: float
    funding_rate_8h: Optional[float]
    hedge_market: str
    hedge_hl_coin: str
    hedge_side: Side
    hedge_notional_usd: float
    vol_multiplier: float
    effective_hedged_notional_usd: float
    coverage_pct: float
    unhedged_funding_cashflow_usd_per_year: float
    target_hedge_cashflow_usd_per_year: float
    assumption: str
    status: str
    disclaimer: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FundingHedgeBacktestRow:
    index: int
    timestamp: Optional[str]
    funding_rate_8h: float
    hedge_rate_8h: float
    unhedged_cashflow_usd: float
    hedge_cashflow_usd: float
    net_cashflow_usd: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FundingHedgeBacktest:
    asset: str
    perp_side: Side
    perp_notional_usd: float
    hedge_market: str
    hedge_hl_coin: str
    hedge_side: Side
    hedge_notional_usd: float
    vol_multiplier: float
    effective_hedged_notional_usd: float
    coverage_pct: float
    periods: int
    average_funding_rate_8h: float
    annualized_average_funding_apr: float
    unhedged_cashflow_usd: float
    hedge_cashflow_usd: float
    net_cashflow_usd: float
    max_period_unhedged_payment_usd: float
    max_period_net_cost_usd: float
    rows: list[FundingHedgeBacktestRow]
    assumption: str
    disclaimer: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["rows"] = [row.to_dict() for row in self.rows]
        return payload


def funding_hedge_info() -> dict[str, object]:
    """Return agent-discoverable metadata for the public hedge slice."""
    return {
        "name": "BTCSWP funding-rate hedge",
        "summary": (
            "Pure sizing and local cashflow backtesting for Nunchi's public BTC "
            "funding-rate hedge surface."
        ),
        "deployed_profiles": [dict(BTCSWP_PROFILE)],
        "roadmap_profiles": [dict(profile) for profile in ROADMAP_PROFILES],
        "default_vol_multiplier": BTCSWP_PROFILE["vol_multiplier"],
        "sizing_rule": "same-side BTCSWP, hedge_notional = perp_notional / vol_multiplier",
        "supported_cli": [
            "hl hedge propose --perp-notional ... --funding-apr ...",
            "hl hedge backtest --csv ... --perp-notional ...",
        ],
        "mcp_tools": ["funding_hedge_info", "funding_hedge_propose", "funding_hedge_backtest"],
        "csv_required_columns": ["funding_rate_8h", "perp_funding_rate_8h", "funding_rate", "rate"],
        "csv_optional_columns": ["hedge_rate_8h", "btcswp_rate_8h", "btcswp_funding_rate_8h"],
        "hedge_agent_distinction": (
            "strategy hedge_agent is an inventory/delta reducer. The BTCSWP "
            "funding-rate hedge lives under hl hedge and the funding_hedge_* MCP tools."
        ),
        "execution_boundary": (
            "funding_hedge_info/propose/backtest do not place orders, sign payloads, "
            "fetch private account state, or expose private rate methodology."
        ),
    }


def normalize_side(side: str) -> Side:
    normalized = side.strip().lower()
    if normalized not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    return normalized  # type: ignore[return-value]


def normalize_apr(value: float) -> float:
    """Accept either decimal APR (0.42) or percent APR (42)."""
    if abs(value) > 1:
        return value / 100.0
    return value


def annualize_funding_rate_8h(rate: float) -> float:
    """Convert an 8h funding rate into simple annualized APR."""
    return rate * 3 * 365


def _normalize_rate(value: float) -> float:
    """Accept decimals, or whole percent values when clearly percent-like."""
    if abs(value) > 1:
        return value / 100.0
    return value


def propose_funding_hedge(
    *,
    asset: str = "BTC",
    perp_side: str = "long",
    perp_notional_usd: float,
    funding_apr: Optional[float] = None,
    funding_rate_8h: Optional[float] = None,
    vol_multiplier: float = BTCSWP_PROFILE["vol_multiplier"],
) -> FundingHedgeProposal:
    """Size a BTCSWP hedge for a BTC perp funding exposure.

    Positive funding means longs pay shorts. The BTCSWP hedge is same-side and
    sized at 1 / vol_multiplier notional so the rate leg targets the full perp
    notional.
    """
    asset = asset.strip().upper()
    if asset != "BTC":
        raise ValueError("only BTC funding hedges are deployed today; ETH/HYPE/SPCX profiles are roadmap")
    if perp_notional_usd <= 0:
        raise ValueError("perp_notional_usd must be positive")
    if vol_multiplier <= 0:
        raise ValueError("vol_multiplier must be positive")
    if funding_apr is None and funding_rate_8h is None:
        raise ValueError("provide funding_apr or funding_rate_8h")

    side = normalize_side(perp_side)
    apr = annualize_funding_rate_8h(funding_rate_8h) if funding_apr is None else normalize_apr(funding_apr)
    side_sign = 1 if side == "long" else -1

    hedge_notional = perp_notional_usd / vol_multiplier
    effective_notional = hedge_notional * vol_multiplier
    unhedged_cashflow = -side_sign * perp_notional_usd * apr
    target_hedge_cashflow = -unhedged_cashflow

    return FundingHedgeProposal(
        asset=asset,
        perp_side=side,
        perp_notional_usd=round(perp_notional_usd, 2),
        funding_apr=apr,
        funding_rate_8h=funding_rate_8h,
        hedge_market=BTCSWP_PROFILE["hedge_market"],
        hedge_hl_coin=BTCSWP_PROFILE["hl_coin"],
        hedge_side=side,
        hedge_notional_usd=round(hedge_notional, 2),
        vol_multiplier=vol_multiplier,
        effective_hedged_notional_usd=round(effective_notional, 2),
        coverage_pct=round(effective_notional / perp_notional_usd * 100, 4),
        unhedged_funding_cashflow_usd_per_year=round(unhedged_cashflow, 2),
        target_hedge_cashflow_usd_per_year=round(target_hedge_cashflow, 2),
        assumption=(
            "BTCSWP hedge is same-side and uses 1/15 notional by default; "
            "positive funding means long perps pay shorts."
        ),
        status=BTCSWP_PROFILE["status"],
        disclaimer="Sizing proposal only. This command does not place orders or expose the private rate methodology.",
    )


def _first_present(row: dict[str, str], names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def load_funding_rows_from_csv(path: str | Path) -> list[dict[str, Optional[str] | float]]:
    """Load funding rows from CSV.

    Required column aliases: funding_rate_8h, perp_funding_rate_8h, funding_rate, or rate.
    Optional hedge aliases: hedge_rate_8h, btcswp_rate_8h, btcswp_funding_rate_8h.
    """
    csv_path = Path(path)
    rows: list[dict[str, Optional[str] | float]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, raw in enumerate(reader, start=1):
            normalized = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
            funding_raw = _first_present(
                normalized,
                ("funding_rate_8h", "perp_funding_rate_8h", "funding_rate", "rate"),
            )
            if funding_raw is None:
                raise ValueError(
                    "CSV must include funding_rate_8h, perp_funding_rate_8h, funding_rate, or rate"
                )
            hedge_raw = _first_present(
                normalized,
                ("hedge_rate_8h", "btcswp_rate_8h", "btcswp_funding_rate_8h"),
            )
            try:
                funding_rate = _normalize_rate(float(funding_raw))
                hedge_rate = _normalize_rate(float(hedge_raw)) if hedge_raw is not None else funding_rate
            except ValueError as exc:
                raise ValueError(f"invalid funding rate on CSV row {index}") from exc
            rows.append(
                {
                    "timestamp": _first_present(normalized, ("timestamp", "time", "date")),
                    "funding_rate_8h": funding_rate,
                    "hedge_rate_8h": hedge_rate,
                }
            )
    if not rows:
        raise ValueError("CSV contains no funding rows")
    return rows


def backtest_funding_hedge(
    *,
    funding_rows: list[dict[str, Optional[str] | float]],
    asset: str = "BTC",
    perp_side: str = "long",
    perp_notional_usd: float,
    vol_multiplier: float = BTCSWP_PROFILE["vol_multiplier"],
) -> FundingHedgeBacktest:
    """Backtest funding cashflows for a same-side BTCSWP hedge."""
    proposal = propose_funding_hedge(
        asset=asset,
        perp_side=perp_side,
        perp_notional_usd=perp_notional_usd,
        funding_rate_8h=float(funding_rows[0]["funding_rate_8h"]),
        vol_multiplier=vol_multiplier,
    )
    side_sign = 1 if proposal.perp_side == "long" else -1

    detail_rows: list[FundingHedgeBacktestRow] = []
    for index, row in enumerate(funding_rows, start=1):
        funding_rate = float(row["funding_rate_8h"])
        hedge_rate = float(row["hedge_rate_8h"])
        unhedged = -side_sign * perp_notional_usd * funding_rate
        hedge = side_sign * proposal.effective_hedged_notional_usd * hedge_rate
        net = unhedged + hedge
        detail_rows.append(
            FundingHedgeBacktestRow(
                index=index,
                timestamp=str(row["timestamp"]) if row.get("timestamp") else None,
                funding_rate_8h=funding_rate,
                hedge_rate_8h=hedge_rate,
                unhedged_cashflow_usd=round(unhedged, 2),
                hedge_cashflow_usd=round(hedge, 2),
                net_cashflow_usd=round(net, 2),
            )
        )

    periods = len(detail_rows)
    avg_rate = sum(row.funding_rate_8h for row in detail_rows) / periods
    unhedged_total = sum(row.unhedged_cashflow_usd for row in detail_rows)
    hedge_total = sum(row.hedge_cashflow_usd for row in detail_rows)
    net_total = sum(row.net_cashflow_usd for row in detail_rows)
    max_unhedged_payment = max(max(-row.unhedged_cashflow_usd, 0.0) for row in detail_rows)
    max_net_cost = max(max(-row.net_cashflow_usd, 0.0) for row in detail_rows)

    return FundingHedgeBacktest(
        asset=proposal.asset,
        perp_side=proposal.perp_side,
        perp_notional_usd=proposal.perp_notional_usd,
        hedge_market=proposal.hedge_market,
        hedge_hl_coin=proposal.hedge_hl_coin,
        hedge_side=proposal.hedge_side,
        hedge_notional_usd=proposal.hedge_notional_usd,
        vol_multiplier=proposal.vol_multiplier,
        effective_hedged_notional_usd=proposal.effective_hedged_notional_usd,
        coverage_pct=proposal.coverage_pct,
        periods=periods,
        average_funding_rate_8h=round(avg_rate, 10),
        annualized_average_funding_apr=round(annualize_funding_rate_8h(avg_rate), 6),
        unhedged_cashflow_usd=round(unhedged_total, 2),
        hedge_cashflow_usd=round(hedge_total, 2),
        net_cashflow_usd=round(net_total, 2),
        max_period_unhedged_payment_usd=round(max_unhedged_payment, 2),
        max_period_net_cost_usd=round(max_net_cost, 2),
        rows=detail_rows,
        assumption=(
            "If no hedge_rate_8h/BTCSWP column is supplied, the backtest assumes "
            "the BTCSWP hedge rate equals the perp funding rate for an idealized offset."
        ),
        disclaimer="Backtest is local cashflow math only. It does not place orders or model liquidity, fees, or mark-to-market.",
    )


def backtest_funding_hedge_csv(
    *,
    csv_path: str | Path,
    asset: str = "BTC",
    perp_side: str = "long",
    perp_notional_usd: float,
    vol_multiplier: float = BTCSWP_PROFILE["vol_multiplier"],
) -> FundingHedgeBacktest:
    return backtest_funding_hedge(
        funding_rows=load_funding_rows_from_csv(csv_path),
        asset=asset,
        perp_side=perp_side,
        perp_notional_usd=perp_notional_usd,
        vol_multiplier=vol_multiplier,
    )


def format_proposal(proposal: FundingHedgeProposal) -> str:
    direction = "paying" if proposal.unhedged_funding_cashflow_usd_per_year < 0 else "receiving"
    return "\n".join(
        [
            "Funding Hedge Proposal",
            "=" * 40,
            f"Exposure:       {proposal.perp_side.upper()} {proposal.asset} perp ${proposal.perp_notional_usd:,.2f}",
            f"Funding APR:    {proposal.funding_apr * 100:,.2f}%",
            f"Unhedged leg:   {direction} ${abs(proposal.unhedged_funding_cashflow_usd_per_year):,.2f}/yr",
            "",
            f"Hedge market:   {proposal.hedge_market} ({proposal.hedge_hl_coin})",
            f"Hedge action:   {proposal.hedge_side.upper()} ${proposal.hedge_notional_usd:,.2f}",
            f"Multiplier:     {proposal.vol_multiplier:,.2f}x",
            f"Coverage:       ${proposal.effective_hedged_notional_usd:,.2f} ({proposal.coverage_pct:.2f}%)",
            f"Target offset:  ${proposal.target_hedge_cashflow_usd_per_year:,.2f}/yr",
            "",
            f"Assumption:     {proposal.assumption}",
            f"Status:         {proposal.status}",
            f"Disclaimer:     {proposal.disclaimer}",
        ]
    )


def format_info(info: dict[str, object]) -> str:
    profiles = info.get("deployed_profiles", [])
    deployed = profiles[0] if isinstance(profiles, list) and profiles else {}
    if not isinstance(deployed, dict):
        deployed = {}
    return "\n".join(
        [
            "Funding Hedge Info",
            "=" * 40,
            f"Name:          {info['name']}",
            f"Summary:       {info['summary']}",
            f"Deployed:      {deployed.get('asset', 'BTC')} -> {deployed.get('hedge_market', 'BTCSWP-USDYP')}",
            f"Multiplier:    {info['default_vol_multiplier']}x",
            f"Sizing rule:   {info['sizing_rule']}",
            "",
            "CLI:",
            *[f"  {cmd}" for cmd in info["supported_cli"]],  # type: ignore[index]
            "",
            "MCP:",
            *[f"  {tool}" for tool in info["mcp_tools"]],  # type: ignore[index]
            "",
            f"CSV required: {', '.join(info['csv_required_columns'])}",  # type: ignore[arg-type]
            f"CSV optional: {', '.join(info['csv_optional_columns'])}",  # type: ignore[arg-type]
            f"Note:         {info['hedge_agent_distinction']}",
        ]
    )


def format_backtest(backtest: FundingHedgeBacktest) -> str:
    return "\n".join(
        [
            "Funding Hedge Backtest",
            "=" * 40,
            f"Exposure:         {backtest.perp_side.upper()} {backtest.asset} perp ${backtest.perp_notional_usd:,.2f}",
            f"Hedge:            {backtest.hedge_side.upper()} ${backtest.hedge_notional_usd:,.2f} {backtest.hedge_market}",
            f"Periods:          {backtest.periods}",
            f"Avg funding APR:  {backtest.annualized_average_funding_apr * 100:,.2f}%",
            "",
            f"Unhedged cashflow:{backtest.unhedged_cashflow_usd:>15,.2f} USD",
            f"Hedge cashflow:   {backtest.hedge_cashflow_usd:>15,.2f} USD",
            f"Net cashflow:     {backtest.net_cashflow_usd:>15,.2f} USD",
            f"Max net cost:     {backtest.max_period_net_cost_usd:>15,.2f} USD / period",
            "",
            f"Assumption:       {backtest.assumption}",
            f"Disclaimer:       {backtest.disclaimer}",
        ]
    )
