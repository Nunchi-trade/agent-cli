"""hl setup — environment validation and initialization."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

setup_app = typer.Typer(no_args_is_help=True)


@setup_app.command("check")
def setup_check():
    """Validate environment: SDK, keys, builder fee config."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    issues = []
    ok_items = []
    warnings = []

    # 1. Python + hyperliquid SDK
    try:
        import hyperliquid  # noqa: F401
        ok_items.append("hyperliquid-python-sdk installed")
    except ImportError:
        issues.append("hyperliquid-python-sdk not installed (pip install hyperliquid-python-sdk)")

    # 2. Private key
    has_env_key = bool(os.environ.get("HL_PRIVATE_KEY"))
    from cli.keystore import list_keystores
    from cli.web_auth import pairing_from_env
    has_keystore = len(list_keystores()) > 0
    pairing = pairing_from_env()
    if has_env_key:
        ok_items.append("HL_PRIVATE_KEY set")
        if pairing is None:
            warnings.append(
                "Raw-key mode active. For MCP/agent use, prefer `hl auth import` or hosted Nunchi Auth "
                "so the AI client receives scoped access instead of a private key."
            )
    elif has_keystore:
        ok_items.append(f"Keystore found ({len(list_keystores())} keys)")
        from cli.keystore import _load_env_password
        if os.environ.get("HL_KEYSTORE_PASSWORD"):
            ok_items.append("HL_KEYSTORE_PASSWORD set via environment")
        elif _load_env_password():
            ok_items.append("HL_KEYSTORE_PASSWORD found in ~/.hl-agent/env")
        else:
            issues.append("HL_KEYSTORE_PASSWORD not set (needed for auto-unlock)")
    elif pairing is not None:
        ok_items.append(f"scoped-token signing context found ({pairing.address})")
    else:
        issues.append("No private key: set HL_PRIVATE_KEY or run 'hl wallet import'")
    if pairing is not None:
        ok_items.append(f"web-auth pairing context provided ({pairing.address})")
    else:
        warnings.append(
            "No web-auth pairing context found. Hosted/keyless signing uses "
            "NUNCHI_WEB_AUTH_PAIR_TOKEN and NUNCHI_WEB_AUTH_ADDRESS, or run `hl auth import` locally."
        )

    # 3. Network
    testnet = os.environ.get("HL_TESTNET", "true").lower()
    ok_items.append(f"Network: {'testnet' if testnet == 'true' else 'mainnet'}")

    # 4. Builder fee
    from cli.config import TradingConfig
    cfg = TradingConfig()
    bcfg = cfg.get_builder_config()
    if bcfg.enabled:
        ok_items.append(f"Builder fee: {bcfg.fee_bps} bps -> {bcfg.builder_address[:10]}...")
    else:
        ok_items.append("Builder fee: not configured (optional)")

    # 5. LLM key (for claude_agent)
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        ok_items.append("LLM API key found")
    else:
        ok_items.append("LLM API key: not set (only needed for claude_agent strategy)")

    # 6. Data directories
    data_dir = Path("data/cli")
    if data_dir.exists():
        ok_items.append(f"Data dir: {data_dir} exists")
    else:
        ok_items.append(f"Data dir: {data_dir} (will be created on first run)")

    # Report
    typer.echo("Environment Check")
    typer.echo("=" * 40)

    for item in ok_items:
        typer.echo(f"  OK  {item}")

    if issues:
        typer.echo("")
        for issue in issues:
            typer.echo(f"  !!  {issue}")
        typer.echo(f"\n{len(issues)} issue(s) found.")
    else:
        typer.echo("\nAll checks passed.")

    if warnings:
        typer.echo("")
        for warning in warnings:
            typer.echo(f"  WARN  {warning}")


