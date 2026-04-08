"""Single-user authentication for Telegram bot."""
from __future__ import annotations

import logging
from functools import wraps
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

log = logging.getLogger("telegram.auth")

# File to persist auto-detected chat ID
_CHAT_ID_FILE = None


def _get_chat_id_file():
    from pathlib import Path
    return Path.home() / ".hl-agent" / "telegram_chat_id"


def load_persisted_chat_id() -> int | None:
    """Load previously persisted chat ID from disk."""
    path = _get_chat_id_file()
    if path.exists():
        try:
            return int(path.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def persist_chat_id(chat_id: int) -> None:
    """Save chat ID to disk for persistence across restarts."""
    path = _get_chat_id_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(chat_id))


def authorized(func: Callable) -> Callable:
    """Decorator that restricts handler to allowed chat IDs.

    On first interaction, if no chat IDs are configured, auto-registers the first user.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        allowed: list = context.bot_data.get("allowed_chat_ids", [])

        if not allowed:
            # Auto-register first user
            allowed.append(chat_id)
            context.bot_data["allowed_chat_ids"] = allowed
            persist_chat_id(chat_id)
            log.info("Auto-registered chat ID %d as authorized user", chat_id)

        if chat_id not in allowed:
            log.warning("Unauthorized access attempt from chat_id=%d", chat_id)
            await update.message.reply_text("Unauthorized. This bot is private.")
            return

        return await func(update, context)

    return wrapper
