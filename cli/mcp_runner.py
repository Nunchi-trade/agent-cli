"""Hosted MCP JSON-RPC dispatch for Railway entrypoint."""
from __future__ import annotations

import json
import logging
import os
import sys
from types import SimpleNamespace
from typing import Any, Optional

MCP_PATHS = {"/mcp", "/mcp/trading"}
log = logging.getLogger(__name__)
MCP_READ_TOOLS = {
    "strategies",
    "builder_status",
    "wallet_list",
    "setup_check",
    "account",
    "status",
    "apex_status",
    "radar_run",
    "reflect_run",
    "agent_memory",
    "trade_journal",
    "judge_report",
    "obsidian_context",
    "order_status",
    "funding_rates",
}
MCP_WRITE_TOOLS = {
    "wallet_auto",
    "trade",
    "run_strategy",
    "apex_run",
    "schedule_cancel",
    "emergency_close_all",
}
MCP_ALL_TOOLS = sorted(MCP_READ_TOOLS | MCP_WRITE_TOOLS)


def handle_mcp_json_rpc(raw_body: bytes, headers: Any) -> tuple[int, dict[str, Any]]:
    try:
        rpc = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
    except json.JSONDecodeError:
        return 400, _json_rpc_error(None, -32700, "parse error")

    if not isinstance(rpc, dict):
        return 400, _json_rpc_error(None, -32600, "invalid request")

    request_id = rpc.get("id")
    method = rpc.get("method")
    params = rpc.get("params") if isinstance(rpc.get("params"), dict) else {}

    try:
        if method == "initialize":
            return 200, _json_rpc_result(request_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "nunchi-agent-cli-runner", "version": "1.0.0"},
            })
        if method == "tools/list":
            return 200, _json_rpc_result(request_id, {
                "tools": [{"name": name, "description": _tool_description(name)} for name in MCP_ALL_TOOLS],
            })
        if method == "tools/call":
            name = str(params.get("name", ""))
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            return 200, _json_rpc_result(request_id, {"content": [{"type": "text", "text": call_mcp_tool(name, arguments, headers)}]})
        return 200, _json_rpc_error(request_id, -32601, f"method not found: {method}")
    except Exception as exc:
        log.exception("MCP JSON-RPC error")
        return 200, _json_rpc_error(request_id, -32000, str(exc))


def call_mcp_tool(name: str, arguments: dict[str, Any], headers: Any) -> str:
    from cli.mcp_server import (
        _context_limit_error,
        _json_error,
        _run_hl,
        _trusted_context_env_overrides,
        _trusted_max_ticks,
    )

    env_overrides = _trusted_context_env_overrides(_context_from_headers(headers))

    if name == "strategies":
        return _run_hl("strategies", env_overrides=env_overrides)
    if name == "builder_status":
        return _run_hl("builder", "status", env_overrides=env_overrides)
    if name == "wallet_list":
        return _run_hl("wallet", "list", env_overrides=env_overrides)
    if name == "setup_check":
        return _setup_check_text(env_overrides)
    if name == "account":
        cmd = ["account"]
        if _bool_arg(arguments, "mainnet"):
            cmd.append("--mainnet")
        return _run_hl(*cmd, env_overrides=env_overrides)
    if name == "status":
        return _run_hl("status", env_overrides=env_overrides)
    if name == "apex_status":
        return _run_hl("apex", "status", env_overrides=env_overrides)
    if name == "radar_run":
        cmd = ["radar", "once"]
        if _bool_arg(arguments, "mock"):
            cmd.append("--mock")
        return _run_hl(*cmd, timeout=60, env_overrides=env_overrides)
    if name == "reflect_run":
        cmd = ["reflect", "run"]
        since = _str_arg(arguments, "since")
        if since:
            cmd.extend(["--since", since])
        return _run_hl(*cmd, env_overrides=env_overrides)
    if name == "order_status":
        oid = _str_arg(arguments, "oid")
        if not oid:
            return _json_error("order_status requires oid")
        cmd = ["order-status", oid]
        if _bool_arg(arguments, "mainnet"):
            cmd.append("--mainnet")
        return _run_hl(*cmd, env_overrides=env_overrides)
    if name == "funding_rates":
        cmd = ["funding"]
        coin = _str_arg(arguments, "coin")
        if coin:
            cmd.append(coin)
        if _bool_arg(arguments, "mainnet"):
            cmd.append("--mainnet")
        return _run_hl(*cmd, env_overrides=env_overrides)
    if name == "agent_memory":
        return _agent_memory_text(arguments)
    if name == "trade_journal":
        return _trade_journal_text(arguments)
    if name == "judge_report":
        return _judge_report_text()
    if name == "obsidian_context":
        return _obsidian_context_text()

    if name == "trade":
        instrument = _str_arg(arguments, "instrument") or "ETH-PERP"
        side = _str_arg(arguments, "side")
        size = _float_arg(arguments, "size")
        mainnet = _bool_arg(arguments, "mainnet")
        confirmed = _bool_arg(arguments, "confirmed")
        if not side or size is None:
            return _json_error("trade requires side and size")
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
        cmd = ["trade", instrument, side, str(size)]
        if mainnet:
            cmd.append("--mainnet")
        if confirmed or env_overrides:
            cmd.append("--yes")
        return _run_hl(*cmd, env_overrides=env_overrides)

    if name == "run_strategy":
        strategy = _str_arg(arguments, "strategy")
        if not strategy:
            return _json_error("run_strategy requires strategy")
        instrument = _str_arg(arguments, "instrument") or "ETH-PERP"
        tick = _int_arg(arguments, "tick") or 10
        max_ticks = _int_arg(arguments, "max_ticks")
        mock = _bool_arg(arguments, "mock")
        dry_run = _bool_arg(arguments, "dry_run")
        mainnet = _bool_arg(arguments, "mainnet")
        confirmed = _bool_arg(arguments, "confirmed")
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
        cmd = ["run", strategy, "-i", instrument, "-t", str(tick)]
        if effective_max_ticks is not None:
            cmd.extend(["--max-ticks", str(effective_max_ticks)])
        if mock:
            cmd.append("--mock")
        if dry_run:
            cmd.append("--dry-run")
        if mainnet:
            cmd.append("--mainnet")
        return _run_hl(*cmd, timeout=max(60, (effective_max_ticks or 10) * tick + 30), env_overrides=env_overrides)

    if name == "apex_run":
        mock = _bool_arg(arguments, "mock")
        max_ticks = _int_arg(arguments, "max_ticks")
        preset = _str_arg(arguments, "preset") or "default"
        mainnet = _bool_arg(arguments, "mainnet")
        confirmed = _bool_arg(arguments, "confirmed")
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
        cmd = ["apex", "run", "--preset", preset]
        if mock:
            cmd.append("--mock")
        if effective_max_ticks is not None:
            cmd.extend(["--max-ticks", str(effective_max_ticks)])
        if mainnet:
            cmd.append("--mainnet")
        return _run_hl(*cmd, timeout=max(120, (effective_max_ticks or 10) * 60 + 30), env_overrides=env_overrides)

    if name == "wallet_auto":
        return _json_error("wallet_auto is disabled on the hosted keyless runner")
    if name in {"schedule_cancel", "emergency_close_all"}:
        return _json_error(f"{name} is not exposed by hosted trading")
    return _json_error(f"unknown tool: {name}")


