"""hl treadfi — read-only TreadFi integration status."""
from __future__ import annotations

import json
from typing import Any

import typer

from modules import treadfi_contract

treadfi_app = typer.Typer(no_args_is_help=True)


def _echo_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2))


@treadfi_app.command("spec-status")
def treadfi_spec_status():
    """Report whether the TreadFi endpoint/MCP contract is present."""
    _echo_json(treadfi_contract.spec_status())


@treadfi_app.command("capabilities")
def treadfi_capabilities():
    """List local TreadFi placeholders and blocked live capabilities."""
    _echo_json(treadfi_contract.capabilities())


@treadfi_app.command("market-params")
def treadfi_market_params(
    instrument: str = typer.Option("BTCSWP-USDYP", "--instrument", "-i"),
):
    """Show locally known BTCSWP identifiers and missing live params."""
    _echo_json(treadfi_contract.market_params(instrument=instrument))
