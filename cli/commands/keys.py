"""hl keys — unified key management across backends."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

keys_app = typer.Typer(no_args_is_help=True)


def _ensure_path():
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


BACKEND_NAMES = ["keychain", "keystore", "file"]


@keys_app.command("import")
def keys_import(
    backend: str = typer.Option(
        "keystore", "--backend", "-b",
        help="Storage backend: keychain, keystore, or file",
    ),
):
    """Import a private key into the chosen backend."""
    _ensure_path()

    if backend not in BACKEND_NAMES:
        typer.echo(f"Unknown backend '{backend}'. Choose from: {', '.join(BACKEND_NAMES)}", err=True)
        raise typer.Exit(1)

    from common.credentials import get_backend

    be = get_backend(backend)
    if be is None or not be.available():
        typer.echo(f"Backend '{backend}' is not available on this system.", err=True)
        raise typer.Exit(1)

    private_key = typer.prompt("Private key (hex)", hide_input=True)
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    # Derive address from key
    try:
        from eth_account import Account
        acct = Account.from_key(private_key)
        address = acct.address.lower()
    except Exception as e:
        typer.echo(f"Invalid private key: {e}", err=True)
        raise typer.Exit(1)

    try:
        be.store_key(address, private_key)
        typer.echo(f"Key stored for {address} via {backend} backend.")
    except Exception as e:
        typer.echo(f"Failed to store key: {e}", err=True)
        raise typer.Exit(1)


@keys_app.command("list")
def keys_list():
    """List all keys across all available backends."""
    _ensure_path()

    from common.credentials import get_all_backends

    found_any = False
    typer.echo(f"{'Address':<44} {'Backend'}")
    typer.echo("-" * 60)

    for be in get_all_backends():
        if not be.available():
            continue
        try:
            addresses = be.list_keys()
            for addr in addresses:
                typer.echo(f"{addr:<44} {be.name()}")
                found_any = True
        except Exception:
            pass

    if not found_any:
        typer.echo("No keys found across any backend.")
        typer.echo("Import one with: hl keys import --backend keychain")


@keys_app.command("migrate")
def keys_migrate(
    from_backend: str = typer.Option(..., "--from", help="Source backend name"),
    to_backend: str = typer.Option(..., "--to", help="Destination backend name"),
    address: str = typer.Option("", "--address", "-a", help="Specific address to migrate (default: all)"),
):
    """Copy keys from one backend to another."""
    _ensure_path()

    from common.credentials import get_backend

    src = get_backend(from_backend)
    dst = get_backend(to_backend)

    if src is None or not src.available():
        typer.echo(f"Source backend '{from_backend}' not available.", err=True)
        raise typer.Exit(1)
    if dst is None or not dst.available():
        typer.echo(f"Destination backend '{to_backend}' not available.", err=True)
        raise typer.Exit(1)

    if address:
        addresses = [address.lower()]
    else:
        addresses = src.list_keys()

    if not addresses:
        typer.echo(f"No keys found in '{from_backend}' backend.")
        raise typer.Exit()

    migrated = 0
    for addr in addresses:
        try:
            key = src.get_key(addr)
            if key is None:
                typer.echo(f"  SKIP  {addr} — key not retrievable from {from_backend}")
                continue
            dst.store_key(addr, key)
            typer.echo(f"  OK    {addr} -> {to_backend}")
            migrated += 1
        except NotImplementedError as e:
            typer.echo(f"  FAIL  {addr} — {e}", err=True)
        except Exception as e:
            typer.echo(f"  FAIL  {addr} — {e}", err=True)

    typer.echo(f"\nMigrated {migrated}/{len(addresses)} key(s) from {from_backend} to {to_backend}.")
