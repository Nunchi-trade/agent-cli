"""hl privy — Privy agent policy helpers for Hyperliquid."""
from __future__ import annotations

import json
from typing import Optional

import typer

privy_app = typer.Typer(no_args_is_help=True)


@privy_app.command("policies", help="Print Privy policy templates for Hyperliquid agent signers")
def policies(
    kind: str = typer.Option("all", "--kind", help="all, deny_withdraw, deny_send_asset, allow_approve_agent"),
    network: str = typer.Option("both", "--network", help="both, mainnet, or testnet"),
) -> None:
    from cli.privy_agent import HL_BOTH_NETWORKS, HL_MAINNETS, HL_TESTNETS, hyperliquid_policy_templates

    if network == "mainnet":
        networks = HL_MAINNETS
    elif network == "testnet":
        networks = HL_TESTNETS
    elif network == "both":
        networks = HL_BOTH_NETWORKS
    else:
        raise typer.BadParameter("network must be one of: both, mainnet, testnet")

    templates = hyperliquid_policy_templates(networks)
    if kind != "all":
        if kind not in templates:
            raise typer.BadParameter(f"unknown policy kind: {kind}")
        typer.echo(json.dumps(templates[kind], indent=2))
        return
    typer.echo(json.dumps(templates, indent=2))


@privy_app.command("signer-payload", help="Print a Privy wallet update payload for a policy-scoped signer")
def signer_payload(
    signer_id: str = typer.Argument(..., help="Privy signer or key quorum id"),
    policy_ids: list[str] = typer.Argument(..., help="Privy policy ids to apply to this signer"),
) -> None:
    from cli.privy_agent import signer_update_payload

    typer.echo(json.dumps(signer_update_payload(signer_id, list(policy_ids)), indent=2))


@privy_app.command("scope", help="Print web-auth scope metadata for session-signer-gated requests")
def scope(
    method: str = typer.Argument(..., help="Scope method, e.g. hl.withdraw or hl.approveAgent"),
    network: int = typer.Option(..., "--network", help="Chain/network id to bind the request"),
    notional_usdc: Optional[float] = typer.Option(None, "--notional-usdc", help="Optional notional amount"),
    instrument_hash: Optional[str] = typer.Option(None, "--instrument-hash", help="Optional instrument allowlist hash"),
) -> None:
    from cli.privy_agent import session_scope

    typer.echo(
        json.dumps(
            session_scope(
                method,
                network,
                notional_usdc=notional_usdc,
                instrument_hash=instrument_hash,
            ),
            indent=2,
        )
    )
