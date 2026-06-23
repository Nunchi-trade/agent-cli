"""hl builder — builder fee management commands."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer

builder_app = typer.Typer(no_args_is_help=True)


@builder_app.command("approve")
def builder_approve(
    mainnet: bool = typer.Option(False, "--mainnet"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    policy: Optional[Path] = typer.Option(
        None, "--policy",
        help="Session policy file (or inline JSON / NUNCHI_SESSION_POLICY env). "
             "Local guard only — no web-auth, no network.",
    ),
):
    """Approve builder fee for your account (required before fees can be collected)."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.view_mode import require_not_view_only

    require_not_view_only()

    from cli.builder_fee import BuilderFeeConfig
    from cli.config import TradingConfig

    cfg = TradingConfig()

    # ── Session policy guard (local; permissive if no policy configured) ──
    from cli.session_policy import ACTION_BUILDER_APPROVE, guard_or_exit, load_policy_or_exit

    policy_path = str(policy) if policy else None
    active_policy = load_policy_or_exit(policy_path)
    signer_wallet = None
    private_key = None
    if active_policy and active_policy.wallets:
        private_key = cfg.get_private_key()
        signer_wallet = cfg.get_wallet_address(private_key)

    guard_or_exit(
        ACTION_BUILDER_APPROVE,
        policy_path=policy_path,
        wallet=signer_wallet,
        network="mainnet" if mainnet else "testnet",
    )

    builder_cfg = cfg.get_builder_config()

    if not builder_cfg.enabled:
        typer.echo("Builder fee not configured. Set BUILDER_ADDRESS and BUILDER_FEE_TENTHS_BPS.")
        raise typer.Exit(1)

    typer.echo(f"Builder address: {builder_cfg.builder_address}")
    typer.echo(f"Fee rate: {builder_cfg.fee_bps} bps ({builder_cfg.max_fee_rate_str})")
    typer.echo("")

    if not yes:
        if sys.stdin.isatty():
            confirm = typer.confirm("Approve this builder fee on your HL account?")
            if not confirm:
                raise typer.Exit()
        else:
            typer.echo("Auto-confirming (non-interactive mode)")

    from parent.hl_proxy import HLProxy

    private_key = private_key or cfg.get_private_key()
    hl = HLProxy(private_key=private_key, testnet=not mainnet)
    hl._ensure_client()

    try:
        result = hl._exchange.approve_builder_fee(
            builder_cfg.builder_address,
            builder_cfg.max_fee_rate_str,
        )
        typer.echo(f"Approved. Response: {result}")
    except Exception as e:
        typer.echo(f"Failed to approve builder fee: {e}", err=True)
        raise typer.Exit(1)


@builder_app.command("status")
def builder_status():
    """Show current builder fee configuration."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.config import TradingConfig

    cfg = TradingConfig()
    builder_cfg = cfg.get_builder_config()

    if not builder_cfg.enabled:
        typer.echo("Builder fee: DISABLED")
        typer.echo("  Set BUILDER_ADDRESS and BUILDER_FEE_TENTHS_BPS to enable.")
    else:
        typer.echo("Builder fee: ENABLED")
        typer.echo(f"  Address: {builder_cfg.builder_address}")
        typer.echo(f"  Fee: {builder_cfg.fee_bps} bps ({builder_cfg.fee_rate_tenths_bps} tenths)")
        typer.echo(f"  Max rate: {builder_cfg.max_fee_rate_str}")
