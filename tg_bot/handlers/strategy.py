"""Strategy selection and agent deployment flow."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from tg_bot.auth import authorized
from tg_bot.formatters import (
    confirm_keyboard,
    deploy_confirm_card,
    instrument_keyboard,
    mainnet_confirm_keyboard,
    preset_keyboard,
    strategy_info_card,
    strategy_keyboard,
)

log = logging.getLogger("telegram.strategy")

# Conversation states
CHOOSE_STRATEGY, CHOOSE_INSTRUMENT, CHOOSE_PRESET, CONFIRM, MAINNET_CONFIRM = range(5)

# Risk presets
PRESETS = {
    "conservative": {
        "max_position_qty": 2.0,
        "max_notional_usd": 5000.0,
        "max_order_size": 1.0,
        "max_leverage": 2.0,
        "tvl": 10000.0,
    },
    "default": {
        "max_position_qty": 10.0,
        "max_notional_usd": 25000.0,
        "max_order_size": 5.0,
        "max_leverage": 3.0,
        "tvl": 100000.0,
    },
    "aggressive": {
        "max_position_qty": 25.0,
        "max_notional_usd": 100000.0,
        "max_order_size": 10.0,
        "max_leverage": 5.0,
        "tvl": 250000.0,
    },
}


def _get_registry():
    from cli.strategy_registry import STRATEGY_REGISTRY
    return STRATEGY_REGISTRY


@authorized
async def deploy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /deploy — select strategy."""
    # Check if agent is already running
    bridge = context.bot_data.get("engine_bridge")
    if bridge and bridge.is_running():
        await update.message.reply_text(
            "An agent is already running. Use /stop first, then /deploy again."
        )
        return ConversationHandler.END

    # Check wallet exists
    from cli.keystore import list_keystores
    if not list_keystores():
        await update.message.reply_text("No wallet found. Use /start first to create one.")
        return ConversationHandler.END

    registry = _get_registry()
    await update.message.reply_text(
        "Choose a strategy:",
        reply_markup=strategy_keyboard(registry, page=0),
    )
    return CHOOSE_STRATEGY


async def strategy_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle strategy pagination."""
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("_")[-1])
    registry = _get_registry()
    await query.edit_message_reply_markup(
        reply_markup=strategy_keyboard(registry, page=page),
    )
    return CHOOSE_STRATEGY


async def strategy_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle strategy selection."""
    query = update.callback_query
    await query.answer()
    strategy_name = query.data.replace("strat_", "")

    registry = _get_registry()
    if strategy_name not in registry:
        await query.edit_message_text(f"Unknown strategy: {strategy_name}")
        return ConversationHandler.END

    context.user_data["deploy_strategy"] = strategy_name

    # Show strategy info + instrument picker
    info = registry[strategy_name]
    text = strategy_info_card(strategy_name, info) + "\n\nChoose instrument:"
    await query.edit_message_text(text, reply_markup=instrument_keyboard())
    return CHOOSE_INSTRUMENT


async def instrument_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle instrument selection."""
    query = update.callback_query
    await query.answer()
    instrument = query.data.replace("inst_", "")
    context.user_data["deploy_instrument"] = instrument

    await query.edit_message_text(
        f"Strategy: {context.user_data['deploy_strategy']}\n"
        f"Instrument: {instrument}\n\n"
        "Choose risk preset:",
        reply_markup=preset_keyboard(),
    )
    return CHOOSE_PRESET


async def preset_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle preset selection, show confirmation."""
    query = update.callback_query
    await query.answer()
    preset_name = query.data.replace("preset_", "")
    context.user_data["deploy_preset"] = preset_name
    risk_params = PRESETS.get(preset_name, PRESETS["default"])
    context.user_data["deploy_risk_params"] = risk_params

    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    text = deploy_confirm_card(
        strategy=context.user_data["deploy_strategy"],
        instrument=context.user_data["deploy_instrument"],
        preset=preset_name,
        network=network,
        risk_params=risk_params,
    )
    await query.edit_message_text(text, reply_markup=confirm_keyboard(mainnet=network == "mainnet"))
    return CONFIRM


async def confirm_deploy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm deployment — or gate to mainnet double-confirm."""
    query = update.callback_query
    await query.answer()

    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    if network == "mainnet" and config and config.mainnet_confirmation:
        await query.edit_message_text(
            "WARNING: You are about to deploy on MAINNET with REAL funds.\n"
            "Are you absolutely sure?",
            reply_markup=mainnet_confirm_keyboard(),
        )
        return MAINNET_CONFIRM

    return await _do_deploy(query, context)


async def mainnet_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mainnet double confirmation."""
    query = update.callback_query
    await query.answer()

    if query.data == "mainnet_confirm_yes":
        return await _do_deploy(query, context)
    else:
        await query.edit_message_text("Deployment cancelled.")
        return ConversationHandler.END


async def confirm_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel deployment."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Deployment cancelled.")
    return ConversationHandler.END


async def _do_deploy(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Actually start the trading engine."""
    strategy = context.user_data["deploy_strategy"]
    instrument = context.user_data["deploy_instrument"]
    preset = context.user_data["deploy_preset"]
    risk_params = context.user_data["deploy_risk_params"]

    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    bridge = context.bot_data.get("engine_bridge")
    if not bridge:
        await query.edit_message_text("Engine bridge not initialized. Contact admin.")
        return ConversationHandler.END

    try:
        await query.edit_message_text(f"Deploying {strategy} on {instrument}...")

        result = bridge.start_agent(
            strategy_name=strategy,
            instrument=instrument,
            mainnet=(network == "mainnet"),
            risk_overrides=risk_params,
        )

        from tg_bot.formatters import control_keyboard
        await query.edit_message_text(
            f"Agent deployed!\n\n"
            f"Strategy: {strategy}\n"
            f"Instrument: {instrument}\n"
            f"Network: {network}\n"
            f"Preset: {preset}\n\n"
            f"Use the buttons below or /status to monitor.",
            reply_markup=control_keyboard(),
        )
    except Exception as e:
        log.error("Failed to deploy agent: %s", e, exc_info=True)
        await query.edit_message_text(f"Deployment failed: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Deployment cancelled.")
    return ConversationHandler.END


def build_deploy_handler() -> ConversationHandler:
    """Build the /deploy conversation handler."""
    return ConversationHandler(
        entry_points=[CommandHandler("deploy", deploy_cmd)],
        states={
            CHOOSE_STRATEGY: [
                CallbackQueryHandler(strategy_page_callback, pattern=r"^strat_page_\d+$"),
                CallbackQueryHandler(strategy_select_callback, pattern=r"^strat_(?!page_)\w+$"),
            ],
            CHOOSE_INSTRUMENT: [
                CallbackQueryHandler(instrument_select_callback, pattern=r"^inst_"),
            ],
            CHOOSE_PRESET: [
                CallbackQueryHandler(preset_select_callback, pattern=r"^preset_"),
            ],
            CONFIRM: [
                CallbackQueryHandler(confirm_deploy_callback, pattern="^confirm_deploy$"),
                CallbackQueryHandler(confirm_cancel_callback, pattern="^confirm_cancel$"),
            ],
            MAINNET_CONFIRM: [
                CallbackQueryHandler(mainnet_confirm_callback, pattern=r"^mainnet_confirm_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
