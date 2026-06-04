"""hl policy — inspect and validate the active local session policy.

Local-only: reads from --policy / NUNCHI_SESSION_POLICY. No web-auth, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

policy_app = typer.Typer(no_args_is_help=True)


def _resolve_path(policy: Optional[Path]) -> Optional[str]:
    return str(policy) if policy else None


@policy_app.command("show")
def policy_show(
    policy: Optional[Path] = typer.Option(
        None, "--policy",
        help="Session policy file or inline JSON (else NUNCHI_SESSION_POLICY env).",
    ),
):
    """Print the active session policy (or note that none is set)."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.session_policy import POLICY_ENV_VAR, load_policy

    try:
        pol = load_policy(_resolve_path(policy))
    except Exception as e:
        typer.echo(f"Session policy error: {e}", err=True)
        raise typer.Exit(2)

    if pol is None:
        typer.echo("No session policy configured (permissive).")
        typer.echo(f"  Set --policy <file|json> or the {POLICY_ENV_VAR} env var to enable.")
        raise typer.Exit(0)

    typer.echo(json.dumps(pol.to_dict(), indent=2))


@policy_app.command("validate")
def policy_validate(
    policy: Optional[Path] = typer.Option(
        None, "--policy",
        help="Session policy file or inline JSON (else NUNCHI_SESSION_POLICY env).",
    ),
):
    """Validate the active session policy and report OK / the error."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.session_policy import load_policy

    try:
        pol = load_policy(_resolve_path(policy))
    except Exception as e:
        typer.echo(f"INVALID: {e}", err=True)
        raise typer.Exit(2)

    if pol is None:
        typer.echo("No session policy configured (permissive) — nothing to validate.")
        raise typer.Exit(0)

    # Round-trip to surface any structural issues, then summarise constraints.
    d = pol.to_dict()
    constraints = []
    if d["wallets"]:
        constraints.append(f"wallets={len(d['wallets'])}")
    if d["network"]:
        constraints.append(f"network={d['network']}")
    if d["allowed_actions"]:
        constraints.append(f"actions={','.join(d['allowed_actions'])}")
    if d["allowed_strategies"]:
        constraints.append(f"strategies={len(d['allowed_strategies'])}")
    if d["allowed_markets"]:
        constraints.append(f"markets={','.join(d['allowed_markets'])}")
    if d["max_notional_usd_per_action"] is not None:
        constraints.append(f"max_per_action=${d['max_notional_usd_per_action']:,.2f}")
    if d["daily_notional_limit_usd"] is not None:
        constraints.append(f"daily=${d['daily_notional_limit_usd']:,.2f}")

    typer.echo("VALID")
    typer.echo("  constraints: " + (", ".join(constraints) if constraints else "(none — permissive)"))