def _context_from_headers(headers: Any) -> Any:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    request = SimpleNamespace(headers=normalized)
    return SimpleNamespace(request_context=SimpleNamespace(request=request, meta={}))


def _setup_check_text(env_overrides: dict[str, str]) -> str:
    from cli.config import TradingConfig
    from cli.keystore import list_keystores

    issues: list[str] = []
    ok_items: list[str] = []
    try:
        import hyperliquid  # noqa: F401
        ok_items.append("hyperliquid-python-sdk installed")
    except ImportError:
        issues.append("hyperliquid-python-sdk not installed")

    has_env_key = bool(env_overrides.get("HL_PRIVATE_KEY") or os.environ.get("HL_PRIVATE_KEY"))
    has_web_auth = bool(env_overrides.get("NUNCHI_WEB_AUTH_PAIR_TOKEN")) and bool(env_overrides.get("NUNCHI_WEB_AUTH_ADDRESS"))
    keystores = list_keystores()
    if has_env_key:
        ok_items.append("HL_PRIVATE_KEY set")
    elif has_web_auth:
        ok_items.append("web-auth pairing context provided")
    elif keystores:
        ok_items.append(f"Keystore found ({len(keystores)} keys)")
    else:
        issues.append("No signing context: pass trusted web-auth pairing context for write tools")

    testnet = os.environ.get("HL_TESTNET", "true").lower()
    ok_items.append(f"Network: {'testnet' if testnet == 'true' else 'mainnet'}")
    bcfg = TradingConfig().get_builder_config()
    ok_items.append(f"Builder fee: {bcfg.fee_bps} bps" if bcfg.enabled else "Builder fee: not configured")
    return json.dumps({"ok": ok_items, "issues": issues, "passed": len(issues) == 0}, indent=2)


def _agent_memory_text(arguments: dict[str, Any]) -> str:
    from modules.memory_guard import MemoryGuard

    guard = MemoryGuard()
    if _str_arg(arguments, "query_type") == "playbook":
        return json.dumps(guard.load_playbook().to_dict(), indent=2)
    events = guard.read_events(limit=_int_arg(arguments, "limit") or 20, event_type=_str_arg(arguments, "event_type"))
    return json.dumps([event.to_dict() for event in events], indent=2)


def _trade_journal_text(arguments: dict[str, Any]) -> str:
    from modules.journal_guard import JournalGuard

    entries = JournalGuard().read_entries(date=_str_arg(arguments, "date"), limit=_int_arg(arguments, "limit") or 20)
    return json.dumps([entry.to_dict() for entry in entries], indent=2)


def _judge_report_text() -> str:
    from modules.judge_guard import JudgeGuard

    report = JudgeGuard().read_latest_report()
    if not report:
        return json.dumps({"status": "no_reports", "message": "No judge reports yet. Run APEX to generate."})
    return json.dumps(report.to_dict(), indent=2)


def _obsidian_context_text() -> str:
    from modules.obsidian_reader import ObsidianReader

    reader = ObsidianReader()
    if not reader.available:
        return json.dumps({"status": "unavailable", "message": "Obsidian vault not found at ~/obsidian-vault"})
    return json.dumps(reader.read_trading_context().to_dict(), indent=2)


def _json_rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_description(name: str) -> str:
    return "Hosted agent-cli trading tool"


def _str_arg(arguments: dict[str, Any], key: str) -> Optional[str]:
    value = arguments.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_arg(arguments: dict[str, Any], key: str) -> bool:
    return str(arguments.get(key, "")).strip().lower() in {"1", "true", "yes", "on"}


def _float_arg(arguments: dict[str, Any], key: str) -> Optional[float]:
    value = arguments.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_arg(arguments: dict[str, Any], key: str) -> Optional[int]:
    value = arguments.get(key)
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
