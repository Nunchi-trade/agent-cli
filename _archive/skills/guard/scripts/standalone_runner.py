"""Standalone Guard runner — monitors a position and closes when triggered.

Fetches prices from Hyperliquid, runs Guard checks, and closes positions
directly via aggressive IOC orders. No agent intervention needed.
"""
from __future__ import annotations

import skills._bootstrap  # noqa: F401 — auto-setup sys.path

import logging
import signal
import time

from modules.guard_bridge import GuardBridge
from modules.trailing_stop import GuardAction

log = logging.getLogger("guard_runner")


class StandaloneGuardRunner:
    """Tick loop: fetch price -> Guard check -> close if triggered."""

    def __init__(
        self,
        hl,  # DirectHLProxy | DirectMockProxy
        guard: GuardBridge,
        instrument: str,
        tick_interval: float = 5.0,
        dry_run: bool = False,
    ):
        self.hl = hl
        self.guard = guard
        self.instrument = instrument
        self.tick_interval = tick_interval
        self.dry_run = dry_run
        self._running = False

    def run(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        log.info(
            "Guard Runner started: %s instrument=%s tick=%.1fs dry=%s",
            self.guard.state.position_id,
            self.instrument,
            self.tick_interval,
            self.dry_run,
        )

        while self._running and self.guard.is_active:
            try:
                self._tick()
            except Exception as e:
                log.error("Guard tick error: %s", e, exc_info=True)

            if self._running and self.guard.is_active:
                time.sleep(self.tick_interval)

        reason = "closed" if self.guard.state.closed else "stopped"
        log.info("Guard Runner finished: %s (%s)", self.guard.state.position_id, reason)

    def _tick(self) -> None:
        snapshot = self.hl.get_snapshot(self.instrument)
        if snapshot.mid_price <= 0:
            log.warning("No market data for %s, skipping", self.instrument)
            return

        result = self.guard.check(snapshot.mid_price)

        if result.action == GuardAction.CLOSE:
            log.warning("GUARD CLOSE: %s", result.reason)
            closed = self._close_position(snapshot.mid_price)
            if closed:
                self.guard.mark_closed(snapshot.mid_price, result.reason)
            # If not closed, guard stays active and retries next tick

    def _close_position(self, current_price: float) -> bool:
        """Close position via aggressive IOC order. Returns True if filled."""
        state = self.guard.state
        close_side = "sell" if state.direction == "long" else "buy"
        size = state.position_size

        # Aggressive pricing: 0.5% slippage to ensure fill
        if close_side == "sell":
            price = round(current_price * 0.995, 6)
        else:
            price = round(current_price * 1.005, 6)

        if self.dry_run:
            log.info(
                "[DRY RUN] Would close: %s %.6f %s @ %.4f",
                close_side, size, self.instrument, price,
            )
            return True

        fill = self.hl.place_order(
            instrument=self.instrument,
            side=close_side,
            size=size,
            price=price,
            tif="Ioc",
        )

        if fill:
            log.info(
                "Position closed: %s %s %s @ %s",
                fill.side, fill.quantity, fill.instrument, fill.price,
            )
            return True

        log.warning("Close order did not fill — will retry next tick")
        return False

    def _stop(self, signum, frame):
        log.info("Stop signal received")
        self._running = False
