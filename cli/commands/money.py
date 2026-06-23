"""hl money — test withdraw, deposit, and bridge flows through existing web-auth."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

money_app = typer.Typer(no_args_is_help=True)


def _ensure_path() -> None:
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def _confirm(prompt: str, yes: bool) -> None:
    if yes:
        return
    if sys.stdin.isatty():
        if not typer.confirm(prompt):
            raise typer.Exit()
    else:
        typer.echo("Refusing to move funds without --yes in non-interactive mode.", err=True)
        raise typer.Exit(1)


@money_app.command("withdraw", help="Withdraw USDC from Hyperliquid to Arbitrum via web-auth")
def withdraw(
    amount: str = typer.Argument(..., help="USDC amount"),
    destination: str = typer.Argument(..., help="Arbitrum destination address"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use Hyperliquid mainnet"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the typed data without signing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    _ensure_path()
    from cli.hl_money import build_withdraw_request, submit_hl_action

    request = build_withdraw_request(amount, destination, mainnet)
    if dry_run:
        typer.echo(json.dumps({"summary": request.summary, "typed_data": request.typed_data}, indent=2))
        return
    typer.echo(request.summary)
    _confirm("Submit this Hyperliquid withdrawal via web-auth?", yes)
    typer.echo(json.dumps(submit_hl_action(request, mainnet), indent=2))


@money_app.command("deposit", help="Deposit Arbitrum USDC into Hyperliquid Bridge2 via web-auth")
def deposit(
    amount: str = typer.Argument(..., help="USDC amount; minimum 5"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use Arbitrum/Hyperliquid mainnet"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the transaction without submitting"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    _ensure_path()
    from cli.hl_money import build_deposit_transaction
    from cli.web_auth import submit_transaction

    tx, summary = build_deposit_transaction(amount, mainnet)
    if dry_run:
        typer.echo(json.dumps({"summary": summary, "transaction": tx}, indent=2))
        return
    typer.echo(summary)
    _confirm("Submit this Arbitrum transaction via web-auth?", yes)
    tx_hash = submit_transaction(tx, summary)
    typer.echo(json.dumps({"status": "sent", "tx_hash": tx_hash}, indent=2))


@money_app.command("bridge-quote", help="Fetch LI.FI calldata for bridging USDC to Arbitrum")
def bridge_quote(
    from_chain: int = typer.Option(..., "--from-chain", help="Source chain ID"),
    from_token: str = typer.Option("USDC", "--from-token", help="Source token symbol or address"),
    amount: str = typer.Option(..., "--amount", help="USDC amount"),
    from_address: Optional[str] = typer.Option(None, "--from-address", help="Override paired wallet address"),
    to_token: str = typer.Option("USDC", "--to-token", help="Destination token symbol/address on Arbitrum"),
    slippage: Optional[float] = typer.Option(None, "--slippage", help="Max slippage, e.g. 0.005"),
) -> None:
    _ensure_path()
    from cli.hl_money import fetch_lifi_bridge_quote

    quote = fetch_lifi_bridge_quote(
        from_chain=from_chain,
        from_token=from_token,
        amount=amount,
        from_address=from_address,
        to_token=to_token,
        slippage=slippage,
    )
    typer.echo(json.dumps(quote, indent=2))


@money_app.command("bridge", help="Bridge USDC to Arbitrum using LI.FI calldata through web-auth")
def bridge(
    from_chain: int = typer.Option(..., "--from-chain", help="Source chain ID"),
    from_token: str = typer.Option("USDC", "--from-token", help="Source token symbol or address"),
    amount: str = typer.Option(..., "--amount", help="USDC amount"),
    from_address: Optional[str] = typer.Option(None, "--from-address", help="Override paired wallet address"),
    to_token: str = typer.Option("USDC", "--to-token", help="Destination token symbol/address on Arbitrum"),
    slippage: Optional[float] = typer.Option(None, "--slippage", help="Max slippage, e.g. 0.005"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print quote transaction without submitting"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    _ensure_path()
    from cli.hl_money import fetch_lifi_bridge_quote, lifi_quote_to_transaction
    from cli.web_auth import submit_transaction

    quote = fetch_lifi_bridge_quote(
        from_chain=from_chain,
        from_token=from_token,
        amount=amount,
        from_address=from_address,
        to_token=to_token,
        slippage=slippage,
    )
    tx = lifi_quote_to_transaction(quote, from_address=from_address)
    summary = f"Bridge {amount} {from_token} from chain {from_chain} to Arbitrum via LI.FI"
    if dry_run:
        typer.echo(json.dumps({"summary": summary, "transaction": tx, "estimate": quote.get("estimate")}, indent=2))
        return
    typer.echo(summary)
    _confirm("Submit this bridge transaction via web-auth?", yes)
    tx_hash = submit_transaction(tx, summary)
    typer.echo(json.dumps({"status": "sent", "tx_hash": tx_hash}, indent=2))
