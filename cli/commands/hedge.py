"""hl hedge — funding-rate hedge proposal tools."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

hedge_app = typer.Typer(no_args_is_help=True)


@hedge_app.command("propose", help="Propose a BTCSWP funding-rate hedge")
def hedge_propose(
    asset: str = typer.Option("BTC", "--asset", help="Underlying perp exposure. BTC is deployed today."),
    side: str = typer.Option("long", "--side", help="Perp exposure side: long or short."),
    perp_notional: float = typer.Option(..., "--perp-notional", help="Absolute perp notional in USD."),
    funding_apr: Optional[float] = typer.Option(
        None,
        "--funding-apr",
        help="Annualized funding APR. Accepts 0.42 or 42 for 42%.",
    ),
    funding_rate_8h: Optional[float] = typer.Option(
        None,
        "--funding-rate-8h",
        help="8h funding rate as a decimal, e.g. 0.0003. Used only if --funding-apr is omitted.",
    ),
    vol_multiplier: float = typer.Option(15.0, "--vol-multiplier", help="BTCSWP hedge multiplier."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Return a read-only BTCSWP sizing proposal for a BTC funding exposure."""
    from modules.funding_hedge import format_proposal, propose_funding_hedge

    try:
        proposal = propose_funding_hedge(
            asset=asset,
            perp_side=side,
            perp_notional_usd=perp_notional,
            funding_apr=funding_apr,
            funding_rate_8h=funding_rate_8h,
            vol_multiplier=vol_multiplier,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if json_output:
        typer.echo(json.dumps(proposal.to_dict(), indent=2))
    else:
        typer.echo(format_proposal(proposal))


@hedge_app.command("backtest", help="Backtest BTCSWP funding hedge cashflows from CSV")
def hedge_backtest(
    csv_path: Path = typer.Option(
        ...,
        "--csv",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="CSV with funding_rate_8h/funding_rate column and optional hedge_rate_8h.",
    ),
    asset: str = typer.Option("BTC", "--asset", help="Underlying perp exposure. BTC is deployed today."),
    side: str = typer.Option("long", "--side", help="Perp exposure side: long or short."),
    perp_notional: float = typer.Option(..., "--perp-notional", help="Absolute perp notional in USD."),
    vol_multiplier: float = typer.Option(15.0, "--vol-multiplier", help="BTCSWP hedge multiplier."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Backtest funding cashflows for a same-side BTCSWP hedge."""
    from modules.funding_hedge import backtest_funding_hedge_csv, format_backtest

    try:
        backtest = backtest_funding_hedge_csv(
            csv_path=csv_path,
            asset=asset,
            perp_side=side,
            perp_notional_usd=perp_notional,
            vol_multiplier=vol_multiplier,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if json_output:
        typer.echo(json.dumps(backtest.to_dict(), indent=2))
    else:
        typer.echo(format_backtest(backtest))
