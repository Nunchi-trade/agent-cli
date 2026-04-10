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

    # 1. Python + hyperliquid SDK
    try:
        import hyperliquid  # noqa: F401
        ok_items.append("hyperliquid-python-sdk installed")
    except ImportError:
        issues.append("hyperliquid-python-sdk not installed (pip install hyperliquid-python-sdk)")

    # 2. Private key
    from cli.keystore import _load_env_password, list_keystores
    from common.app_paths import env_file as default_env_file
    from common.credentials import private_key_env_vars_for_venue

    env_key_vars = private_key_env_vars_for_venue("hl")
    has_env_key = any(os.environ.get(var) for var in env_key_vars)
    keystore_count = len(list_keystores())
    if has_env_key:
        ok_items.append(f"Private key env set ({', '.join(env_key_vars)})")
    elif keystore_count > 0:
        ok_items.append(f"Keystore found ({keystore_count} keys)")
        if os.environ.get("AGENT_CLI_KEYSTORE_PASSWORD") or os.environ.get("KEYSTORE_PASSWORD"):
            ok_items.append("Generic keystore password env set")
        elif os.environ.get("HL_KEYSTORE_PASSWORD"):
            ok_items.append("Legacy HL_KEYSTORE_PASSWORD set via environment")
        elif _load_env_password():
            ok_items.append(f"Keystore password found in {default_env_file()}")
        else:
            issues.append("Keystore password not set (prefer AGENT_CLI_KEYSTORE_PASSWORD for auto-unlock)")
    else:
        issues.append("No private key: set a venue-specific env var or run 'hl wallet import'")

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

    typer.echo("\nBootstrap complete. Next: hl wallet auto --save-env")


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
