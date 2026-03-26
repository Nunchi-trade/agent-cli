"""Event notification system — pushes engine events to Telegram with throttling."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from telegram import Bot

from tg_bot.formatters import fill_card, shutdown_card, status_card

log = logging.getLogger("telegram.notifier")


class Notifier:
    """Reads events from engine queue and sends Telegram messages with throttling."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        event_queue: asyncio.Queue,
        pnl_interval_s: int = 60,
        tick_summary_interval_s: int = 300,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.event_queue = event_queue
        self.pnl_interval_s = pnl_interval_s
        self.tick_summary_interval_s = tick_summary_interval_s
        self._last_pnl_sent = 0.0
        self._last_tick_summary = 0.0
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Start the notifier as a background task."""
        self._task = asyncio.create_task(self._run())
        log.info("Notifier started (pnl_interval=%ds, tick_summary=%ds)",
                 self.pnl_interval_s, self.tick_summary_interval_s)

    def stop(self) -> None:
        """Stop the notifier."""
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        """Main loop: read events from queue, format, send."""
        while True:
            try:
                event = await self.event_queue.get()
                await self._handle_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Notifier error: %s", e, exc_info=True)

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        event_type = event.get("type")

        if event_type == "fill":
            await self._send_fill(event)
        elif event_type == "tick":
            await self._maybe_send_tick_summary(event)
        elif event_type == "shutdown":
            await self._send_shutdown(event)
        elif event_type == "error":
            await self._send_error(event)
        elif event_type == "risk_alert":
            await self._send_risk_alert(event)

    async def _send_fill(self, event: Dict[str, Any]) -> None:
        """Always send fill notifications."""
        text = fill_card(
            side=event["side"],
            quantity=event["quantity"],
            price=event["price"],
            instrument=event["instrument"],
            strategy=event["strategy"],
            tick=event["tick"],
        )
        await self._send(text)

    async def _maybe_send_tick_summary(self, event: Dict[str, Any]) -> None:
        """Send PnL updates at throttled intervals."""
        now = time.time()

        # Always-send conditions: safe mode or reduce-only transitions
        if event.get("safe_mode") or event.get("reduce_only"):
            await self._send(
                f"RISK ALERT: {'Safe mode' if event.get('safe_mode') else 'Reduce-only'} active\n"
                f"Strategy: {event['strategy']} | Tick: {event['tick_count']}"
            )
            return

        # Throttled PnL update
        if now - self._last_tick_summary < self.tick_summary_interval_s:
            return

        self._last_tick_summary = now
        sign = lambda v: f"+{v:.2f}" if v >= 0 else f"{v:.2f}"
        total = event.get("upnl", 0) + event.get("rpnl", 0)
        text = (
            f"T{event['tick_count']} | {event['instrument']} mid={event['mid_price']:.4f}\n"
            f"Pos: {sign(event['pos_qty'])} | PnL: ${sign(total)}"
        )
        await self._send(text)

    async def _send_shutdown(self, event: Dict[str, Any]) -> None:
        """Always send shutdown summary."""
        text = shutdown_card(
            tick_count=event["tick_count"],
            total_placed=event["total_placed"],
            total_filled=event["total_filled"],
            total_pnl=event["total_pnl"],
            elapsed_s=event["elapsed_s"],
        )
        await self._send(text)

    async def _send_error(self, event: Dict[str, Any]) -> None:
        """Always send error notifications."""
        await self._send(f"ERROR: {event.get('message', 'Unknown error')}")

    async def _send_risk_alert(self, event: Dict[str, Any]) -> None:
        """Always send risk alerts."""
        await self._send(f"RISK ALERT: {event.get('message', 'Risk event triggered')}")

    async def _send(self, text: str) -> None:
        """Send a message to the configured chat."""
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text)
        except Exception as e:
            log.error("Failed to send Telegram message: %s", e)
