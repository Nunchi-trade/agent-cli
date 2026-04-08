"""Telegram bot application — entry point and handler registration."""
from __future__ import annotations

import asyncio
import logging

from telegram.ext import Application

from tg_bot.auth import load_persisted_chat_id
from tg_bot.config import TelegramBotConfig
from tg_bot.engine_bridge import EngineBridge
from tg_bot.handlers.apex import register_apex_handlers
from tg_bot.handlers.control import register_control_handlers
from tg_bot.handlers.start import build_start_handler
from tg_bot.handlers.strategy import build_deploy_handler
from tg_bot.notifier import Notifier

log = logging.getLogger("telegram.bot")


async def post_init(application: Application) -> None:
    """Called after bot is initialized but before polling starts."""
    config: TelegramBotConfig = application.bot_data["config"]
    event_queue: asyncio.Queue = application.bot_data["event_queue"]

    # Auto-detect chat ID for notifications
    chat_id = None
    if config.allowed_chat_ids:
        chat_id = config.allowed_chat_ids[0]
    else:
        persisted = load_persisted_chat_id()
        if persisted:
            config.allowed_chat_ids.append(persisted)
            chat_id = persisted

    if chat_id:
        notifier = Notifier(
            bot=application.bot,
            chat_id=chat_id,
            event_queue=event_queue,
            pnl_interval_s=config.notification_interval_s,
            tick_summary_interval_s=config.tick_summary_interval_s,
        )
        notifier.start()
        application.bot_data["notifier"] = notifier
        log.info("Notifier started for chat_id=%d", chat_id)
    else:
        log.info("No chat ID configured — notifier will start after /start")


def run_bot(config: TelegramBotConfig) -> None:
    """Build and run the Telegram bot (blocking)."""
    if not config.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required. Set it in your environment.")

    log.info("Starting Telegram bot (network=%s)", config.default_network)

    # Create event queue for engine -> bot communication
    event_queue = asyncio.Queue()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Build application
    application = (
        Application.builder()
        .token(config.bot_token)
        .post_init(post_init)
        .build()
    )

    # Store shared state
    application.bot_data["config"] = config
    application.bot_data["event_queue"] = event_queue
    application.bot_data["allowed_chat_ids"] = list(config.allowed_chat_ids)

    # Create engine bridge
    bridge = EngineBridge(event_queue=event_queue, loop=loop)
    application.bot_data["engine_bridge"] = bridge

    # Register handlers (order matters — ConversationHandlers first)
    application.add_handler(build_start_handler())
    application.add_handler(build_deploy_handler())
    register_control_handlers(application)
    register_apex_handlers(application)

    log.info("Bot ready — polling for updates")
    application.run_polling(drop_pending_updates=True)
