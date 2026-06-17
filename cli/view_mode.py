"""Read-only view mode — operate against another address without a key.

Contract
--------
When the environment variable ``HL_VIEW_AS_USER`` is set to a Hyperliquid
address (``0x`` + 40 hex chars), the CLI runs in *view-only* mode:

  * Read commands (``account``, ``status``, ``journal``) fetch the named
    address' state from Hyperliquid's PUBLIC info API. No private key is
    loaded and nothing is signed.
  * Write commands (anything that places, cancels, or modifies orders, sets
    leverage, transfers funds, etc.) MUST refuse to run. They enforce this by
    calling :func:`require_not_view_only` before touching the exchange.

An explicit ``--address`` flag on a read command is equivalent to
``HL_VIEW_AS_USER`` for that one invocation. ``--address`` takes precedence
over the env var when both are present.

This module is intentionally dependency-free so it can be imported from any
command without pulling in the HL SDK.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import typer

VIEW_AS_ENV = "HL_VIEW_AS_USER"

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def normalize_address(address: str) -> Optional[str]:
    """Return the address unchanged if it is a valid HL address, else None."""
    if address and _ADDR_RE.match(address.strip()):
        return address.strip()
    return None


def view_address(explicit: Optional[str] = None) -> Optional[str]:
    """Resolve the read-only address to operate against.

    Precedence: explicit ``--address`` argument, then ``HL_VIEW_AS_USER``.
    Returns a normalized address or None if neither is set/valid.
    """
    if explicit:
        return normalize_address(explicit)
    env = os.environ.get(VIEW_AS_ENV, "")
    return normalize_address(env)


def is_view_only(explicit: Optional[str] = None) -> bool:
    """True when running read-only against another address (no key/signing)."""
    return view_address(explicit) is not None


def require_not_view_only(explicit: Optional[str] = None) -> None:
    """Guard for write commands: refuse to run in view-only mode.

    Future order-placing / state-mutating commands MUST call this before
    constructing a signing client. Raises ``typer.Exit(1)`` with a clear
    message when a view-only address is in effect.
    """
    addr = view_address(explicit)
    if addr is not None:
        src = "--address" if explicit else VIEW_AS_ENV
        typer.echo(
            f"Refusing to run: view-only mode is active ({src}={addr}). "
            "This command would sign/mutate state, which requires a private "
            "key and a real (non-view) session.",
            err=True,
        )
        raise typer.Exit(1)
