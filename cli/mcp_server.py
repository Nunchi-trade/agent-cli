"""MCP server for agent-cli — exposes trading tools via Model Context Protocol.

Fast tools (account, strategies, builder, wallet, setup) call Python directly.
Long-running tools (run_strategy, apex_run, radar, reflect) use subprocess.

Every tool carries MCP annotations (readOnlyHint / destructiveHint) so MCP
clients can distinguish a harmless read from a fund-moving action. The
classification lives in the module-level sets below so it can be unit-tested
without importing the optional `mcp` package.
"""
from __future__ import annotations

import json
import hmac
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    from mcp.server.fastmcp import Context as FastMCPContext
except Exception:  # pragma: no cover - optional mcp dependency
    FastMCPContext = Any


# Tools that only read state (no side effects, safe to call freely).
_READ_ONLY_TOOLS = {
    "strategies", "builder_status", "wallet_list", "setup_check",
    "account", "status", "apex_status",
    "agent_memory", "trade_journal", "judge_report", "obsidian_context",
    "order_status", "funding_rates", "btcswp_hedge_quote",
}
# Tools that move funds or cancel/close live orders/positions — handle with care.
_DESTRUCTIVE_TOOLS = {
    "trade", "run_strategy", "apex_run", "schedule_cancel", "emergency_close_all",
    "btcswp_hedge_execute",
}
# Everything else (wallet_auto, radar_run, reflect_run) is
# state-changing-but-safe: neither a pure read nor fund-destructive.

_TRUSTED_CONTEXT_SECRET_ENVS = (
    "NUNCHI_RUNNER_CONTEXT_SECRET",
    "NUNCHI_GATEWAY_CONTEXT_SECRET",
)
_CONTEXT_SECRET_HEADERS = (
    "x-nunchi-runner-context-secret",
    "x-nunchi-gateway-context-secret",
    "x-nunchi-secret-nunchi-runner-context-secret",
    "x-nunchi-secret-nunchi-gateway-context-secret",
)
_CONTEXT_ENV_HEADERS = {
    "x-nunchi-web-auth-pair-token": "NUNCHI_WEB_AUTH_PAIR_TOKEN",
    "x-nunchi-secret-nunchi-web-auth-pair-token": "NUNCHI_WEB_AUTH_PAIR_TOKEN",
    "x-nunchi-web-auth-address": "NUNCHI_WEB_AUTH_ADDRESS",
    "x-nunchi-secret-nunchi-web-auth-address": "NUNCHI_WEB_AUTH_ADDRESS",
    "x-nunchi-trading-permission-tier": "NUNCHI_TRADING_PERMISSION_TIER",
    "x-nunchi-secret-nunchi-trading-permission-tier": "NUNCHI_TRADING_PERMISSION_TIER",
    "x-nunchi-trading-network": "NUNCHI_TRADING_NETWORK",
    "x-nunchi-secret-nunchi-trading-network": "NUNCHI_TRADING_NETWORK",
    "x-nunchi-allow-mainnet": "NUNCHI_ALLOW_MAINNET",
    "x-nunchi-secret-nunchi-allow-mainnet": "NUNCHI_ALLOW_MAINNET",
    "x-nunchi-max-order-size": "NUNCHI_MAX_ORDER_SIZE",
    "x-nunchi-secret-nunchi-max-order-size": "NUNCHI_MAX_ORDER_SIZE",
    "x-nunchi-max-strategy-ticks": "NUNCHI_MAX_STRATEGY_TICKS",
    "x-nunchi-secret-nunchi-max-strategy-ticks": "NUNCHI_MAX_STRATEGY_TICKS",
    "x-nunchi-require-confirmation": "NUNCHI_REQUIRE_CONFIRMATION",
    "x-nunchi-secret-nunchi-require-confirmation": "NUNCHI_REQUIRE_CONFIRMATION",
    "x-nunchi-session-policy": "NUNCHI_SESSION_POLICY",
    "x-nunchi-secret-nunchi-session-policy": "NUNCHI_SESSION_POLICY",
    "x-nunchi-workspace": "NUNCHI_WORKSPACE",
    "x-nunchi-secret-nunchi-workspace": "NUNCHI_WORKSPACE",
}
_CONTEXT_ENV_NAMES = frozenset(_CONTEXT_ENV_HEADERS.values())
_MAX_CONTEXT_VALUE_LEN = 64_000