@setup_app.command("bootstrap")
def setup_bootstrap():
    """Bootstrap environment: check Python, create venv if needed, install package."""
    import subprocess

    project_root = Path(__file__).resolve().parent.parent.parent

    # 1. Python version check
    if sys.version_info < (3, 10):
        typer.echo(f"ERROR: Python 3.10+ required (found {sys.version_info.major}.{sys.version_info.minor})")
        raise typer.Exit(1)
    typer.echo(f"OK  Python {sys.version_info.major}.{sys.version_info.minor}")

    # 2. Check if in venv
    in_venv = sys.prefix != sys.base_prefix
    venv_dir = project_root / ".venv"

    if not in_venv:
        if not venv_dir.exists():
            typer.echo(f"Creating venv at {venv_dir} ...")
            import venv
            venv.create(str(venv_dir), with_pip=True)
        typer.echo(f"NOTE: Activate venv first:  source {venv_dir}/bin/activate")
        typer.echo("Then re-run:  hl setup bootstrap")
        raise typer.Exit(0)
    else:
        typer.echo(f"OK  In venv: {sys.prefix}")

    # 3. Install package
    typer.echo("Installing agent-cli ...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(project_root), "--quiet"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        typer.echo(f"ERROR: pip install failed:\n{result.stderr}", err=True)
        raise typer.Exit(1)
    typer.echo("OK  Package installed")

    # 4. Run check
    typer.echo("")
    setup_check()

    typer.echo("\nBootstrap complete. Next: hl wallet auto")


@setup_app.command("claim-usdyp")
def setup_claim_usdyp():
    """Claim testnet USDyP tokens (required for YEX markets)."""
    import json
    import urllib.request

    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Derive address from private key
    from cli.config import TradingConfig

    cfg = TradingConfig()
    try:
        key = cfg.get_private_key()
    except RuntimeError as e:
        typer.echo(f"ERROR: {e}", err=True)
        typer.echo("Run 'hl wallet auto' first to create a wallet.")
        raise typer.Exit(1)

    from eth_account import Account
    acct = Account.from_key(key)
    address = acct.address

    typer.echo(f"Claiming USDyP for {address} ...")

    url = "https://api-temp.nunchi.trade/api/v1/yex/usdyp-claim"
    payload = json.dumps({"userAddress": address}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-network": "testnet",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            typer.echo(f"OK  Claim response: {body}")
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        typer.echo(f"ERROR: HTTP {e.code}: {body}", err=True)
        if "not eligible" in body.lower() or "verify" in body.lower():
            typer.echo("")
            typer.echo("This wallet hasn't been seen by Hyperliquid yet.")
            typer.echo("")
            typer.echo("  One-time fix (takes 30 seconds):")
            typer.echo("  1. Visit https://app.hyperliquid-testnet.xyz")
            typer.echo("  2. Connect wallet: " + address)
            typer.echo("  3. Re-run: hl setup claim-usdyp")
            typer.echo("")
            typer.echo("This is a Hyperliquid requirement for fresh wallets — only needed once.")
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)


@setup_app.command("status")
def setup_status(
    workspace: str = typer.Option("", "--workspace", "-w",
                                  help="Workspace id (echoed into the report)"),
    json_output: bool = typer.Option(False, "--json",
                                     help="Output the full readiness report as JSON"),
    mainnet: bool = typer.Option(False, "--mainnet",
                                 help="Check against mainnet (default: testnet)"),
    no_probe: bool = typer.Option(False, "--no-probe",
                                  help="Skip live HL network probes (offline shape)"),
):
    """Readiness report: can this workspace get to first attributed fill?

    Aggregates explicit per-check pass/fail/action_needed/na with a human reason
    for each, plus a top-level `ready` boolean. This command is the backend
    truth for onboarding state — see cli/readiness.py.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.readiness import build_readiness_report

    report = build_readiness_report(
        workspace=workspace or None,
        testnet=not mainnet,
        probe_network=not no_probe,
    )

    if json_output:
        import json
        typer.echo(json.dumps(report, indent=2))
        raise typer.Exit(0 if report["ready"] else 1)

    # Human-readable rendering.
    typer.echo("Readiness Report")
    typer.echo("=" * 50)
    if report["workspace"]:
        typer.echo(f"  Workspace: {report['workspace']}")
    typer.echo(f"  Network:   {report['network']}")
    typer.echo("")

    symbol = {"pass": "OK ", "fail": "!! ", "action_needed": "-> ",
              "na": " . ", "unknown": " ? "}
    for c in report["checks"]:
        mark = symbol.get(c["status"], " ? ")
        typer.echo(f"  [{mark}] {c['id']}: {c['status']}")
        typer.echo(f"        {c['detail']}")

    s = report["summary"]
    typer.echo("")
    typer.echo(f"  {s['total']} checks; {s['blocking']} blocking "
               f"({', '.join(s['blocking_ids']) or 'none'})")
    typer.echo("")
    typer.echo(f"READY: {report['ready']}")
    raise typer.Exit(0 if report["ready"] else 1)


@setup_app.command("hl-onboard")
def setup_hl_onboard(
    mainnet: bool = typer.Option(False, "--mainnet",
                                 help="Check against mainnet (default: testnet)"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Helper for the Hyperliquid wallet onboarding (web-action) step.

    Fresh wallets must be "seen" by Hyperliquid once via the web app before
    deposits/claims work. There is no headless EIP-191 verification endpoint
    discoverable in this repo, so this command reports the wallet, its current
    onboarding status (read-only probe), and the REAL browser action URL — it
    does NOT fabricate an endpoint. The URL is the same one used by
    'hl setup claim-usdyp'.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.readiness import (
        HL_TESTNET_ONBOARD_URL,
        _resolve_address,
        check_hl_onboarding,
    )

    address = _resolve_address()
    check = check_hl_onboarding(testnet=not mainnet)
    onboard_url = HL_TESTNET_ONBOARD_URL if not mainnet else None

    if json_output:
        import json
        typer.echo(json.dumps({
            "address": address,
            "network": "testnet" if not mainnet else "mainnet",
            "onboarding": check,
            # Labeled clearly: this is a browser action, NOT an API endpoint.
            "browser_action_url": onboard_url,
            "verification_flow": "web-connect",  # no headless EIP-191 path in repo
        }, indent=2))
        raise typer.Exit(0 if check["status"] == "pass" else 1)

    typer.echo("Hyperliquid Wallet Onboarding")
    typer.echo("=" * 40)
    typer.echo(f"  Wallet:  {address or '(none configured)'}")
    typer.echo(f"  Network: {'testnet' if not mainnet else 'mainnet'}")
    typer.echo(f"  Status:  {check['status']} — {check['detail']}")
    typer.echo("")

    if check["status"] == "pass":
        typer.echo("Already onboarded. No action needed.")
        raise typer.Exit(0)

    if not address:
        typer.echo("Next: run 'hl wallet auto --save-env' to create a wallet, "
                   "then re-run this command.")
        raise typer.Exit(1)

    if onboard_url:
        typer.echo("Action needed (one-time, ~30s, cannot be done headlessly):")
        typer.echo(f"  1. Open {onboard_url}")
        typer.echo(f"  2. Connect this wallet: {address}")
        typer.echo("  3. Deposit/fund, then re-run: hl setup status")
        typer.echo("")
        typer.echo("NOTE: no headless EIP-191 verification endpoint exists in this "
                   "repo; the step above is a browser action.")
    else:
        typer.echo("Action needed: deposit funds to this wallet on mainnet, "
                   "then re-run: hl setup status")
    raise typer.Exit(1)
