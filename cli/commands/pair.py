"""hl pair — manage the web-auth paired-wallet handshake."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import typer

pair_app = typer.Typer(no_args_is_help=True)


def _ensure_path() -> None:
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def _short_addr(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}"


def _humanize_age(paired_at_ms: int) -> str:
    age_s = max(0, int(time.time() - paired_at_ms / 1000))
    if age_s < 60:
        return f"{age_s}s ago"
    if age_s < 3600:
        return f"{age_s // 60}m ago"
    if age_s < 86400:
        return f"{age_s // 3600}h ago"
    return f"{age_s // 86400}d ago"


@pair_app.command("connect", help="Pair the CLI with web-auth via the browser")
def pair_connect(
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the URL instead of opening a browser."),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait for browser approval."),
    app_name: str = typer.Option("HL Agent CLI", "--app-name", help="Display name shown on the authorize page."),
    agent_id: str = typer.Option("", "--agent-id", help="Stable local agent id to include in pairing metadata."),
    agent_name: str = typer.Option("", "--agent-name", help="Human-readable local agent name."),
    connection_mode: str = typer.Option(
        "clone-local",
        "--connection-mode",
        help="clone-local, hosted-mcp-tools, or hosted-mcp-tools-inference.",
    ),
) -> None:
    _ensure_path()
    from cli.web_auth import PairingTimedOutError, get_stored_pairing, start_pairing

    existing = get_stored_pairing()
    if existing:
        typer.echo(
            f"Already paired as {existing.label or '-'} "
            f"({len(existing.addresses)} address{'es' if len(existing.addresses) != 1 else ''})."
        )
        typer.echo("Run `hl pair revoke` first if you want to re-pair.", err=True)
        raise typer.Exit(1)

    def _on_url(url: str) -> None:
        if no_browser:
            typer.echo("Open this URL in your browser to approve pairing:")
            typer.echo(f"  {url}")
        else:
            typer.echo(f"Opening browser -> {url}")
        typer.echo("")
        typer.echo(f"Waiting for approval (up to {timeout}s)...")

    last_tick = [time.monotonic()]

    def _on_polling() -> None:
        if time.monotonic() - last_tick[0] >= 10:
            typer.echo("  ...still waiting in browser...")
            last_tick[0] = time.monotonic()

    try:
        result = start_pairing(
            app_name=app_name,
            agent_id=agent_id or None,
            agent_name=agent_name or app_name,
            connection_mode=connection_mode,
            no_browser=no_browser,
            on_url=_on_url,
            on_polling=_on_polling,
            timeout_s=timeout,
        )
    except PairingTimedOutError as exc:
        typer.echo(f"\n{exc}", err=True)
        raise typer.Exit(1)
    except KeyboardInterrupt:
        typer.echo("\nCancelled.", err=True)
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(f"\nPair failed: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo("")
    typer.echo(
        f"Paired as {result.label or '-'} - {len(result.addresses)} "
        f"address{'es' if len(result.addresses) != 1 else ''}."
    )
    if result.master_address:
        typer.echo(f"Master: {_short_addr(result.master_address)}  {result.master_address}")
    for addr in result.addresses:
        typer.echo(f"  {_short_addr(addr)}  {addr}")
    typer.echo("")
    typer.echo("Use `hl pair sign-test` to verify the signing relay works end-to-end.")


@pair_app.command("status", help="Show current pairing state")
def pair_status() -> None:
    _ensure_path()
    from cli.web_auth import PAIR_API_BASE, STORAGE_PATH, fetch_health, get_stored_pairing, verify_pairing

    pairing = get_stored_pairing()
    health = fetch_health()
    typer.echo(f"web-auth: {PAIR_API_BASE}")
    typer.echo("  status: ok" if health else "  status: UNREACHABLE")
    typer.echo("")

    if pairing is None:
        typer.echo("Pairing: NONE")
        typer.echo(f"  storage: {STORAGE_PATH} (missing or stale >28d)")
        typer.echo("  Run `hl pair connect` to pair.")
        return

    remote = None
    try:
        remote = verify_pairing()
    except Exception as exc:
        typer.echo(f"  verify: {exc}", err=True)

    typer.echo("Pairing: ACTIVE")
    typer.echo(f"  label: {pairing.label or '-'}")
    typer.echo(f"  paired: {_humanize_age(pairing.paired_at_ms)}")
    if pairing.account_id:
        typer.echo(f"  account: {pairing.account_id}")
    if pairing.agent_id:
        typer.echo(f"  agent: {pairing.agent_name or pairing.agent_id} ({pairing.agent_id})")
        typer.echo(f"  runtime: {pairing.runtime_location or 'local'} / {pairing.connection_mode or 'clone-local'}")
    if pairing.master_address:
        typer.echo(f"  master: {pairing.master_address}")
    typer.echo(f"  addresses ({len(pairing.addresses)}):")
    for index, addr in enumerate(pairing.addresses):
        marker = " *" if pairing.selected_address == addr else ""
        typer.echo(f"    [{index}] {addr}{marker}")
    typer.echo(f"  selected: {pairing.selected_or_master_address}")
    if remote and remote.get("activeSession"):
        typer.echo("  active session: yes")
    typer.echo(f"  storage: {STORAGE_PATH}")


@pair_app.command("list", help="List paired wallets as JSON")
def pair_list() -> None:
    _ensure_path()
    from cli.web_auth import get_stored_pairing

    pairing = get_stored_pairing()
    if pairing is None:
        typer.echo('{"ok": false, "wallets": [], "selectedAddress": null}')
        return
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "label": pairing.label,
                "accountId": pairing.account_id,
                "agentId": pairing.agent_id,
                "agentName": pairing.agent_name,
                "runtimeLocation": pairing.runtime_location,
                "connectionMode": pairing.connection_mode,
                "masterAddress": pairing.master_address,
                "selectedAddress": pairing.selected_or_master_address,
                "wallets": [
                    {
                        "index": index,
                        "address": address,
                        "selected": address == pairing.selected_or_master_address,
                    }
                    for index, address in enumerate(pairing.addresses)
                ],
            },
            indent=2,
        )
    )


@pair_app.command("select", help="Select which paired wallet CLI actions should use")
def pair_select(wallet: str = typer.Argument(..., help="paired wallet address or list index")) -> None:
    _ensure_path()
    from cli.web_auth import PairingMissingError, select_pairing_address

    try:
        pairing = select_pairing_address(wallet)
    except PairingMissingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"Selected paired wallet: {pairing.selected_address}")


@pair_app.command("open", help="Open web-auth for review, approval, revocation, or wallet binding")
def pair_open(
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the URL instead of opening a browser."),
    account_id: str = typer.Option("", "--account-id", help="Optional account id for agent-wallet binding view."),
    agent_id: str = typer.Option("", "--agent-id", help="Optional agent id for agent-wallet binding view."),
    agent_name: str = typer.Option("", "--agent-name", help="Optional display name for the agent-wallet binding view."),
    connection_mode: str = typer.Option(
        "clone-local",
        "--connection-mode",
        help="clone-local, hosted-mcp-tools, or hosted-mcp-tools-inference.",
    ),
    include_pair_token: bool = typer.Option(
        False,
        "--include-pair-token",
        help="Include the stored pair token in the web-auth URL so the UI can persist a binding for this CLI.",
    ),
) -> None:
    _ensure_path()
    from cli.web_auth import open_wallet_ui

    url = open_wallet_ui(
        no_browser=no_browser,
        account_id=account_id or None,
        agent_id=agent_id or None,
        agent_name=agent_name or None,
        runtime_location="local",
        connection_mode=connection_mode,
        include_pair_token=include_pair_token,
    )
    typer.echo(f"web-auth: {url}")


@pair_app.command("bind-role", help="Open web-auth to select and persist a maker/taker agent wallet")
def pair_bind_role(
    role: str = typer.Argument(..., help="Role to bind: maker or taker"),
    account_id: str = typer.Option("", "--account-id", help="web-auth account id for the binding. Defaults to the paired account."),
    agent_id: str = typer.Option("", "--agent-id", help="Override agent id. Defaults to agent-cli-cost-e2e-<role>."),
    agent_name: str = typer.Option("", "--agent-name", help="Override display name in web-auth."),
    connection_mode: str = typer.Option("clone-local", "--connection-mode", help="Connection mode to tag in web-auth."),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait for the web-auth selection."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the URL instead of opening a browser."),
) -> None:
    _ensure_path()
    from cli.web_auth import (
        PairingMissingError,
        PairingTimedOutError,
        open_wallet_ui,
        require_pairing,
        wait_for_agent_wallet_binding,
    )

    try:
        pairing = require_pairing()
    except PairingMissingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    role = role.lower().strip()
    if role not in {"maker", "taker"}:
        typer.echo("role must be `maker` or `taker`", err=True)
        raise typer.Exit(2)
    resolved_account_id = account_id or pairing.account_id or "agent-cli-cost-e2e"
    resolved_agent_id = agent_id or f"agent-cli-cost-e2e-{role}"
    resolved_agent_name = agent_name or f"Agent CLI Cost E2E {role.title()}"

    try:
        url = open_wallet_ui(
            no_browser=no_browser,
            account_id=resolved_account_id,
            agent_id=resolved_agent_id,
            agent_name=resolved_agent_name,
            runtime_location="local",
            connection_mode=connection_mode,
            include_pair_token=True,
        )
    except PairingMissingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Open web-auth and select the {role} wallet:")
    typer.echo(f"  {url}")
    typer.echo(f"Waiting for {role} binding to persist (up to {timeout}s)...")

    last_tick = [time.monotonic()]

    def _on_polling() -> None:
        if time.monotonic() - last_tick[0] >= 10:
            typer.echo("  ...still waiting for web-auth binding...")
            last_tick[0] = time.monotonic()

    try:
        binding = wait_for_agent_wallet_binding(
            account_id=resolved_account_id,
            agent_id=resolved_agent_id,
            role=role,
            timeout_s=timeout,
            on_polling=_on_polling,
        )
    except PairingTimedOutError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(f"Role binding failed: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Bound {role}: {binding.get('walletAddress')}")
    typer.echo(f"  accountId: {resolved_account_id}")
    typer.echo(f"  agentId: {resolved_agent_id}")


@pair_app.command("register", help="Register or update this local agent identity in web-auth")
def pair_register(
    agent_id: str = typer.Option(..., "--agent-id", help="Stable local agent id."),
    agent_name: str = typer.Option("", "--agent-name", help="Human-readable agent name."),
    account_id: str = typer.Option("", "--account-id", help="web-auth account id. Defaults to paired account."),
    connection_mode: str = typer.Option(
        "clone-local",
        "--connection-mode",
        help="clone-local, hosted-mcp-tools, or hosted-mcp-tools-inference.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON response."),
) -> None:
    _ensure_path()
    from cli.web_auth import PairingInvalidError, PairingMissingError, register_agent

    try:
        result = register_agent(
            account_id=account_id or None,
            agent_id=agent_id,
            agent_name=agent_name or agent_id,
            connection_mode=connection_mode,
        )
    except (PairingMissingError, PairingInvalidError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(f"Agent register failed: {exc}", err=True)
        raise typer.Exit(1)

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return
    agent = result.get("agent") or {}
    typer.echo(f"Registered agent: {agent.get('agentName') or agent_name or agent_id}")
    typer.echo(f"  agentId: {agent.get('agentId') or agent.get('agent_id') or agent_id}")
    typer.echo(f"  runtime: {agent.get('runtimeLocation') or 'local'} / {agent.get('connectionMode') or connection_mode}")


@pair_app.command("roles", help="Show maker/taker wallet-role selections stored for this pairing")
def pair_roles() -> None:
    _ensure_path()
    from cli.web_auth import get_stored_pairing

    pairing = get_stored_pairing()
    if pairing is None:
        typer.echo("No paired wallet. Run `hl pair connect` first.", err=True)
        raise typer.Exit(1)
    roles = pairing.role_addresses or {}
    if not roles:
        typer.echo("No maker/taker role bindings stored.")
        typer.echo("Run `hl pair bind-role maker` and `hl pair bind-role taker`.")
        return
    for role in ("maker", "taker"):
        typer.echo(f"{role}: {roles.get(role, '-')}")


@pair_app.command("pending", help="List backend-visible scoped approval requests for this pairing")
def pair_pending(json_output: bool = typer.Option(False, "--json", help="Print raw JSON.")) -> None:
    _ensure_path()
    from cli.web_auth import PairingInvalidError, PairingMissingError, fetch_pending_scoped_requests

    try:
        pending = fetch_pending_scoped_requests()
    except (PairingMissingError, PairingInvalidError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    if json_output:
        typer.echo(json.dumps({"ok": True, "pending": pending}, indent=2))
        return
    if not pending:
        typer.echo("No pending scoped requests for this pairing.")
        return
    for request in pending:
        eligible = "eligible" if request.get("programmatic_eligible") else f"browser-required ({request.get('programmatic_error')})"
        typer.echo(f"{request.get('request_id')} - {eligible}")
        if request.get("summary"):
            typer.echo(f"  {request['summary']}")
        if request.get("requested_signer"):
            typer.echo(f"  signer: {request['requested_signer']}")


@pair_app.command("approve", help="Approve an eligible scoped request through web-auth backend state")
def pair_approve(
    request_id: str = typer.Argument(..., help="Pending request id from `hl pair pending`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve without an interactive prompt."),
) -> None:
    _ensure_path()
    from cli.web_auth import PairingInvalidError, PairingMissingError, approve_scoped_request

    if not yes:
        typed = typer.prompt("Type approve to approve this scoped request")
        if typed.strip().lower() != "approve":
            typer.echo("Cancelled.")
            raise typer.Exit(0)
    try:
        result = approve_scoped_request(request_id, approval="approve")
    except (PairingMissingError, PairingInvalidError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        typer.echo("Open web-auth for browser approval if this request is not programmatic-eligible.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Approved scoped request {request_id}.")
    approval = result.get("approval") or {}
    if approval.get("signer"):
        typer.echo(f"  signer: {approval['signer']}")


@pair_app.command("revoke", help="Revoke the pairing locally and on the server")
def pair_revoke() -> None:
    _ensure_path()
    from cli.web_auth import clear_pairing, get_stored_pairing

    if get_stored_pairing() is None:
        typer.echo("No active pairing.")
        return
    clear_pairing()
    typer.echo("Pairing revoked.")


@pair_app.command("sign-test", help="Ask the paired wallet to sign harmless typed data")
def pair_sign_test() -> None:
    _ensure_path()
    from cli.web_auth import WALLET_AUTH_URL, get_selected_pairing_address, sign_with_pair

    signer = get_selected_pairing_address()
    typed_data = {
        "domain": {
            "name": "HL Agent CLI",
            "version": "1",
            "chainId": 42161,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "PairingTest": [
                {"name": "wallet", "type": "address"},
                {"name": "message", "type": "string"},
                {"name": "time", "type": "uint64"},
            ],
        },
        "primaryType": "PairingTest",
        "message": {
            "wallet": signer,
            "message": "Verify HL Agent CLI web-auth pairing",
            "time": int(time.time() * 1000),
        },
    }
    typer.echo(f"Open wallet approvals if needed: {WALLET_AUTH_URL}")
    sig = sign_with_pair(typed_data, "HL Agent CLI pairing sign-test")
    typer.echo(f"Signed: {sig}")

