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
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

START_TIME = time.time()
CHILD_PROC: subprocess.Popen | None = None


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal health check handler for Railway."""

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "ok",
                "mode": os.environ.get("RUN_MODE", "apex"),
                "uptime_s": int(time.time() - START_TIME),
                "pid": CHILD_PROC.pid if CHILD_PROC else None,
                "alive": CHILD_PROC.poll() is None if CHILD_PROC else False,
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
            self._cors_headers()
            self._json_response(body)

        elif self.path == "/api/strategies":
            try:
                from cli.api.status_reader import read_strategies
                body = json.dumps(read_strategies())
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._cors_headers()
            self._json_response(body)

        elif self.path == "/api/feed":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors_headers(headers_only=True)
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
            self._cors_headers()
            self._json_response(body)

        elif self.path == "/api/reflect":
            data_dir = os.environ.get("DATA_DIR", "/data")
            try:
                from cli.api.status_reader import read_reflect
                body = json.dumps(read_reflect(data_dir))
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._cors_headers()
            self._json_response(body)

        elif self.path == "/api/scanner":
            data_dir = os.environ.get("DATA_DIR", "/data")
            try:
                from cli.api.status_reader import read_radar
                body = json.dumps(read_radar(data_dir))
            except Exception as e:
                body = json.dumps({"error": str(e)})
            self._cors_headers()
            self._json_response(body)

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
            self._cors_headers()
            self._json_response(body)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/skill/install":
            try:
                from cli.api.status_reader import read_strategies
                data = read_strategies()
                count = len(data.get("strategies", {}))
                self._cors_headers()
                self._json_response(json.dumps({"installed": True, "strategies": count, "tools": 13}))
            except Exception as e:
                self.send_response(500)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.write(json.dumps({"installed": False, "error": str(e)}))

        elif self.path == "/api/configure":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                config = json.loads(body)
                data_dir = os.environ.get("DATA_DIR", "/data")
                from cli.api.status_reader import write_config_override
                write_config_override(data_dir, config)
                self._cors_headers()
                self._json_response(json.dumps({"status": "ok", "applied_at": "next_tick"}))
            except Exception as e:
                self.send_response(400)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.write(json.dumps({"error": str(e)}))

        elif self.path == "/api/pause":
            if CHILD_PROC and CHILD_PROC.poll() is None:
                os.kill(CHILD_PROC.pid, signal.SIGSTOP)
            self._cors_headers()
            self._json_response(json.dumps({"status": "paused"}))

        elif self.path == "/api/resume":
            if CHILD_PROC and CHILD_PROC.poll() is None:
                os.kill(CHILD_PROC.pid, signal.SIGCONT)
            self._cors_headers()
            self._json_response(json.dumps({"status": "resumed"}))

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def write(self, body: str):
        self.wfile.write(body.encode())

    def _json_response(self, body: str):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.write(body)

    def _cors_headers(self, headers_only: bool = False):
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
        data_dir = os.environ.get("DATA_DIR", "/data/apex")
        cmd += ["--data-dir", data_dir]
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
        print(f"Unknown RUN_MODE: {mode}. Use apex, wolf, strategy, or mcp.", file=sys.stderr)
        sys.exit(1)


def shutdown(signum, frame):
    """Forward shutdown signal to child process."""
    global CHILD_PROC
    if CHILD_PROC and CHILD_PROC.poll() is None:
        print(f"[entrypoint] Received signal {signum}, forwarding to child (pid={CHILD_PROC.pid})")
        CHILD_PROC.send_signal(signal.SIGTERM)
        try:
            CHILD_PROC.wait(timeout=15)
        except subprocess.TimeoutExpired:
            CHILD_PROC.kill()
    sys.exit(0)


def main():
    global CHILD_PROC

    port = int(os.environ.get("PORT", "8080"))

    # Start health check server in background
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    health_thread = Thread(target=server.serve_forever, daemon=True)
    health_thread.start()
    print(f"[entrypoint] Health server listening on :{port}")

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
            print("[entrypoint] Builder fee approval sent")
        except Exception:
            pass  # best-effort

    # Build and run main command
    cmd = build_command()
    mode = os.environ.get("RUN_MODE", "apex")
    print(f"[entrypoint] Starting {mode} mode: {' '.join(cmd)}")

    CHILD_PROC = subprocess.Popen(cmd)

    # Wait for child to finish (or be killed)
    rc = CHILD_PROC.wait()
    print(f"[entrypoint] Process exited with code {rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
