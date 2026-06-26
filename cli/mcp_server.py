"""MCP server for agent-cli — exposes trading tools via Model Context Protocol.

Fast tools (account, strategies, builder, wallet, setup) call Python directly.
Long-running tools (run_strategy, apex_run, radar, reflect) use subprocess.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional


def _run_hl(*args: str, timeout: int = 30) -> str:
    """Run an hl CLI command via subprocess and return stdout."""
    cmd = [sys.executable, "-m", "cli.main", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    output = result.stdout.strip()
    if result.returncode != 0 and result.stderr:
        output = output + "\n" + result.stderr.strip() if output else result.stderr.strip()
    return output or "(no output)"


def create_mcp_server():
    """Create and configure the FastMCP server."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "yex-trader",
        instructions="Autonomous Hyperliquid trading CLI — 14 strategies, APEX orchestrator, REFLECT reviews, BTCSWP funding hedge proposals.",
    )

    # ------------------------------------------------------------------
    # Fast tools — call Python directly (no subprocess overhead)
    # ------------------------------------------------------------------

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
    def wallet_list() -> str:
        """List saved encrypted keystores."""
        from cli.keystore import list_keystores

        keystores = list_keystores()
        return json.dumps(keystores, indent=2) if keystores else "No keystores found."

    @mcp.tool()
    def wallet_auto(save_env: bool = True) -> str:
        """Create a new wallet non-interactively (agent-friendly).

        Args:
            save_env: Save credentials to ~/.hl-agent/env for auto-detection (default: True)
        """
        import secrets
        from pathlib import Path
        from eth_account import Account
        from cli.keystore import create_keystore

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

    @mcp.tool()
    def setup_check() -> str:
        """Validate environment — SDK, keys, network, builder fee."""
        import os
        from cli.keystore import list_keystores
        from cli.config import TradingConfig

        issues = []
        ok_items = []
        warnings = []

        # SDK
        try:
            import hyperliquid  # noqa: F401
            ok_items.append("hyperliquid-python-sdk installed")
        except ImportError:
            issues.append("hyperliquid-python-sdk not installed")

        # Key
        has_env_key = bool(os.environ.get("HL_PRIVATE_KEY"))
        keystores = list_keystores()
        from cli.web_auth import get_stored_pairing
        pairing = get_stored_pairing()
        if has_env_key:
            ok_items.append("HL_PRIVATE_KEY set")
            if pairing is None:
                warnings.append(
                    "Raw-key mode active. Prefer hl pair connect or hosted Nunchi Auth for MCP/agent use."
                )
        elif keystores:
            ok_items.append(f"Keystore found ({len(keystores)} keys)")
        else:
            issues.append("No private key: set HL_PRIVATE_KEY or run wallet_auto")
        if pairing is not None:
            ok_items.append(f"Paired wallet active ({pairing.selected_or_master_address})")
        else:
            warnings.append("No paired wallet found. Run hl pair connect to enable browser-approved signing.")

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
            "warnings": warnings,
            "issues": issues,
            "passed": len(issues) == 0,
        }, indent=2)

    @mcp.tool()
    def funding_hedge_propose(
        asset: str = "BTC",
        perp_side: str = "long",
        perp_notional_usd: float = 100_000.0,
        funding_apr: Optional[float] = None,
        funding_rate_8h: Optional[float] = None,
        vol_multiplier: float = 15.0,
    ) -> str:
        """Propose a read-only BTCSWP funding-rate hedge.

        Args:
            asset: Underlying perp exposure. BTC is deployed today.
            perp_side: Perp exposure side — "long" or "short".
            perp_notional_usd: Absolute perp notional in USD.
            funding_apr: Annualized funding APR. Accepts 0.42 or 42 for 42%.
            funding_rate_8h: 8h funding rate as a decimal, used if funding_apr is omitted.
            vol_multiplier: BTCSWP hedge multiplier. Default 15 means 1/15 notional.
        """
        from modules.funding_hedge import propose_funding_hedge

        try:
            proposal = propose_funding_hedge(
                asset=asset,
                perp_side=perp_side,
                perp_notional_usd=perp_notional_usd,
                funding_apr=funding_apr,
                funding_rate_8h=funding_rate_8h,
                vol_multiplier=vol_multiplier,
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, indent=2)
        return json.dumps(proposal.to_dict(), indent=2)

    @mcp.tool()
    def funding_hedge_backtest(
        csv_path: str,
        asset: str = "BTC",
        perp_side: str = "long",
        perp_notional_usd: float = 100_000.0,
        vol_multiplier: float = 15.0,
    ) -> str:
        """Backtest BTCSWP funding hedge cashflows from a local CSV.

        The CSV must include funding_rate_8h, perp_funding_rate_8h, funding_rate,
        or rate. It may also include hedge_rate_8h, btcswp_rate_8h, or
        btcswp_funding_rate_8h for realized hedge residuals.

        Args:
            csv_path: Local CSV path readable by the MCP server process.
            asset: Underlying perp exposure. BTC is deployed today.
            perp_side: Perp exposure side — "long" or "short".
            perp_notional_usd: Absolute perp notional in USD.
            vol_multiplier: BTCSWP hedge multiplier. Default 15 means 1/15 notional.
        """
        from modules.funding_hedge import backtest_funding_hedge_csv

        try:
            backtest = backtest_funding_hedge_csv(
                csv_path=csv_path,
                asset=asset,
                perp_side=perp_side,
                perp_notional_usd=perp_notional_usd,
                vol_multiplier=vol_multiplier,
            )
        except (OSError, ValueError) as exc:
            return json.dumps({"error": str(exc)}, indent=2)
        return json.dumps(backtest.to_dict(), indent=2)

    @mcp.tool()
    def account(mainnet: bool = False) -> str:
        """Get Hyperliquid account state (balances, positions)."""
        # Account requires live HL connection — use subprocess for isolation
        args = ["account"]
        if mainnet:
            args.append("--mainnet")
        return _run_hl(*args)

    @mcp.tool()
    def status() -> str:
        """Show current positions, PnL, and risk state."""
        return _run_hl("status")

    # ------------------------------------------------------------------
    # Action tools — subprocess (side effects, long-running)
    # ------------------------------------------------------------------

    @mcp.tool()
    def trade(instrument: str, side: str, size: float) -> str:
        """Place a single manual order.

        Args:
            instrument: Trading pair (e.g., ETH-PERP, BTC-PERP, VXX-USDYP)
            side: Order side — "buy" or "sell"
            size: Order size in contracts
        """
        return _run_hl("trade", instrument, side, str(size))

    @mcp.tool()
    def run_strategy(
        strategy: str,
        instrument: str = "ETH-PERP",
        tick: int = 10,
        max_ticks: Optional[int] = None,
        mock: bool = False,
        dry_run: bool = False,
        mainnet: bool = False,
    ) -> str:
        """Start autonomous trading with a strategy.

        Args:
            strategy: Strategy name (e.g., engine_mm, avellaneda_mm, momentum_breakout)
            instrument: Trading instrument (default: ETH-PERP)
            tick: Seconds between ticks (default: 10)
            max_ticks: Stop after N ticks (None = run forever)
            mock: Use mock data instead of real API
            dry_run: Log decisions without placing orders
            mainnet: Use mainnet instead of testnet
        """
        args = ["run", strategy, "-i", instrument, "-t", str(tick)]
        if max_ticks is not None:
            args.extend(["--max-ticks", str(max_ticks)])
        if mock:
            args.append("--mock")
        if dry_run:
            args.append("--dry-run")
        if mainnet:
            args.append("--mainnet")
        return _run_hl(*args, timeout=max(60, (max_ticks or 10) * tick + 30))

    @mcp.tool()
    def radar_run(mock: bool = False) -> str:
        """Run opportunity radar — screen HL perps for trading setups."""
        args = ["radar", "once"]
        if mock:
            args.append("--mock")
        return _run_hl(*args, timeout=60)

    @mcp.tool()
    def apex_status() -> str:
        """Get APEX orchestrator status (slots, positions, daily PnL)."""
        return _run_hl("apex", "status")

    @mcp.tool()
    def apex_run(
        mock: bool = False,
        max_ticks: Optional[int] = None,
        preset: str = "default",
        mainnet: bool = False,
    ) -> str:
        """Start APEX multi-slot orchestrator.

        Args:
            mock: Use mock data
            max_ticks: Stop after N ticks
            preset: Strategy preset (default, conservative, aggressive)
            mainnet: Use mainnet
        """
        args = ["apex", "run", "--preset", preset]
        if mock:
            args.append("--mock")
        if max_ticks is not None:
            args.extend(["--max-ticks", str(max_ticks)])
        if mainnet:
            args.append("--mainnet")
        return _run_hl(*args, timeout=max(120, (max_ticks or 10) * 60 + 30))

    @mcp.tool()
    def reflect_run(since: Optional[str] = None) -> str:
        """Run REFLECT performance review — analyze trades and generate report.

        Args:
            since: Start date for analysis (YYYY-MM-DD). Default: since last report.
        """
        args = ["reflect", "run"]
        if since:
            args.extend(["--since", since])
        return _run_hl(*args)

    # ------------------------------------------------------------------
    # Self-improvement tools — memory, journal, judge, obsidian
    # ------------------------------------------------------------------

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
    def judge_report() -> str:
        """Get latest Judge evaluation — signal quality, false positive rates, recommendations."""
        from modules.judge_guard import JudgeGuard

        guard = JudgeGuard()
        report = guard.read_latest_report()
        if not report:
            return json.dumps({"status": "no_reports", "message": "No judge reports yet. Run APEX to generate."})
        return json.dumps(report.to_dict(), indent=2)

    @mcp.tool()
    def obsidian_context() -> str:
        """Read trading context from Obsidian vault — watchlists, market theses, risk preferences."""
        from modules.obsidian_reader import ObsidianReader

        reader = ObsidianReader()
        if not reader.available:
            return json.dumps({"status": "unavailable", "message": "Obsidian vault not found at ~/obsidian-vault"})
        ctx = reader.read_trading_context()
        return json.dumps(ctx.to_dict(), indent=2)

    return mcp
