"""Wallet creation and onboarding flow."""
from __future__ import annotations

import logging
import os
import secrets

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from tg_bot.auth import authorized
from tg_bot.formatters import (
    wallet_created_card,
    wallet_keyboard,
    welcome_card,
)

log = logging.getLogger("telegram.start")

# Conversation states
CHOOSE_ACTION, IMPORT_KEY = range(2)


@authorized
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /start — check wallet, show welcome."""
    from cli.keystore import list_keystores

    keystores = list_keystores()
    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    if keystores:
        address = keystores[0]["address"]
        # Try to get balance
        balance = 0.0
        try:
            balance = await _get_balance(address, network)
        except Exception:
            pass
        await update.message.reply_text(welcome_card(True, address, balance))
        return ConversationHandler.END

    await update.message.reply_text(
        welcome_card(False),
        reply_markup=wallet_keyboard(),
    )
    return CHOOSE_ACTION


async def wallet_create_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Create a new encrypted wallet."""
    query = update.callback_query
    await query.answer()

    from cli.keystore import create_keystore, ENV_FILE
    from eth_account import Account

    # Generate random key + password
    account = Account.create()
    private_key = account.key.hex()
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    password = secrets.token_urlsafe(24)

    # Save to keystore
    ks_path = create_keystore(private_key, password)

    # Persist password for auto-unlock
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
    lines = [l for l in lines if not l.startswith("HL_KEYSTORE_PASSWORD=")]
    lines.append(f"HL_KEYSTORE_PASSWORD={password}")
    ENV_FILE.write_text("\n".join(lines) + "\n")

    address = account.address
    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    log.info("Created wallet %s (keystore: %s)", address, ks_path)
    await query.edit_message_text(wallet_created_card(address, network))
    return ConversationHandler.END


async def wallet_import_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to send private key."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Send your private key (hex format with 0x prefix).\n"
        "The message will be deleted immediately for security."
    )
    return IMPORT_KEY


async def receive_private_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and encrypt a private key. Delete the user's message immediately."""
    import secrets
    from cli.keystore import create_keystore, ENV_FILE

    # Delete the message containing the private key immediately
    try:
        await update.message.delete()
    except Exception:
        log.warning("Could not delete message containing private key")

    private_key = update.message.text.strip()
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    # Validate
    try:
        from eth_account import Account
        account = Account.from_key(private_key)
    except Exception:
        await update.message.reply_text(
            "Invalid private key. Must be a 64-character hex string (with or without 0x prefix).\n"
            "Try again or use /start to create a new wallet."
        )
        return ConversationHandler.END

    password = secrets.token_urlsafe(24)
    ks_path = create_keystore(private_key, password)

    # Persist password
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
    lines = [l for l in lines if not l.startswith("HL_KEYSTORE_PASSWORD=")]
    lines.append(f"HL_KEYSTORE_PASSWORD={password}")
    ENV_FILE.write_text("\n".join(lines) + "\n")

    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    log.info("Imported wallet %s (keystore: %s)", account.address, ks_path)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=wallet_created_card(account.address, network),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def _get_balance(address: str, network: str) -> float:
    """Get account balance from HL. Returns 0 on failure."""
    try:
        from common.credentials import resolve_private_key
        from parent.hl_proxy import HLProxy
        from cli.hl_adapter import DirectHLProxy

        private_key = resolve_private_key(venue="hl")
        testnet = network != "mainnet"
        raw_hl = HLProxy(private_key=private_key, testnet=testnet)
        hl = DirectHLProxy(raw_hl)
        account = hl.get_account_state()
        if "crossMarginSummary" in account:
            return float(account["crossMarginSummary"].get("accountValue", 0))
        if "marginSummary" in account:
            return float(account["marginSummary"].get("accountValue", 0))
    except Exception as e:
        log.debug("Balance check failed: %s", e)
    return 0.0


def build_start_handler() -> ConversationHandler:
    """Build the /start conversation handler."""
    return ConversationHandler(
        entry_points=[CommandHandler("start", start_cmd)],
        states={
            CHOOSE_ACTION: [
                CallbackQueryHandler(wallet_create_callback, pattern="^wallet_create$"),
                CallbackQueryHandler(wallet_import_callback, pattern="^wallet_import$"),
            ],
            IMPORT_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_private_key),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
