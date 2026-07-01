"""PulseSignalWebhook — fires outbound HTTP POSTs on qualifying Pulse signals.

D7 from the HOUSE unified-demo plan. PulseEngine stays stateless; this module
is the side-effect sink that the runner invokes after each scan.

Default target is ECC's `/api/lab/launch` endpoint, which spawns an
autoresearch agent on the asset. Per-signal idempotency via an in-memory
dedupe window (asset + tier + scan_time bucket).

Failures are swallowed and logged — webhook delivery is best-effort and must
never block the scan loop.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Iterable, Set

from modules.pulse_config import PulseConfig
from modules.pulse_state import PulseSignal


log = logging.getLogger("pulse_webhook")


class PulseSignalWebhook:
    """Best-effort outbound webhook for Pulse signals.

    Threading: each fire goes on a daemon thread so the scan loop never blocks.
    Dedup: a 5-minute window keyed on `(asset, signal_type)` prevents the same
    signal from re-launching autoresearch on every tick.
    """

    def __init__(self, config: PulseConfig, dedup_window_sec: float = 300.0):
        self.config = config
        self.dedup_window_sec = dedup_window_sec
        self._recent: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.config.webhook_url)

    def fire_for_signals(self, signals: Iterable[PulseSignal]) -> int:
        """POST each qualifying signal to the configured webhook. Returns
        the count of signals dispatched (after gating + dedup)."""
        if not self.enabled:
            return 0

        gate = self.config.webhook_min_confidence
        sent: Set[tuple[str, str]] = set()
        now = time.time()

        for sig in signals:
            if sig.confidence < gate:
                continue
            key = (sig.asset, sig.signal_type)
            with self._lock:
                last = self._recent.get(key, 0.0)
                if now - last < self.dedup_window_sec:
                    continue
                self._recent[key] = now
            sent.add(key)
            self._dispatch_async(sig)

        # Compact dedupe map opportunistically.
        if len(self._recent) > 500:
            cutoff = now - self.dedup_window_sec
            with self._lock:
                self._recent = {
                    k: v for k, v in self._recent.items() if v >= cutoff
                }

        return len(sent)

    # ---------- internals ----------

    def _dispatch_async(self, sig: PulseSignal) -> None:
        t = threading.Thread(
            target=self._dispatch_sync,
            args=(sig,),
            daemon=True,
            name=f"pulse-webhook-{sig.asset}",
        )
        t.start()

    def _dispatch_sync(self, sig: PulseSignal) -> None:
        url = self.config.webhook_url
        payload = {
            "source": "pulse",
            "asset": sig.asset,
            "signal_type": sig.signal_type,
            "direction": sig.direction,
            "confidence": sig.confidence,
            "oi_delta_pct": sig.oi_delta_pct,
            "volume_surge_ratio": sig.volume_surge_ratio,
            "funding_shift": sig.funding_shift,
            "is_erratic": sig.is_erratic,
            "fired_at_ms": int(time.time() * 1000),
        }
        body = json.dumps(payload).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.config.webhook_auth_token:
            headers["Authorization"] = f"Bearer {self.config.webhook_auth_token}"

        req = urllib.request.Request(
            url, data=body, headers=headers, method="POST",
        )

        try:
            with urllib.request.urlopen(
                req, timeout=self.config.webhook_timeout_sec,
            ) as resp:
                status = resp.status
                if 200 <= status < 300:
                    log.info(
                        "webhook ok asset=%s tier=%s status=%d",
                        sig.asset, sig.signal_type, status,
                    )
                else:
                    log.warning(
                        "webhook non-2xx asset=%s tier=%s status=%d",
                        sig.asset, sig.signal_type, status,
                    )
        except urllib.error.URLError as err:
            log.warning(
                "webhook failed asset=%s tier=%s err=%s",
                sig.asset, sig.signal_type, err,
            )
        except Exception as err:  # noqa: BLE001 — best-effort sink
            log.warning(
                "webhook unexpected error asset=%s tier=%s err=%s",
                sig.asset, sig.signal_type, err,
            )
