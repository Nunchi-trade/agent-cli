"""hl wallet — encrypted keystore management."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

wallet_app = typer.Typer(no_args_is_help=True)


@wallet_app.command("create")
def wallet_create():
    """Create a new wallet and save encrypted keystore."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from eth_account import Account
    from cli.keystore import create_keystore

    account = Account.create()
    typer.echo(f"New address: 0x{account.address[2:].lower()}")
    typer.echo("WARNING: Save your private key somewhere secure before encrypting.")
    typer.echo(f"Private key: {account.key.hex()}")
    typer.echo("")

    password = typer.prompt("Encryption password", hide_input=True)
    password_confirm = typer.prompt("Confirm password", hide_input=True)

    if password != password_confirm:
        typer.echo("Passwords don't match.", err=True)
        raise typer.Exit(1)

    ks_path = create_keystore(account.key.hex(), password)
    typer.echo(f"Keystore saved: {ks_path}")


@wallet_app.command("import")
def wallet_import(
    key: str = typer.Option(..., "--key", "-k", prompt=True, hide_input=True,
                            help="Private key (hex, with or without 0x prefix)"),
):
    """Import an existing private key into encrypted keystore."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.keystore import create_keystore

    if not key.startswith("0x"):
        key = "0x" + key

    password = typer.prompt("Encryption password", hide_input=True)
    password_confirm = typer.prompt("Confirm password", hide_input=True)

    if password != password_confirm:
        typer.echo("Passwords don't match.", err=True)
        raise typer.Exit(1)

    try:
        ks_path = create_keystore(key, password)
        typer.echo(f"Keystore saved: {ks_path}")
    except Exception as e:
        typer.echo(f"Failed to create keystore: {e}", err=True)
        raise typer.Exit(1)


@wallet_app.command("list")
def wallet_list():
    """List saved keystores."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.keystore import list_keystores

    keystores = list_keystores()

    if not keystores:
        typer.echo("No keystores found. Run 'hl wallet create' or 'hl wallet import'.")
        raise typer.Exit()

    typer.echo(f"{'Address':<44} {'Path'}")
    typer.echo("-" * 80)
    for ks in keystores:
        typer.echo(f"{ks['address']:<44} {ks['path']}")


@wallet_app.command("auto")
def wallet_auto(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (machine-parseable)"),
    save_env: bool = typer.Option(False, "--save-env", help="Save credentials to ~/.hl-agent/env"),
):
    """Create a new wallet non-interactively (agent-friendly, no prompts)."""
    import json
    import secrets

    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from eth_account import Account
    from cli.keystore import create_keystore

    # Generate random password and wallet
    password = secrets.token_urlsafe(32)
    account = Account.create()
    address = account.address

    ks_path = create_keystore(account.key.hex(), password)

    # Optionally persist to ~/.hl-agent/env
    if save_env:
        env_path = Path.home() / ".hl-agent" / "env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(
            f"HL_KEYSTORE_PASSWORD={password}\n"
        )
        env_path.chmod(0o600)

    if json_output:
        result = {
            "address": address,
            "password": password,
            "keystore": str(ks_path),
        }
        if save_env:
            result["env_file"] = str(env_path)
        typer.echo(json.dumps(result))
    else:
        typer.echo(f"Address:  {address}")
        typer.echo(f"Password: {password}")
        typer.echo(f"Keystore: {ks_path}")
        if save_env:
            typer.echo(f"Env file: {env_path}")
        typer.echo("")
        typer.echo("To use this wallet, set:")
        typer.echo(f"  export HL_KEYSTORE_PASSWORD={password}")
        typer.echo("")
        typer.echo("SAVE THE PASSWORD — it cannot be recovered.")


@wallet_app.command("export")
def wallet_export(
    address: str = typer.Option("", "--address", "-a",
                                help="Address to export (default: first keystore)"),
):
    """Export private key from keystore (decrypts with password)."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.keystore import list_keystores, load_keystore

    if not address:
        keystores = list_keystores()
        if not keystores:
            typer.echo("No keystores found.", err=True)
            raise typer.Exit(1)
        address = keystores[0]["address"]

    password = typer.prompt("Keystore password", hide_input=True)

    try:
        key = load_keystore(address, password)
        typer.echo(f"Address: {address}")
        typer.echo(f"Private key: {key}")
    except FileNotFoundError:
        typer.echo(f"No keystore found for {address}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Decryption failed: {e}", err=True)
        raise typer.Exit(1)
