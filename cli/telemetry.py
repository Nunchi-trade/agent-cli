"""Agent telemetry — registration + heartbeat for Nunchi tracking.

Fire-and-forget HTTP calls on daemon threads. Never blocks trading.
Opt-out via NUNCHI_TELEMETRY=false environment variable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.request
from threading import Thread

log = logging.getLogger("telemetry")

TELEMETRY_BASE = os.environ.get("NUNCHI_TELEMETRY_URL", "")
HEARTBEAT_INTERVAL_TICKS = 10
TIMEOUT_S = 5


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version("yex-trader")
    except Exception:
        pass
    try:
        import re
        pyproject = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(pyproject) as f:
            for line in f:
                m = re.match(r'version\s*=\s*"(.+?)"', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return "unknown"


def _detect_deploy_mode() -> str:
    if os.environ.get("RAILWAY_SERVICE_NAME"):
        return "railway"
    if os.environ.get("OPENCLAW_STATE_DIR"):
        return "openclaw"
    return "local"


class TelemetryClient:
    """Non-blocking telemetry client. All methods are safe to call unconditionally."""

    def __init__(
        self,
        wallet_address: str,
        strategy_name: str,
        network: str,
        deploy_mode: str,
        version: str,
    ):
        self.wallet_address = wallet_address
        self.strategy_name = strategy_name
        self.network = network
        self.deploy_mode = deploy_mode
        self.version = version
        self.instance_id = hashlib.sha256(
            f"{wallet_address.lower()}:{strategy_name}".encode()
        ).hexdigest()[:16]

    @property
    def enabled(self) -> bool:
        if os.environ.get("NUNCHI_TELEMETRY", "true").lower() == "false":
            return False
        return bool(TELEMETRY_BASE)

    def register(self) -> None:
        if not self.enabled:
            return
        payload = {
            "instance_id": self.instance_id,
            "wallet_address": self.wallet_address,
            "strategy": self.strategy_name,
            "version": self.version,
            "network": self.network,
            "deploy_mode": self.deploy_mode,
            "registered_at": int(time.time()),
        }
        self._post("register", payload)

    def heartbeat(self, tick_count: int, uptime_s: float, active_positions: int) -> None:
        if not self.enabled:
            return
        payload = {
            "instance_id": self.instance_id,
            "tick_count": tick_count,
            "uptime_s": round(uptime_s, 1),
            "active_positions": active_positions,
            "heartbeat_at": int(time.time()),
        }
        self._post("heartbeat", payload)

    def trade(self, journal_payload: dict) -> None:
        """Post a closed-trade journal row to the central attribution sink.

        Phase 1.1 of the profitability roadmap. Fire-and-forget — never
        blocks the trading loop. The caller is responsible for computing
        gross_pnl, fees_estimate, and net_pnl before calling this; we don't
        re-derive them server-side because the agent has access to the
        actual entry size that the leaderboard cannot see.
        """
        if not self.enabled:
            return
        # Stamp identity onto every row so the central DB can group by agent
        payload = dict(journal_payload)
        payload.setdefault("instance_id", self.instance_id)
        payload.setdefault("wallet_address", self.wallet_address)
        self._post("trade", payload)

    def should_heartbeat(self, tick_count: int) -> bool:
        return tick_count > 0 and tick_count % HEARTBEAT_INTERVAL_TICKS == 0

    def _post(self, endpoint: str, payload: dict) -> None:
        def _send():
            try:
                url = f"{TELEMETRY_BASE}/{endpoint}"
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=TIMEOUT_S):
                    pass
                log.debug("Telemetry %s sent: %s", endpoint, self.instance_id)
            except Exception as e:
                log.debug("Telemetry %s failed (non-fatal): %s", endpoint, e)

        try:
            Thread(target=_send, daemon=True).start()
        except Exception:
            pass  # thread spawn failed — never block trading


def create_telemetry(wallet_address: str, strategy_name: str) -> TelemetryClient:
    """Factory that reads config from environment."""
    network = "testnet" if os.environ.get("HL_TESTNET", "true").lower() == "true" else "mainnet"
    return TelemetryClient(
        wallet_address=wallet_address,
        strategy_name=strategy_name,
        network=network,
        deploy_mode=_detect_deploy_mode(),
        version=_get_version(),
    )
