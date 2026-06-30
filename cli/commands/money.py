"""hl money — withdraw, transfer, deposit, and bridge funds."""
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


def _refuse_non_interactive_without_yes(yes: bool) -> None:
    """Fail before expensive or pairing-dependent setup in automation."""
    if yes or sys.stdin.isatty():
        return
    typer.echo("Refusing to move funds without --yes in non-interactive mode.", err=True)
    raise typer.Exit(1)


def _submit(request, mainnet: bool) -> None:
    from cli.hl_actions import sign_and_submit

    result = sign_and_submit(request, mainnet)
    typer.echo(json.dumps(result, indent=2))


@money_app.command("withdraw", help="Withdraw USDC from Hyperliquid to Arbitrum")
def withdraw(
    amount: str = typer.Argument(..., help="USDC amount"),
    destination: str = typer.Argument(..., help="Arbitrum destination address"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    _ensure_path()
    from cli.hl_actions import build_withdraw

    request = build_withdraw(amount, destination, mainnet)
    typer.echo(request.summary)
    _confirm("Submit this Hyperliquid withdrawal via your paired master wallet?", yes)
    _submit(request, mainnet)


@money_app.command("transfer", help="Transfer funds within Hyperliquid")
def transfer(
    kind: str = typer.Argument(..., help="usd, spot, perp-spot, vault, subaccount, subaccount-spot, send-asset"),
    amount: str = typer.Argument(..., help="Amount to transfer"),
    destination: Optional[str] = typer.Argument(None, help="Destination address/vault/sub-account when needed"),
    token: str = typer.Option("USDC", "--token", help="Spot token symbol for spot transfers"),
    to_perp: bool = typer.Option(False, "--to-perp", help="For perp-spot transfers, move spot USDC to perps"),
    to_spot: bool = typer.Option(False, "--to-spot", help="For perp-spot transfers, move perp USDC to spot"),
    deposit: bool = typer.Option(False, "--deposit", help="For vault/sub-account transfers, deposit into target"),
    withdraw_from_target: bool = typer.Option(
        False,
        "--withdraw-from-target",
        help="For vault/sub-account transfers, withdraw from target",
    ),
    source_dex: str = typer.Option("", "--source-dex", help="For send-asset, source dex; empty string is perp"),
    destination_dex: str = typer.Option("", "--destination-dex", help="For send-asset, destination dex"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    _ensure_path()
    from cli.hl_actions import (
        build_send_asset,
        build_spot_transfer,
        build_sub_account_spot_transfer,
        build_sub_account_transfer,
        build_usd_class_transfer,
        build_usd_transfer,
        build_vault_transfer,
        parse_whole_usd,
    )

    kind = kind.lower()
    if kind == "usd":
        if destination is None:
            raise typer.BadParameter("usd transfers require a destination address")
        request = build_usd_transfer(amount, destination, mainnet)
    elif kind == "spot":
        if destination is None:
            raise typer.BadParameter("spot transfers require a destination address")
        request = build_spot_transfer(amount, destination, token, mainnet)
    elif kind == "perp-spot":
        if to_perp == to_spot:
            raise typer.BadParameter("Choose exactly one of --to-perp or --to-spot")
        request = build_usd_class_transfer(amount, to_perp=to_perp, mainnet=mainnet)
    elif kind == "vault":
        if destination is None:
            raise typer.BadParameter("vault transfers require a vault address")
        if deposit == withdraw_from_target:
            raise typer.BadParameter("Choose exactly one of --deposit or --withdraw-from-target")
        request = build_vault_transfer(destination, deposit, parse_whole_usd(amount), mainnet)
    elif kind == "subaccount":
        if destination is None:
            raise typer.BadParameter("subaccount transfers require a sub-account user address")
        if deposit == withdraw_from_target:
            raise typer.BadParameter("Choose exactly one of --deposit or --withdraw-from-target")
        request = build_sub_account_transfer(destination, deposit, parse_whole_usd(amount), mainnet)
    elif kind == "subaccount-spot":
        if destination is None:
            raise typer.BadParameter("subaccount-spot transfers require a sub-account user address")
        if deposit == withdraw_from_target:
            raise typer.BadParameter("Choose exactly one of --deposit or --withdraw-from-target")
        request = build_sub_account_spot_transfer(destination, deposit, token, amount, mainnet)
    elif kind == "send-asset":
        if destination is None:
            raise typer.BadParameter("send-asset transfers require a destination address")
        request = build_send_asset(amount, destination, token, source_dex, destination_dex, mainnet)
    else:
        raise typer.BadParameter(f"unknown transfer kind: {kind}")

    typer.echo(request.summary)
    _confirm("Submit this Hyperliquid transfer via your paired master wallet?", yes)
    _submit(request, mainnet)


@money_app.command("deposit", help="Deposit Arbitrum USDC into Hyperliquid Bridge2")
def deposit(
    amount: str = typer.Argument(..., help="USDC amount; minimum 5"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    _ensure_path()
    _refuse_non_interactive_without_yes(yes)
    from cli.config import TradingConfig
    from cli.hl_actions import build_deposit_transaction
    from cli.web_auth import submit_transaction

    tx, summary = build_deposit_transaction(amount, mainnet, TradingConfig())
    typer.echo(summary)
    typer.echo(f"From: {tx['from']}")
    typer.echo(f"To:   {tx['to']}")
    typer.echo(f"Chain ID: {tx['chainId']}")
    _confirm("Submit this Arbitrum transaction via your paired master wallet?", yes)
    tx_hash = submit_transaction(tx, summary)
    typer.echo(json.dumps({"status": "sent", "tx_hash": tx_hash}, indent=2))


@money_app.command("bridge", help="Cross-chain bridge into Arbitrum, then Hyperliquid")
def bridge() -> None:
    typer.echo(
        "Cross-chain bridge support is intentionally deferred until a provider API, "
        "contracts, and calldata are source-verified.",
        err=True,
    )
    raise typer.Exit(2)
