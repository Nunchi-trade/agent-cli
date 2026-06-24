"""hl pear — Pear Protocol campaign readiness helpers."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import typer

pear_app = typer.Typer(
    name="pear",
    help="Pear Protocol integration readiness and campaign setup.",
    no_args_is_help=True,
)
setup_app = typer.Typer(name="setup", help="Pear setup and readiness checks.", no_args_is_help=True)
pear_app.add_typer(setup_app, name="setup")


def _boot_cli() -> None:
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


@setup_app.command("status")
def status_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print machine-readable JSON"),
    probe: bool = typer.Option(False, "--probe", help="Probe Pear account state using configured credentials"),
):
    """Show Pear campaign readiness without placing trades."""
    _boot_cli()
    from cli.pear_config import PEAR_BUILDER_ADDRESS, PEAR_BUILDER_FEE_TENTHS_BPS, pear_builder_fee_bps

    address = os.getenv("PEAR_ADDRESS") or os.getenv("PEAR_WALLET_ADDRESS")
    api_key = os.getenv("PEAR_API_KEY")
    has_private_key = bool(os.getenv("HL_PRIVATE_KEY"))
    dedicated_ack = os.getenv("PEAR_DEDICATED_WALLET_ACK", "").lower() in {"1", "true", "yes"}
    api_wallet_approved = os.getenv("PEAR_API_WALLET_APPROVED", "").lower() in {"1", "true", "yes"}
    builder_approved = os.getenv("PEAR_BUILDER_APPROVED", "").lower() in {"1", "true", "yes"}

    checks = [
        _check("pear_address", bool(address), "PEAR_ADDRESS or PEAR_WALLET_ADDRESS is set"),
        _check("pear_api_key", bool(api_key), "PEAR_API_KEY is set; preferred for agents so user PK is not reused for JWT refresh"),
        _check("fallback_eip712_key", bool(api_key) or has_private_key, "PEAR_API_KEY or HL_PRIVATE_KEY is available for Pear auth"),
        _check("dedicated_wallet_ack", dedicated_ack, "PEAR_DEDICATED_WALLET_ACK=true acknowledges Pear's dedicated-wallet guidance"),
        _check("pear_api_wallet_approval", api_wallet_approved, "PEAR_API_WALLET_APPROVED=true after approving Pear-managed API wallet"),
        _check("pear_builder_approval", builder_approved, "PEAR_BUILDER_APPROVED=true after approving Pear builder code"),
    ]
    payload: Dict[str, Any] = {
        "ready": all(c["status"] == "pass" for c in checks),
        "auth_mode": "api_key" if api_key else "eip712_private_key" if has_private_key else "missing",
        "pear_builder": {
            "address": PEAR_BUILDER_ADDRESS,
            "fee_tenths_bps": PEAR_BUILDER_FEE_TENTHS_BPS,
            "fee_bps": pear_builder_fee_bps(),
        },
        "dedicated_wallet_guidance": (
            "Use a dedicated wallet for Pear campaign trades because Pear does not support subaccounts; "
            "mixing Pear baskets and normal perps in one wallet can confuse position display."
        ),
        "checks": checks,
    }

    if probe:
        payload["account_probe"] = _probe_account()
        payload["ready"] = payload["ready"] and payload["account_probe"]["status"] == "pass"

    if json_out:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Pear campaign readiness")
    typer.echo(f"Ready: {'yes' if payload['ready'] else 'no'}")
    typer.echo(f"Auth mode: {payload['auth_mode']}")
    typer.echo(
        f"Pear builder: {PEAR_BUILDER_ADDRESS} @ {pear_builder_fee_bps()} bps "
        f"({PEAR_BUILDER_FEE_TENTHS_BPS} tenths bps)"
    )
    for check in checks:
        typer.echo(f"- {check['name']}: {check['status']} — {check['message']}")
    if probe:
        account_probe = payload["account_probe"]
        typer.echo(f"- account_probe: {account_probe['status']} — {account_probe['message']}")
    typer.echo(payload["dedicated_wallet_guidance"])


def _check(name: str, ok: bool, message: str) -> Dict[str, Any]:
    return {"name": name, "status": "pass" if ok else "action_needed", "message": message}


def _probe_account() -> Dict[str, Any]:
    try:
        from cli.commands.pair import _open_pear

        account = _open_pear().get_account_state()
    except Exception as exc:
        return {"status": "action_needed", "message": f"Pear account probe failed: {exc}"}
    return {
        "status": "pass",
        "message": "Pear account read succeeded",
        "account_keys": sorted(str(k) for k in account.keys()),
    }
