"""hl auth — local scoped-token management for keyless agent flows."""
from __future__ import annotations

import json
import time
from typing import Optional

import typer

auth_app = typer.Typer(no_args_is_help=True)


def _redact(token: str) -> str:
    if len(token) <= 12:
        return token[:2] + "..."
    return token[:6] + "..." + token[-4:]


@auth_app.command("import", help="Store a scoped Nunchi web-auth token locally")
def auth_import(
    token: str = typer.Option(..., "--token", prompt=True, hide_input=True, help="Scoped web-auth token."),
    address: str = typer.Option(..., "--address", help="Authorized wallet address."),
    account_id: str = typer.Option("", "--account-id", help="Optional Nunchi account id."),
    permission_tier: str = typer.Option(
        "testnet_trading",
        "--permission-tier",
        help="read_only, testnet_trading, or live_trading.",
    ),
    network: str = typer.Option("testnet", "--network", help="testnet or mainnet."),
    allow_mainnet: bool = typer.Option(False, "--allow-mainnet", help="Allow mainnet actions."),
    max_order_size: Optional[float] = typer.Option(None, "--max-order-size", help="Optional max order size."),
    max_hedge_notional: Optional[float] = typer.Option(
        None,
        "--max-hedge-notional",
        help="Optional max BTCSWP hedge notional in USD.",
    ),
    max_strategy_ticks: Optional[int] = typer.Option(None, "--max-strategy-ticks", help="Optional max ticks."),
    require_confirmation: bool = typer.Option(
        True,
        "--require-confirmation/--no-require-confirmation",
        help="Require confirmed=true for hosted/MCP write tools.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
) -> None:
    """Persist a scoped token so local CLI/MCP can sign without raw private keys."""
    from cli.web_auth import ScopedToken, save_scoped_token

    tier = permission_tier.strip().lower()
    if tier not in {"read_only", "testnet_trading", "live_trading"}:
        raise typer.BadParameter("permission-tier must be read_only, testnet_trading, or live_trading")
    net = network.strip().lower()
    if net not in {"testnet", "mainnet"}:
        raise typer.BadParameter("network must be testnet or mainnet")

    scoped = ScopedToken(
        token=token.strip(),
        address=address.strip(),
        account_id=account_id.strip(),
        permission_tier=tier,
        network=net,
        allow_mainnet=allow_mainnet,
        max_order_size=max_order_size,
        max_hedge_notional=max_hedge_notional,
        max_strategy_ticks=max_strategy_ticks,
        require_confirmation=require_confirmation,
        created_at_ms=int(time.time() * 1000),
    )
    path = save_scoped_token(scoped)
    payload = {
        "stored": True,
        "path": str(path),
        "address": scoped.address,
        "permission_tier": scoped.permission_tier,
        "network": scoped.network,
        "allow_mainnet": scoped.allow_mainnet,
        "token": _redact(scoped.token),
    }
    typer.echo(json.dumps(payload, indent=2) if json_output else f"Stored scoped token for {scoped.address} at {path}")


@auth_app.command("status", help="Show stored scoped-token status")
def auth_status(json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON.")) -> None:
    from cli.web_auth import load_scoped_token, scoped_token_path

    scoped = load_scoped_token()
    if scoped is None:
        payload = {"configured": False, "path": str(scoped_token_path())}
    else:
        payload = {
            "configured": True,
            "path": str(scoped_token_path()),
            "address": scoped.address,
            "account_id": scoped.account_id,
            "permission_tier": scoped.permission_tier,
            "network": scoped.network,
            "allow_mainnet": scoped.allow_mainnet,
            "max_order_size": scoped.max_order_size,
            "max_hedge_notional": scoped.max_hedge_notional,
            "max_strategy_ticks": scoped.max_strategy_ticks,
            "require_confirmation": scoped.require_confirmation,
            "token": _redact(scoped.token),
        }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    elif not payload["configured"]:
        typer.echo(f"No scoped token configured at {payload['path']}")
    else:
        typer.echo(
            f"Scoped token active for {payload['address']} "
            f"({payload['permission_tier']}, {payload['network']})"
        )


@auth_app.command("export-env", help="Print shell exports for the stored scoped token")
def auth_export_env() -> None:
    from cli.web_auth import scoped_token_env

    env = scoped_token_env()
    if not env:
        typer.echo("No scoped token configured.", err=True)
        raise typer.Exit(1)
    for key, value in env.items():
        escaped = value.replace("'", "'\"'\"'")
        typer.echo(f"export {key}='{escaped}'")


@auth_app.command("revoke", help="Delete the local scoped token")
def auth_revoke() -> None:
    from cli.web_auth import clear_scoped_token, scoped_token_path

    path = scoped_token_path()
    clear_scoped_token()
    typer.echo(f"Removed local scoped token at {path}")
