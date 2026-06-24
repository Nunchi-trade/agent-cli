#!/usr/bin/env python3
"""Railway entrypoint — health check server + strategy runner.

Starts a lightweight HTTP health server (required by Railway), then launches
the configured trading mode (apex, strategy, or mcp) as a subprocess.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from types import SimpleNamespace
from threading import Thread
from typing import Any, Optional

import logging
import re

log = logging.getLogger("entrypoint")
START_TIME = time.time()
CHILD_PROC: subprocess.Popen | None = None
MAX_BODY_SIZE = 1_048_576  # 1MB max POST body
AUTH_TOKEN = os.environ.get("API_AUTH_TOKEN")
MCP_PATHS = {"/mcp", "/mcp/trading"}
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
    "btcswp_hedge_quote",
    "pair_trade_quote",
}
MCP_WRITE_TOOLS = {
    "wallet_auto",
    "trade",
    "run_strategy",
    "apex_run",
    "schedule_cancel",
    "emergency_close_all",
    "btcswp_hedge_execute",
    "pair_trade_execute",
    "pair_trade_close",
}
MCP_ALL_TOOLS = sorted(MCP_READ_TOOLS | MCP_WRITE_TOOLS)

# Regex to redact hex private keys (0x + 64 hex chars)
_SECRET_RE = re.compile(r'0x[a-fA-F0-9]{64}')


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal health check handler for Railway."""

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "ok",
                "mode": os.environ.get("RUN_MODE", "apex"),
                "uptime_s": int(time.time() - START_TIME),
                "pid": CHILD_PROC.pid if CHILD_PROC else None,
                "alive": runner_alive(),
            })
            self._json_response(body)

        elif self.path == "/status":
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "cli.main", "apex", "status"],
                    capture_output=True, text=True, timeout=10,
                )
                output = result.stdout.strip() or result.stderr.strip() or "(no output)"
            except Exception as e:
                output = str(e)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.write(output)

        elif self.path == "/api/status":
            data_dir = os.environ.get("DATA_DIR", "/data")
            try:
                from cli.api.status_reader import read_status
                body = json.dumps(read_status(data_dir))
            except Exception as e:
                body = json.dumps({"status": "error", "error": str(e)})
            self._json_response(body, cors=True)

        elif self.path == "/api/strategies":
            try:
                from cli.api.status_reader import read_strategies
                body = json.dumps(read_strategies())
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._json_response(body, cors=True)

        elif self.path == "/api/feed":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._send_cors_headers()
            self.end_headers()
            data_dir = os.environ.get("DATA_DIR", "/data")
            try:
                from cli.api.status_reader import read_status
                last_tick = -1
                while True:
                    status = read_status(data_dir)
                    tick = status.get("tick_count", 0)
                    if tick != last_tick:
                        last_tick = tick
                        self.wfile.write(f"data: {json.dumps(status)}\n\n".encode())
                        self.wfile.flush()
                    time.sleep(2)
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif self.path.startswith("/api/trades"):
            data_dir = os.environ.get("DATA_DIR", "/data")
            try:
                from urllib.parse import urlparse, parse_qs
                from cli.api.status_reader import read_trades
                qs = parse_qs(urlparse(self.path).query)
                limit = int(qs.get("limit", ["50"])[0])
                body = json.dumps(read_trades(data_dir, limit=limit))
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._json_response(body, cors=True)

        elif self.path == "/api/reflect":
            data_dir = os.environ.get("DATA_DIR", "/data")
            try:
                from cli.api.status_reader import read_reflect
                body = json.dumps(read_reflect(data_dir))
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._json_response(body, cors=True)

        elif self.path == "/metrics":
            data_dir = os.environ.get("DATA_DIR", "/data")
            metrics_path = Path(data_dir) / "apex" / "metrics.json"
            try:
                if metrics_path.exists():
                    with open(metrics_path) as f:
                        body = f.read()
                else:
                    body = json.dumps({"status": "no_metrics_yet"})
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._json_response(body)

        elif self.path == "/api/scanner":
            data_dir = os.environ.get("DATA_DIR", "/data")
            try:
                from cli.api.status_reader import read_radar
                body = json.dumps(read_radar(data_dir))
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._json_response(body, cors=True)

        elif self.path.startswith("/api/journal"):
            data_dir = os.environ.get("DATA_DIR", "/data")
            try:
                from urllib.parse import urlparse, parse_qs
                from cli.api.status_reader import read_journal
                qs = parse_qs(urlparse(self.path).query)
                limit = int(qs.get("limit", ["50"])[0])
                body = json.dumps(read_journal(data_dir, limit=limit))
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._json_response(body, cors=True)

        else:
            self.send_response(404)
            self.end_headers()

    def _check_auth(self) -> bool:
        """Check bearer token auth if API_AUTH_TOKEN is configured."""
        if not AUTH_TOKEN:
            return True  # no auth configured
        auth_header = self.headers.get("Authorization", "")
        if auth_header == f"Bearer {AUTH_TOKEN}":
            return True
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.write(json.dumps({"error": "unauthorized"}))
        return False

    def _read_body(self) -> bytes | None:
        """Read POST body with size limit. Returns None if too large."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_BODY_SIZE:
            self.send_response(413)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.write(json.dumps({"error": "request body too large", "max_bytes": MAX_BODY_SIZE}))
            return None
        return self.rfile.read(content_length)

    def do_POST(self):
        if self.path in MCP_PATHS:
            body = self._read_body()
            if body is None:
                return
            status, response = handle_mcp_json_rpc(body, self.headers)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.write(json.dumps(response))

        elif self.path == "/api/skill/install":
            try:
                from cli.api.status_reader import read_strategies
                data = read_strategies()
                count = len(data.get("strategies", {}))
                self._json_response(json.dumps({"installed": True, "strategies": count, "tools": 13}), cors=True)
            except Exception as e:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.write(json.dumps({"installed": False, "error": str(e)}))

        elif self.path == "/api/configure":
            if not self._check_auth():
                return
            body = self._read_body()
            if body is None:
                return
            try:
                config = json.loads(body)
                data_dir = os.environ.get("DATA_DIR", "/data")
                from cli.api.status_reader import write_config_override
                write_config_override(data_dir, config)
                self._json_response(json.dumps({"status": "ok", "applied_at": "next_tick"}), cors=True)
            except Exception as e:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.write(json.dumps({"error": str(e)}))

        elif self.path == "/api/pause":
            if not self._check_auth():
                return
            if CHILD_PROC and CHILD_PROC.poll() is None:
                os.kill(CHILD_PROC.pid, signal.SIGSTOP)
            self._json_response(json.dumps({"status": "paused"}), cors=True)

        elif self.path == "/api/resume":
            if not self._check_auth():
                return
            if CHILD_PROC and CHILD_PROC.poll() is None:
                os.kill(CHILD_PROC.pid, signal.SIGCONT)
            self._json_response(json.dumps({"status": "resumed"}), cors=True)

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def write(self, body: str):
        self.wfile.write(body.encode())

    def _json_response(self, body: str, cors: bool = False):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if cors:
            self._send_cors_headers()
        self.end_headers()
        self.write(body)

    def _send_cors_headers(self):
        origin = os.environ.get("CORS_ORIGIN", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def log_message(self, format, *args):
        pass  # suppress access logs


def build_command() -> list[str]:
    """Build the CLI command from environment variables."""
    mode = os.environ.get("RUN_MODE", "apex").lower()
    py = [sys.executable, "-m", "cli.main"]

    if mode in ("apex", "wolf"):
        cmd = py + ["apex", "run"]
        preset = os.environ.get("APEX_PRESET")
        if preset:
            cmd += ["--preset", preset]
        budget = os.environ.get("APEX_BUDGET")
        if budget:
            cmd += ["--budget", budget]
        slots = os.environ.get("APEX_SLOTS")
        if slots:
            cmd += ["--slots", slots]
        leverage = os.environ.get("APEX_LEVERAGE")
        if leverage:
            cmd += ["--leverage", leverage]
        tick = os.environ.get("TICK_INTERVAL")
        if tick:
            cmd += ["--tick", tick]
        # Restrict the agent's pulse/radar scans and entries to a set of
        # markets. Critical for PR-3 dedicated-wallet mode where the agent
        # is funded on a HIP-3 dex (e.g. yex) and must NOT scan universal
        # HL perps that it has no collateral on. Without this, agents
        # scan 207+ universal markets and produce zero entries even though
        # they hold $1000 in their yex clearinghouse.
        allowed = os.environ.get("ALLOWED_INSTRUMENTS")
        if allowed:
            cmd += ["--markets", allowed]
        strategy_names = os.environ.get("STRATEGY_NAMES")
        if strategy_names:
            cmd += ["--strategy-names", strategy_names]
        base_dir = os.environ.get("DATA_DIR", "/data")
        cmd += ["--data-dir", f"{base_dir}/apex"]
        if os.environ.get("HL_TESTNET", "true").lower() == "false":
            cmd.append("--mainnet")
        return cmd

    elif mode == "strategy":
        strategy = os.environ.get("STRATEGY", "engine_mm")
        instrument = os.environ.get("INSTRUMENT", "ETH-PERP")
        tick = os.environ.get("TICK_INTERVAL", "10")
        cmd = py + ["run", strategy, "-i", instrument, "-t", tick]
        if os.environ.get("HL_TESTNET", "true").lower() == "false":
            cmd.append("--mainnet")
        return cmd

    elif mode == "mcp":
        return py + ["mcp", "serve", "--transport", "sse"]

    else:
        log.error("Unknown RUN_MODE: %s. Use apex, wolf, strategy, or mcp.", mode)
        sys.exit(1)


def runner_alive() -> bool:
    if CHILD_PROC is not None:
        return CHILD_PROC.poll() is None
    return os.environ.get("RUN_MODE", "apex").lower() == "mcp"


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
    if name == "btcswp_hedge_quote":
        from strategies.pear_btcswp_quote import quote_pear_btcswp_hedge

        primary_side = _str_arg(arguments, "primary_side")
        primary_notional_usd = _float_arg(arguments, "primary_notional_usd")
        if not primary_side or primary_notional_usd is None:
            return _json_error("btcswp_hedge_quote requires primary_side and primary_notional_usd")
        quote = quote_pear_btcswp_hedge(
            primary_instrument=_str_arg(arguments, "primary_instrument") or "BTC-PERP",
            primary_side=primary_side,
            primary_notional_usd=primary_notional_usd,
            hedge_goal=_str_arg(arguments, "hedge_goal") or "auto",
            hedge_strength=_float_arg(arguments, "hedge_strength") or 1.0,
            btcswp_mid=_float_arg(arguments, "btcswp_mid"),
            current_funding_hr=_float_arg(arguments, "current_funding_hr"),
            k_fixed_hr=_float_arg(arguments, "k_fixed_hr"),
            max_hedge_notional_usd=_float_arg(arguments, "max_hedge_notional_usd"),
        )
        return json.dumps(quote.as_dict(), indent=2)
    if name == "pair_trade_quote":
        from strategies.pear_pair_trade import build_btc_btcswp_pair_plan

        primary_side = _str_arg(arguments, "primary_side")
        primary_notional_usd = _float_arg(arguments, "primary_notional_usd")
        btc_mid = _float_arg(arguments, "btc_mid")
        btcswp_mid = _float_arg(arguments, "btcswp_mid")
        if not primary_side or primary_notional_usd is None or btc_mid is None or btcswp_mid is None:
            return _json_error("pair_trade_quote requires primary_side, primary_notional_usd, btc_mid, and btcswp_mid")
        plan = build_btc_btcswp_pair_plan(
            primary_side=primary_side,
            primary_notional_usd=primary_notional_usd,
            btc_mid=btc_mid,
            btcswp_mid=btcswp_mid,
            hedge_goal=_str_arg(arguments, "hedge_goal") or "auto",
            hedge_strength=_float_arg(arguments, "hedge_strength") or 1.0,
            slippage=_float_arg(arguments, "slippage") or 0.01,
            leverage=_float_arg(arguments, "leverage") or 1.0,
        )
        return json.dumps(plan.as_dict(), indent=2)
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

    if name == "btcswp_hedge_execute":
        primary_side = _str_arg(arguments, "primary_side")
        primary_notional_usd = _float_arg(arguments, "primary_notional_usd")
        if not primary_side or primary_notional_usd is None:
            return _json_error("btcswp_hedge_execute requires primary_side and primary_notional_usd")
        dry_run = _bool_arg(arguments, "dry_run")
        mainnet = _bool_arg(arguments, "mainnet")
        confirmed = _bool_arg(arguments, "confirmed")
        error = _context_limit_error(
            "btcswp_hedge_execute",
            env_overrides,
            mainnet=mainnet,
            confirmed=confirmed,
            require_signing=not dry_run,
        )
        if error:
            return _json_error(error)
        cmd = [
            "hedge", "execute-quote",
            "--primary-side", primary_side,
            "--primary-notional-usd", str(primary_notional_usd),
            "--primary-instrument", _str_arg(arguments, "primary_instrument") or "BTC-PERP",
            "--hedge-goal", _str_arg(arguments, "hedge_goal") or "auto",
            "--hedge-strength", str(_float_arg(arguments, "hedge_strength") or 1.0),
        ]
        btcswp_mid = _float_arg(arguments, "btcswp_mid")
        if btcswp_mid is not None:
            cmd.extend(["--btcswp-mid", str(btcswp_mid)])
        current_funding_hr = _float_arg(arguments, "current_funding_hr")
        if current_funding_hr is not None:
            cmd.extend(["--current-funding-hr", str(current_funding_hr)])
        k_fixed_hr = _float_arg(arguments, "k_fixed_hr")
        if k_fixed_hr is not None:
            cmd.extend(["--k-fixed-hr", str(k_fixed_hr)])
        max_hedge_notional_usd = _float_arg(arguments, "max_hedge_notional_usd")
        if max_hedge_notional_usd is not None:
            cmd.extend(["--max-hedge-notional-usd", str(max_hedge_notional_usd)])
        if dry_run:
            cmd.append("--dry-run")
        if mainnet:
            cmd.append("--mainnet")
        if confirmed or env_overrides:
            cmd.append("--yes")
        return _run_hl(*cmd, timeout=120, env_overrides=env_overrides)

    if name == "pair_trade_execute":
        primary_side = _str_arg(arguments, "primary_side")
        primary_notional_usd = _float_arg(arguments, "primary_notional_usd")
        if not primary_side or primary_notional_usd is None:
            return _json_error("pair_trade_execute requires primary_side and primary_notional_usd")
        dry_run = _bool_arg(arguments, "dry_run")
        mainnet = _bool_arg(arguments, "mainnet")
        confirmed = _bool_arg(arguments, "confirmed")
        error = _context_limit_error(
            "pair_trade_execute",
            env_overrides,
            mainnet=mainnet,
            confirmed=confirmed,
            require_signing=not dry_run,
        )
        if error:
            return _json_error(error)
        cmd = [
            "pair", "execute",
            "--primary-side", primary_side,
            "--primary-notional-usd", str(primary_notional_usd),
            "--hedge-goal", _str_arg(arguments, "hedge_goal") or "auto",
            "--hedge-strength", str(_float_arg(arguments, "hedge_strength") or 1.0),
            "--slippage", str(_float_arg(arguments, "slippage") or 0.01),
            "--leverage", str(_float_arg(arguments, "leverage") or 1.0),
            "--venue", _str_arg(arguments, "venue") or "pear",
        ]
        btc_mid = _float_arg(arguments, "btc_mid")
        if btc_mid is not None:
            cmd.extend(["--btc-mid", str(btc_mid)])
        btcswp_mid = _float_arg(arguments, "btcswp_mid")
        if btcswp_mid is not None:
            cmd.extend(["--btcswp-mid", str(btcswp_mid)])
        if dry_run:
            cmd.append("--dry-run")
        if mainnet:
            cmd.append("--mainnet")
        if confirmed or env_overrides:
            cmd.append("--yes")
        return _run_hl(*cmd, timeout=120, env_overrides=env_overrides)

    if name == "pair_trade_close":
        dry_run = _bool_arg(arguments, "dry_run")
        mainnet = _bool_arg(arguments, "mainnet")
        confirmed = _bool_arg(arguments, "confirmed")
        error = _context_limit_error(
            "pair_trade_close",
            env_overrides,
            mainnet=mainnet,
            confirmed=confirmed,
            require_signing=not dry_run,
        )
        if error:
            return _json_error(error)
        cmd = ["pair", "close"]
        pair_position_id = _str_arg(arguments, "pair_position_id")
        if pair_position_id:
            cmd.append(pair_position_id)
        if dry_run:
            cmd.append("--dry-run")
        if mainnet:
            cmd.append("--mainnet")
        if confirmed or env_overrides:
            cmd.append("--yes")
        return _run_hl(*cmd, timeout=120, env_overrides=env_overrides)

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


def shutdown(signum, frame):
    """Forward shutdown signal to child process."""
    global CHILD_PROC
    if CHILD_PROC and CHILD_PROC.poll() is None:
        log.info("Received signal %d, forwarding to child (pid=%d)", signum, CHILD_PROC.pid)
        CHILD_PROC.send_signal(signal.SIGTERM)
        try:
            CHILD_PROC.wait(timeout=15)
        except subprocess.TimeoutExpired:
            CHILD_PROC.kill()
    sys.exit(0)


def main():
    global CHILD_PROC

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Competition mode: force testnet regardless of other config
    if os.environ.get("COMPETITION_MODE", "").lower() == "true":
        os.environ["HL_TESTNET"] = "true"
        log.info("COMPETITION_MODE active — forcing testnet")

    port = int(os.environ.get("PORT", "8080"))

    # Start health check server in background (threaded to handle SSE + concurrent requests)
    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadedHTTPServer(("0.0.0.0", port), HealthHandler)
    health_thread = Thread(target=server.serve_forever, daemon=True)
    health_thread.start()
    log.info("Health server listening on :%d", port)

    # Register signal handlers
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Auto-approve builder fee (idempotent, best-effort)
    # Check both HL_PRIVATE_KEY (direct) and keystore auth paths
    has_key = bool(os.environ.get("HL_PRIVATE_KEY"))
    has_keystore = bool(os.environ.get("HL_KEYSTORE_PASSWORD")) or Path(
        os.path.expanduser("~/.hl-agent/env")).exists()
    if (has_key or has_keystore) and os.environ.get("BUILDER_ADDRESS"):
        try:
            mainnet_flag = ["--mainnet"] if os.environ.get("HL_TESTNET", "true").lower() == "false" else []
            subprocess.run(
                [sys.executable, "-m", "cli.main", "builder", "approve", "--yes"] + mainnet_flag,
                capture_output=True, timeout=30,
            )
            log.info("Builder fee approval sent")
        except Exception:
            pass  # best-effort

    mode = os.environ.get("RUN_MODE", "apex")
    if mode.lower() == "mcp":
        log.info("Starting mcp mode: HTTP JSON-RPC wrapper active on /mcp/trading")
        while True:
            time.sleep(3600)

    # Build and run main command
    cmd = build_command()
    safe_cmd = _SECRET_RE.sub("0x[REDACTED]", ' '.join(cmd))
    log.info("Starting %s mode: %s", mode, safe_cmd)

    CHILD_PROC = subprocess.Popen(cmd)

    # Wait for child to finish (or be killed)
    rc = CHILD_PROC.wait()
    log.info("Process exited with code %d", rc)
    sys.exit(rc)


if __name__ == "__main__":
    main()