def _clean_context_value(value: Any) -> Optional[str]:
    """Return a safe env value, or None for absent/invalid context."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or "\x00" in text or len(text) > _MAX_CONTEXT_VALUE_LEN:
        return None
    return text


def _headers_from_context(ctx: Any) -> dict[str, str]:
    """Best-effort HTTP header extraction from FastMCP Context variants."""
    if ctx is None:
        return {}

    request_context = getattr(ctx, "request_context", None)
    request = getattr(request_context, "request", None)
    headers_obj = getattr(request, "headers", None)
    headers: dict[str, str] = {}

    if headers_obj is not None:
        items = headers_obj.items() if hasattr(headers_obj, "items") else []
        for key, value in items:
            cleaned = _clean_context_value(value)
            if cleaned is not None:
                headers[str(key).lower()] = cleaned

    scope = getattr(request_context, "scope", None) or getattr(request, "scope", None)
    raw_headers = scope.get("headers") if isinstance(scope, dict) else None
    if raw_headers:
        for key, value in raw_headers:
            try:
                h_key = key.decode("latin1").lower() if isinstance(key, bytes) else str(key).lower()
                h_val = value.decode("latin1") if isinstance(value, bytes) else str(value)
            except Exception:
                continue
            cleaned = _clean_context_value(h_val)
            if cleaned is not None:
                headers[h_key] = cleaned

    return headers


def _meta_from_context(ctx: Any) -> dict[str, Any]:
    """Extract MCP request metadata when the SDK provides it."""
    request_context = getattr(ctx, "request_context", None) if ctx is not None else None
    meta = getattr(request_context, "meta", None)
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return dict(meta)
    if hasattr(meta, "model_dump"):
        dumped = meta.model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _configured_context_secret() -> Optional[str]:
    for env_name in _TRUSTED_CONTEXT_SECRET_ENVS:
        secret = os.environ.get(env_name)
        if secret and secret.strip():
            return secret.strip()
    return None


def _bearer_token(authorization: str) -> Optional[str]:
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix):].strip()
    return None


def _trusted_context(headers: dict[str, str], meta: dict[str, Any]) -> bool:
    expected = _configured_context_secret()
    if not expected:
        return False

    candidates: list[str] = []
    for header in _CONTEXT_SECRET_HEADERS:
        value = headers.get(header)
        if value:
            candidates.append(value)

    auth_token = _bearer_token(headers.get("authorization", ""))
    if auth_token:
        candidates.append(auth_token)

    for key in ("nunchi_runner_context_secret", "nunchi_gateway_context_secret"):
        value = _clean_context_value(meta.get(key))
        if value:
            candidates.append(value)

    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)


def _policy_from_context_env(env: dict[str, str]) -> Optional[str]:
    """Build the local CLI session policy implied by trusted gateway context."""
    if env.get("NUNCHI_SESSION_POLICY"):
        return None

    policy: dict[str, Any] = {}
    address = env.get("NUNCHI_WEB_AUTH_ADDRESS")
    if address:
        policy["wallets"] = [address]

    network = (env.get("NUNCHI_TRADING_NETWORK") or "").strip().lower()
    if network in ("mainnet", "testnet"):
        policy["network"] = network

    tier = (env.get("NUNCHI_TRADING_PERMISSION_TIER") or "").strip().lower()
    if tier == "read_only":
        policy["allowed_actions"] = ["__read_only__"]
    elif tier in ("testnet_trading", "live_trading"):
        policy["allowed_actions"] = ["trade", "run", "builder-approve"]

    if not policy:
        return None
    return json.dumps(policy, separators=(",", ":"))


def _trusted_context_env_overrides(ctx: Any) -> dict[str, str]:
    """Return per-request env overrides, only from authenticated gateway context."""
    headers = _headers_from_context(ctx)
    meta = _meta_from_context(ctx)
    if not _trusted_context(headers, meta):
        return {}

    overrides: dict[str, str] = {}
    for header, env_name in _CONTEXT_ENV_HEADERS.items():
        value = _clean_context_value(headers.get(header))
        if value is not None:
            overrides[env_name] = value

    meta_context = meta.get("nunchi_runner_context") or meta.get("nunchi_context")
    if isinstance(meta_context, dict):
        for env_name in _CONTEXT_ENV_NAMES:
            value = _clean_context_value(
                meta_context.get(env_name)
                or meta_context.get(env_name.lower())
                or meta_context.get(env_name.lower().replace("_", "-"))
            )
            if value is not None:
                overrides[env_name] = value

    for env_name in _CONTEXT_ENV_NAMES:
        value = _clean_context_value(meta.get(env_name) or meta.get(env_name.lower()))
        if value is not None:
            overrides[env_name] = value

    policy = _policy_from_context_env(overrides)
    if policy is not None:
        overrides["NUNCHI_SESSION_POLICY"] = policy
    return overrides


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _effective_env(name: str, overrides: dict[str, str]) -> str:
    return overrides.get(name) or os.environ.get(name, "")


def _has_signing_context(overrides: Optional[dict[str, str]] = None) -> bool:
    env = overrides or {}
    has_web_auth = bool(_effective_env("NUNCHI_WEB_AUTH_PAIR_TOKEN", env)) and bool(
        _effective_env("NUNCHI_WEB_AUTH_ADDRESS", env)
    )
    has_private_key = bool(_effective_env("HL_PRIVATE_KEY", env))
    has_keystore = bool(_effective_env("HL_KEYSTORE_PASSWORD", env)) or (
        Path.home() / ".hl-agent" / "env"
    ).exists()
    return has_web_auth or has_private_key or has_keystore


def _context_limit_error(
    tool_name: str,
    env_overrides: dict[str, str],
    *,
    mainnet: bool = False,
    size: Optional[float] = None,
    max_ticks: Optional[int] = None,
    confirmed: bool = False,
    require_signing: bool = False,
) -> Optional[str]:
    if require_signing and not _has_signing_context(env_overrides):
        return (
            f"{tool_name} requires a signing context: configure HL_PRIVATE_KEY/"
            "keystore or pass trusted NUNCHI_WEB_AUTH_PAIR_TOKEN and "
            "NUNCHI_WEB_AUTH_ADDRESS context."
        )

    tier = _effective_env("NUNCHI_TRADING_PERMISSION_TIER", env_overrides).strip().lower()
    if tier == "read_only" and tool_name in (_DESTRUCTIVE_TOOLS | {"wallet_auto"}):
        return f"{tool_name} is not allowed for read_only hosted trading sessions."
    if tier == "testnet_trading" and mainnet:
        return "mainnet access requires live_trading permission."

    pinned_network = _effective_env("NUNCHI_TRADING_NETWORK", env_overrides).strip().lower()
    if pinned_network == "testnet" and mainnet:
        return "mainnet access is refused because the session is pinned to testnet."
    if mainnet and _effective_env("NUNCHI_ALLOW_MAINNET", env_overrides):
        if not _truthy(_effective_env("NUNCHI_ALLOW_MAINNET", env_overrides)):
            return "mainnet access is refused by NUNCHI_ALLOW_MAINNET."

    if _truthy(_effective_env("NUNCHI_REQUIRE_CONFIRMATION", env_overrides)) and not confirmed:
        return f"{tool_name} requires confirmed=true for this hosted trading session."

    max_order_size = _effective_env("NUNCHI_MAX_ORDER_SIZE", env_overrides)
    if size is not None and max_order_size:
        try:
            allowed_size = float(max_order_size)
        except ValueError:
            return "invalid NUNCHI_MAX_ORDER_SIZE in trusted context."
        if allowed_size > 0 and abs(float(size)) > allowed_size:
            return f"trade size {size} exceeds max order size {allowed_size}."

    max_strategy_ticks = _effective_env("NUNCHI_MAX_STRATEGY_TICKS", env_overrides)
    if max_ticks is not None and max_strategy_ticks:
        try:
            allowed_ticks = int(float(max_strategy_ticks))
        except ValueError:
            return "invalid NUNCHI_MAX_STRATEGY_TICKS in trusted context."
        if allowed_ticks > 0 and int(max_ticks) > allowed_ticks:
            return f"max_ticks {max_ticks} exceeds max strategy ticks {allowed_ticks}."

    return None


def _trusted_max_ticks(env_overrides: dict[str, str]) -> Optional[int]:
    value = _effective_env("NUNCHI_MAX_STRATEGY_TICKS", env_overrides)
    if not value:
        return None
    try:
        parsed = int(float(value))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _json_error(message: str) -> str:
    return json.dumps({"error": message}, indent=2)


def _run_hl(*args: str, timeout: int = 30, env_overrides: Optional[dict[str, str]] = None) -> str:
    """Run an hl CLI command via subprocess and return stdout."""
    cmd = [sys.executable, "-m", "cli.main", *args]
    env = os.environ.copy()
    if env_overrides:
        env.update({k: v for k, v in env_overrides.items() if v is not None})
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    output = result.stdout.strip()
    if result.returncode != 0 and result.stderr:
        output = output + "\n" + result.stderr.strip() if output else result.stderr.strip()
    return output or "(no output)"


def create_mcp_server():
    """Create and configure the FastMCP server."""
    from mcp.server.fastmcp import FastMCP

    # ToolAnnotations is part of the MCP spec (2025-03-26). Import defensively so
    # the server still builds against an older `mcp` that predates it.
    try:
        from mcp.types import ToolAnnotations
    except Exception:  # pragma: no cover - exercised only on very old mcp
        ToolAnnotations = None

    def _ann(name: str, title: str):
        """Build the annotations kwarg for @mcp.tool from the safety sets."""
        if ToolAnnotations is None:
            return {}
        return {"annotations": ToolAnnotations(
            title=title,
            readOnlyHint=name in _READ_ONLY_TOOLS,
            destructiveHint=name in _DESTRUCTIVE_TOOLS,
        )}

    mcp = FastMCP(
        "yex-trader",
        instructions=(
            "Autonomous Hyperliquid trading CLI — 14 strategies, APEX orchestrator, "
            "REFLECT reviews. Always confirm details with the user before calling "
            "destructive tools (trade, run_strategy, apex_run, schedule_cancel, "
            "emergency_close_all). "
            "emergency_close_all requires confirm=true."
        ),
    )

    def _request_env(ctx: Any = None) -> dict[str, str]:
        if ctx is not None:
            return _trusted_context_env_overrides(ctx)
        try:
            get_context = getattr(mcp, "get_context")
        except AttributeError:
            return {}
        try:
            return _trusted_context_env_overrides(get_context())
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Fast tools — call Python directly (no subprocess overhead)
    # ------------------------------------------------------------------

    @mcp.tool(**_ann("strategies", "List strategies"))
    def strategies() -> str:
        """List all available trading strategies with descriptions and default parameters."""
        from cli.strategy_registry import STRATEGY_REGISTRY, YEX_MARKETS

        result = {"strategies": {}, "yex_markets": {}}
        for name, info in STRATEGY_REGISTRY.items():
            result["strategies"][name] = {
                "description": info.get("description", ""),
                "type": info.get("type", ""),
                "params": {k: v for k, v in info.get("params", {}).items()},
            }
        for name, info in YEX_MARKETS.items():
            result["yex_markets"][name] = {
                "hl_coin": info.get("hl_coin", ""),
                "description": info.get("description", ""),
            }
        return json.dumps(result, indent=2)

    @mcp.tool(**_ann("builder_status", "Builder fee status"))
    def builder_status() -> str:
        """Get builder fee configuration status."""
        from cli.config import TradingConfig

        cfg = TradingConfig()
        bcfg = cfg.get_builder_config()
        return json.dumps({
            "enabled": bcfg.enabled,
            "builder_address": bcfg.builder_address,
            "fee_bps": bcfg.fee_bps,
            "fee_rate_tenths_bps": bcfg.fee_rate_tenths_bps,
            "max_fee_rate_str": bcfg.max_fee_rate_str,
        }, indent=2)

    @mcp.tool(**_ann("wallet_list", "List wallets"))
    def wallet_list() -> str:
        """List saved encrypted keystores."""
        from cli.keystore import list_keystores

        keystores = list_keystores()
        return json.dumps(keystores, indent=2) if keystores else "No keystores found."

    @mcp.tool(**_ann("wallet_auto", "Create wallet"))
    def wallet_auto(
        save_env: bool = True,
        confirmed: bool = False,
        ctx: FastMCPContext = None,
    ) -> str:
        """Create a new wallet non-interactively (agent-friendly).

        Args:
            save_env: Save credentials to ~/.hl-agent/env for auto-detection (default: True)
            confirmed: Explicit confirmation for hosted gateway sessions that require it.
        """
        import secrets
        from pathlib import Path
        from eth_account import Account
        from cli.keystore import create_keystore

        env_overrides = _request_env(ctx)
        error = _context_limit_error("wallet_auto", env_overrides, confirmed=confirmed)
        if error:
            return _json_error(error)

        password = secrets.token_urlsafe(32)
        account = Account.create()
        ks_path = create_keystore(account.key.hex(), password)

        result = {
            "address": account.address,
            "password": password,
            "keystore": str(ks_path),
        }

        if save_env:
            env_path = Path.home() / ".hl-agent" / "env"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text(f"HL_KEYSTORE_PASSWORD={password}\n")
            env_path.chmod(0o600)
            result["env_file"] = str(env_path)

        return json.dumps(result, indent=2)

    @mcp.tool(**_ann("setup_check", "Validate setup"))
    def setup_check(ctx: FastMCPContext = None) -> str:
        """Validate environment — SDK, keys, network, builder fee."""
        import os
        from cli.keystore import list_keystores
        from cli.config import TradingConfig

        env_overrides = _request_env(ctx)
        issues = []
        ok_items = []

        # SDK
        try:
            import hyperliquid  # noqa: F401
            ok_items.append("hyperliquid-python-sdk installed")
        except ImportError:
            issues.append("hyperliquid-python-sdk not installed")

        # Key
        has_env_key = bool(env_overrides.get("HL_PRIVATE_KEY") or os.environ.get("HL_PRIVATE_KEY"))
        has_web_auth = bool(env_overrides.get("NUNCHI_WEB_AUTH_PAIR_TOKEN")) and bool(
            env_overrides.get("NUNCHI_WEB_AUTH_ADDRESS")
        )
        keystores = list_keystores()
        if has_env_key:
            ok_items.append("HL_PRIVATE_KEY set")
        elif has_web_auth:
            ok_items.append("web-auth pairing context provided")
        elif keystores:
            ok_items.append(f"Keystore found ({len(keystores)} keys)")
        else:
            issues.append(
                "No signing context: set HL_PRIVATE_KEY, configure keystore, "
                "or pass trusted web-auth pairing context"
            )

        # Network
        testnet = os.environ.get("HL_TESTNET", "true").lower()
        ok_items.append(f"Network: {'testnet' if testnet == 'true' else 'mainnet'}")

        # Builder
        cfg = TradingConfig()
        bcfg = cfg.get_builder_config()
        if bcfg.enabled:
            ok_items.append(f"Builder fee: {bcfg.fee_bps} bps")
        else:
            ok_items.append("Builder fee: not configured")

        return json.dumps({
            "ok": ok_items,
            "issues": issues,
            "passed": len(issues) == 0,
        }, indent=2)

    @mcp.tool(**_ann("account", "Account state"))
    def account(mainnet: bool = False, ctx: FastMCPContext = None) -> str:
        """Get Hyperliquid account state (balances, positions)."""
        # Account requires live HL connection — use subprocess for isolation
        env_overrides = _request_env(ctx)
        args = ["account"]
        if mainnet:
            args.append("--mainnet")
        return _run_hl(*args, env_overrides=env_overrides)

    @mcp.tool(**_ann("status", "Positions & risk"))
    def status(ctx: FastMCPContext = None) -> str:
        """Show current positions, PnL, and risk state."""
        return _run_hl("status", env_overrides=_request_env(ctx))

    # ------------------------------------------------------------------
    # Action tools — subprocess (side effects, long-running)
    # ------------------------------------------------------------------

    @mcp.tool(**_ann("trade", "Place a single order"))
    def trade(
        instrument: str,
        side: str,
        size: float,
        mainnet: bool = False,
        confirmed: bool = False,
        ctx: FastMCPContext = None,
    ) -> str:
        """Place a single manual order. WARNING: executes a real trade with real funds.

        Args:
            instrument: Trading pair (e.g., ETH-PERP, BTC-PERP, VXX-USDYP)
            side: Order side — "buy" or "sell"
            size: Order size in contracts
            mainnet: Use mainnet instead of testnet
            confirmed: Explicit confirmation for hosted gateway sessions that require it.
        """
        env_overrides = _request_env(ctx)
        error = _context_limit_error(
            "trade",
            env_overrides,
            mainnet=mainnet,
            size=size,
            confirmed=confirmed,
            require_signing=True,
        )
        if error:
            return _json_error(error)

        args = ["trade", instrument, side, str(size)]
        if mainnet:
            args.append("--mainnet")
        if confirmed or env_overrides:
            args.append("--yes")
        return _run_hl(*args, env_overrides=env_overrides)

    @mcp.tool(**_ann("run_strategy", "Run strategy"))
    def run_strategy(
        strategy: str,
        instrument: str = "ETH-PERP",
        tick: int = 10,
        max_ticks: Optional[int] = None,
        mock: bool = False,
        dry_run: bool = False,
        mainnet: bool = False,
        confirmed: bool = False,
        ctx: FastMCPContext = None,
    ) -> str:
        """Start autonomous trading with a strategy. WARNING: places real orders unless dry_run/mock.

        Args:
            strategy: Strategy name (e.g., engine_mm, avellaneda_mm, momentum_breakout)
            instrument: Trading instrument (default: ETH-PERP)
            tick: Seconds between ticks (default: 10)
            max_ticks: Stop after N ticks (None = run forever)
            mock: Use mock data instead of real API
            dry_run: Log decisions without placing orders
            mainnet: Use mainnet instead of testnet
            confirmed: Explicit confirmation for hosted gateway sessions that require it.
        """
        env_overrides = _request_env(ctx)
        effective_max_ticks = max_ticks if max_ticks is not None else _trusted_max_ticks(env_overrides)
        error = _context_limit_error(
            "run_strategy",
            env_overrides,
            mainnet=mainnet,
            max_ticks=effective_max_ticks,
            confirmed=confirmed,
            require_signing=not (mock or dry_run),
        )
        if error:
            return _json_error(error)

        args = ["run", strategy, "-i", instrument, "-t", str(tick)]
        if effective_max_ticks is not None:
            args.extend(["--max-ticks", str(effective_max_ticks)])
        if mock:
            args.append("--mock")
        if dry_run:
            args.append("--dry-run")
        if mainnet:
            args.append("--mainnet")
        return _run_hl(
            *args,
            timeout=max(60, (effective_max_ticks or 10) * tick + 30),
            env_overrides=env_overrides,
        )

    @mcp.tool(**_ann("btcswp_hedge_execute", "Execute BTCSWP hedge quote"))
    def btcswp_hedge_execute(
        primary_side: str,
        primary_notional_usd: float,
        primary_instrument: str = "BTC-PERP",
        hedge_goal: str = "auto",
        hedge_strength: float = 1.0,
        btcswp_mid: Optional[float] = None,
        current_funding_hr: Optional[float] = None,
        k_fixed_hr: Optional[float] = None,
        max_hedge_notional_usd: Optional[float] = None,
        dry_run: bool = False,
        mainnet: bool = False,
        confirmed: bool = False,
        ctx: FastMCPContext = None,
    ) -> str:
        """Quote and execute the accepted Pear BTCSWP hedge. WARNING: places an order unless dry_run."""
        env_overrides = _request_env(ctx)
        error = _context_limit_error(
            "btcswp_hedge_execute",
            env_overrides,
            mainnet=mainnet,
            confirmed=confirmed,
            require_signing=not dry_run,
        )
        if error:
            return _json_error(error)

        args = [
            "hedge", "execute-quote",
            "--primary-side", primary_side,
            "--primary-notional-usd", str(primary_notional_usd),
            "--primary-instrument", primary_instrument,
            "--hedge-goal", hedge_goal,
            "--hedge-strength", str(hedge_strength),
        ]
        if btcswp_mid is not None:
            args.extend(["--btcswp-mid", str(btcswp_mid)])
        if current_funding_hr is not None:
            args.extend(["--current-funding-hr", str(current_funding_hr)])
        if k_fixed_hr is not None:
            args.extend(["--k-fixed-hr", str(k_fixed_hr)])
        if max_hedge_notional_usd is not None:
            args.extend(["--max-hedge-notional-usd", str(max_hedge_notional_usd)])
        if dry_run:
            args.append("--dry-run")
        if mainnet:
            args.append("--mainnet")
        if confirmed or env_overrides:
            args.append("--yes")
        return _run_hl(*args, timeout=120, env_overrides=env_overrides)

    @mcp.tool(**_ann("radar_run", "Run radar scan"))
    def radar_run(mock: bool = False, ctx: FastMCPContext = None) -> str:
        """Run opportunity radar — screen HL perps for trading setups."""
        args = ["radar", "once"]
        if mock:
            args.append("--mock")
        return _run_hl(*args, timeout=60, env_overrides=_request_env(ctx))

    @mcp.tool(**_ann("apex_status", "APEX status"))
    def apex_status(ctx: FastMCPContext = None) -> str:
        """Get APEX orchestrator status (slots, positions, daily PnL)."""
        return _run_hl("apex", "status", env_overrides=_request_env(ctx))

    @mcp.tool(**_ann("apex_run", "Run APEX"))
    def apex_run(
        mock: bool = False,
        max_ticks: Optional[int] = None,
        preset: str = "default",
        mainnet: bool = False,
        confirmed: bool = False,
        ctx: FastMCPContext = None,
    ) -> str:
        """Start APEX multi-slot orchestrator. WARNING: places real orders unless mock.

        Args:
            mock: Use mock data
            max_ticks: Stop after N ticks
            preset: Strategy preset (default, conservative, aggressive)
            mainnet: Use mainnet
            confirmed: Explicit confirmation for hosted gateway sessions that require it.
        """
        env_overrides = _request_env(ctx)
        effective_max_ticks = max_ticks if max_ticks is not None else _trusted_max_ticks(env_overrides)
        error = _context_limit_error(
            "apex_run",
            env_overrides,
            mainnet=mainnet,
            max_ticks=effective_max_ticks,
            confirmed=confirmed,
            require_signing=not mock,
        )
        if error:
            return _json_error(error)

        args = ["apex", "run", "--preset", preset]
        if mock:
            args.append("--mock")
        if effective_max_ticks is not None:
            args.extend(["--max-ticks", str(effective_max_ticks)])
        if mainnet:
            args.append("--mainnet")
        return _run_hl(
            *args,
            timeout=max(120, (effective_max_ticks or 10) * 60 + 30),
            env_overrides=env_overrides,
        )

    @mcp.tool(**_ann("reflect_run", "Run reflect review"))
    def reflect_run(since: Optional[str] = None, ctx: FastMCPContext = None) -> str:
        """Run REFLECT performance review — analyze trades and generate report.

        Args:
            since: Start date for analysis (YYYY-MM-DD). Default: since last report.
        """
        args = ["reflect", "run"]
        if since:
            args.extend(["--since", since])
        return _run_hl(*args, env_overrides=_request_env(ctx))

    # ------------------------------------------------------------------
    # Safety tools — dead-man's switch + panic close
    # ------------------------------------------------------------------

    @mcp.tool(**_ann("schedule_cancel", "Schedule cancel (dead-man's switch)"))
    def schedule_cancel(
        seconds_from_now: int = 60,
        clear: bool = False,
        mainnet: bool = False,
        ctx: FastMCPContext = None,
    ) -> str:
        """Arm Hyperliquid's dead-man's switch: cancel ALL open orders this many
        seconds from now unless refreshed. Re-call to refresh; pass clear=True to
        remove it. Protects against a crashed agent leaving resting orders.

        Args:
            seconds_from_now: Seconds until auto-cancel (HL minimum ~5).
            clear: Clear any scheduled cancel instead of setting one.
            mainnet: Use mainnet instead of testnet.
        """
        env_overrides = _request_env(ctx)
        error = _context_limit_error(
            "schedule_cancel",
            env_overrides,
            mainnet=mainnet,
            confirmed=True,
            require_signing=True,
        )
        if error:
            return _json_error(error)
        args = ["schedule-cancel", str(seconds_from_now)]
        if clear:
            args.append("--clear")
        if mainnet:
            args.append("--mainnet")
        return _run_hl(*args, env_overrides=env_overrides)

    @mcp.tool(**_ann("emergency_close_all", "Emergency close all"))
    def emergency_close_all(
        confirm: bool = False,
        mainnet: bool = False,
        ctx: FastMCPContext = None,
    ) -> str:
        """EMERGENCY kill-switch: cancel ALL open orders and market-close ALL
        positions (reduce-only). Destructive — requires confirm=true.

        Args:
            confirm: Must be true to execute.
            mainnet: Use mainnet instead of testnet.
        """
        if not confirm:
            return json.dumps({"error": "confirmation required", "hint": "call again with confirm=true"})
        env_overrides = _request_env(ctx)
        error = _context_limit_error(
            "emergency_close_all",
            env_overrides,
            mainnet=mainnet,
            confirmed=True,
            require_signing=True,
        )
        if error:
            return _json_error(error)
        args = ["emergency-close", "--confirm"]
        if mainnet:
            args.append("--mainnet")
        return _run_hl(*args, timeout=120, env_overrides=env_overrides)

    # ------------------------------------------------------------------
    # Read tools — order lookup + funding
    # ------------------------------------------------------------------

    @mcp.tool(**_ann("order_status", "Order status"))
    def order_status(oid: str, mainnet: bool = False, ctx: FastMCPContext = None) -> str:
        """Look up the status of a single Hyperliquid order by its oid.

        Args:
            oid: The order id.
            mainnet: Use mainnet instead of testnet.
        """
        args = ["order-status", oid]
        if mainnet:
            args.append("--mainnet")
        return _run_hl(*args, env_overrides=_request_env(ctx))

    @mcp.tool(**_ann("funding_rates", "Funding rates"))
    def funding_rates(
        coin: Optional[str] = None,
        mainnet: bool = False,
        ctx: FastMCPContext = None,
    ) -> str:
        """Current (hourly) funding rates for all perps, or a single coin.

        Args:
            coin: Optional coin/instrument filter (e.g. ETH or ETH-PERP).
            mainnet: Use mainnet instead of testnet.
        """
        args = ["funding"]
        if coin:
            args.append(coin)
        if mainnet:
            args.append("--mainnet")
        return _run_hl(*args, env_overrides=_request_env(ctx))

    @mcp.tool(**_ann("btcswp_hedge_quote", "BTCSWP hedge quote"))
    def btcswp_hedge_quote(
        primary_side: str,
        primary_notional_usd: float,
        primary_instrument: str = "BTC-PERP",
        hedge_goal: str = "auto",
        hedge_strength: float = 1.0,
        btcswp_mid: Optional[float] = None,
        current_funding_hr: Optional[float] = None,
        k_fixed_hr: Optional[float] = None,
        max_hedge_notional_usd: Optional[float] = None,
    ) -> str:
        """Return a Pear-ready BTCSWP quote payload without signing or submitting."""
        from strategies.pear_btcswp_quote import quote_pear_btcswp_hedge

        quote = quote_pear_btcswp_hedge(
            primary_instrument=primary_instrument,
            primary_side=primary_side,
            primary_notional_usd=primary_notional_usd,
            hedge_goal=hedge_goal,
            hedge_strength=hedge_strength,
            btcswp_mid=btcswp_mid,
            current_funding_hr=current_funding_hr,
            k_fixed_hr=k_fixed_hr,
            max_hedge_notional_usd=max_hedge_notional_usd,
        )
        return json.dumps(quote.as_dict(), indent=2)

    # ------------------------------------------------------------------
    # Self-improvement tools — memory, journal, judge, obsidian
    # ------------------------------------------------------------------

    @mcp.tool(**_ann("agent_memory", "Read agent memory"))
    def agent_memory(query_type: str = "recent", limit: int = 20, event_type: Optional[str] = None) -> str:
        """Read agent memory — learnings, param changes, market observations.

        Args:
            query_type: "recent" for latest events, "playbook" for accumulated knowledge
            limit: Max events to return (default 20)
            event_type: Filter by type (param_change, reflect_review, notable_trade, judge_finding, session_start, session_end)
        """
        from modules.memory_guard import MemoryGuard

        guard = MemoryGuard()
        if query_type == "playbook":
            playbook = guard.load_playbook()
            return json.dumps(playbook.to_dict(), indent=2)
        else:
            events = guard.read_events(limit=limit, event_type=event_type)
            return json.dumps([e.to_dict() for e in events], indent=2)

    @mcp.tool(**_ann("trade_journal", "Read trade journal"))
    def trade_journal(date: Optional[str] = None, limit: int = 20) -> str:
        """Read trade journal — structured position records with entry/exit reasoning.

        Args:
            date: Filter by date (YYYY-MM-DD). Default: all dates.
            limit: Max entries to return (default 20)
        """
        from modules.journal_guard import JournalGuard

        guard = JournalGuard()
        entries = guard.read_entries(date=date, limit=limit)
        return json.dumps([e.to_dict() for e in entries], indent=2)

    @mcp.tool(**_ann("judge_report", "Judge report"))
    def judge_report() -> str:
        """Get latest Judge evaluation — signal quality, false positive rates, recommendations."""
        from modules.judge_guard import JudgeGuard

        guard = JudgeGuard()
        report = guard.read_latest_report()
        if not report:
            return json.dumps({"status": "no_reports", "message": "No judge reports yet. Run APEX to generate."})
        return json.dumps(report.to_dict(), indent=2)

    @mcp.tool(**_ann("obsidian_context", "Obsidian context"))
    def obsidian_context() -> str:
        """Read trading context from Obsidian vault — watchlists, market theses, risk preferences."""
        from modules.obsidian_reader import ObsidianReader

        reader = ObsidianReader()
        if not reader.available:
            return json.dumps({"status": "unavailable", "message": "Obsidian vault not found at ~/obsidian-vault"})
        ctx = reader.read_trading_context()
        return json.dumps(ctx.to_dict(), indent=2)

    return mcp
