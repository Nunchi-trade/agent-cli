"""hl pear — Pear Protocol campaign readiness helpers."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import requests
import typer

pear_app = typer.Typer(
    name="pear",
    help="Pear Protocol integration readiness and campaign setup.",
    no_args_is_help=True,
)
setup_app = typer.Typer(name="setup", help="Pear setup and readiness checks.", no_args_is_help=True)
pear_app.add_typer(setup_app, name="setup")

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


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
    from cli.pear_config import PEAR_BUILDER_ADDRESS, PEAR_BUILDER_FEE_TENTHS_BPS, pear_btcswp_asset, pear_builder_fee_bps

    address = os.getenv("PEAR_ADDRESS") or os.getenv("PEAR_WALLET_ADDRESS")
    api_key = os.getenv("PEAR_API_KEY")
    has_private_key = bool(os.getenv("HL_PRIVATE_KEY"))
    dedicated_ack = os.getenv("PEAR_DEDICATED_WALLET_ACK", "").lower() in {"1", "true", "yes"}

    checks = [
        _check("pear_address", bool(address), "PEAR_ADDRESS or PEAR_WALLET_ADDRESS is set"),
        _check("pear_api_key", bool(api_key), "PEAR_API_KEY is set; preferred for agents so user PK is not reused for JWT refresh"),
        _check("fallback_eip712_key", bool(api_key) or has_private_key, "PEAR_API_KEY or HL_PRIVATE_KEY is available for Pear auth"),
        _check("dedicated_wallet_ack", dedicated_ack, "PEAR_DEDICATED_WALLET_ACK=true acknowledges Pear's dedicated-wallet guidance"),
    ]
    payload: Dict[str, Any] = {
        "ready": all(c["status"] == "pass" for c in checks),
        "auth_mode": "api_key" if api_key else "eip712_private_key" if has_private_key else "missing",
        "btcswp_asset": pear_btcswp_asset(),
        "pear_builder": {
            "address": PEAR_BUILDER_ADDRESS,
            "fee_tenths_bps": PEAR_BUILDER_FEE_TENTHS_BPS,
            "fee_bps": pear_builder_fee_bps(),
            "attribution": "server_side",
        },
        "dedicated_wallet_guidance": (
            "Use a dedicated wallet for Pear campaign trades because Pear does not support subaccounts; "
            "mixing Pear baskets and normal perps in one wallet can confuse position display."
        ),
        "checks": checks,
    }

    if probe:
        probes = [
            _probe_account(),
            _probe_builder_approval(address),
            _probe_agent_wallet_approval(address),
        ]
        payload["probes"] = probes
        payload["ready"] = payload["ready"] and all(p["status"] == "pass" for p in probes)

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
    typer.echo(f"BTCSWP asset: {payload['btcswp_asset']}")
    typer.echo("Pear builder attribution is handled server-side; client orders do not include builder payloads.")
    for check in checks:
        typer.echo(f"- {check['name']}: {check['status']} — {check['message']}")
    if probe:
        for probe_result in payload["probes"]:
            typer.echo(f"- {probe_result['name']}: {probe_result['status']} — {probe_result['message']}")
    typer.echo(payload["dedicated_wallet_guidance"])


def _check(name: str, ok: bool, message: str) -> Dict[str, Any]:
    return {"name": name, "status": "pass" if ok else "action_needed", "message": message}


def _probe_account() -> Dict[str, Any]:
    try:
        from cli.commands.pair import _open_pear

        account = _open_pear().get_account_state()
    except Exception as exc:
        return {"name": "pear_account_probe", "status": "action_needed", "message": f"Pear account probe failed: {exc}"}
    return {
        "name": "pear_account_probe",
        "status": "pass",
        "message": "Pear account read succeeded",
        "account_keys": sorted(str(k) for k in account.keys()),
    }


def _probe_builder_approval(address: str | None) -> Dict[str, Any]:
    from cli.pear_config import PEAR_BUILDER_ADDRESS

    if not address:
        return {"name": "pear_builder_approval", "status": "action_needed", "message": "PEAR_ADDRESS is required to check approvedBuilders"}
    try:
        approved = _hl_info({"type": "approvedBuilders", "user": address})
    except Exception as exc:
        return {"name": "pear_builder_approval", "status": "action_needed", "message": f"HL approvedBuilders probe failed: {exc}"}
    approved_lower = {str(addr).lower() for addr in approved if addr}
    ok = PEAR_BUILDER_ADDRESS.lower() in approved_lower
    return {
        "name": "pear_builder_approval",
        "status": "pass" if ok else "action_needed",
        "message": "Pear builder is approved on Hyperliquid" if ok else "Pear builder is not approved on Hyperliquid",
        "builder": PEAR_BUILDER_ADDRESS,
    }


def _probe_agent_wallet_approval(address: str | None) -> Dict[str, Any]:
    if not address:
        return {"name": "pear_agent_wallet_approval", "status": "action_needed", "message": "PEAR_ADDRESS is required to check extraAgents"}
    try:
        from cli.commands.pair import _open_pear

        agent_wallet = _open_pear().get_agent_wallet()
        pear_agent_address = str(agent_wallet["agentWalletAddress"]).lower()
        extra_agents = _hl_info({"type": "extraAgents", "user": address})
    except Exception as exc:
        return {"name": "pear_agent_wallet_approval", "status": "action_needed", "message": f"Pear agent-wallet approval probe failed: {exc}"}
    approved = any(str(agent.get("address", "")).lower() == pear_agent_address for agent in extra_agents)
    return {
        "name": "pear_agent_wallet_approval",
        "status": "pass" if approved else "action_needed",
        "message": "Pear agent wallet is approved as a Hyperliquid extra agent" if approved else "Pear agent wallet is not approved as a Hyperliquid extra agent",
        "agent_wallet": pear_agent_address,
    }


def _hl_info(payload: Dict[str, Any]) -> Any:
    response = requests.post(HL_INFO_URL, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()
