"""Agent control commands — status, pause, resume, stop, balance."""
from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from tg_bot.auth import authorized
from tg_bot.formatters import balance_card, control_keyboard, help_card, status_card, shutdown_card

log = logging.getLogger("telegram.control")


def _get_bridge(context):
    return context.bot_data.get("engine_bridge")


@authorized
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current agent status."""
    bridge = _get_bridge(context)
    if not bridge or not bridge.is_running():
        await update.message.reply_text("No agent is running. Use /deploy to start one.")
        return

    info = bridge.get_status()
    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    text = status_card(
        strategy=info["strategy"],
        instrument=info["instrument"],
        network=network,
        tick_count=info["tick_count"],
        pos_qty=info["pos_qty"],
        avg_entry=info["avg_entry"],
        upnl=info["upnl"],
        rpnl=info["rpnl"],
        elapsed_s=info["elapsed_s"],
        risk_ok=info["risk_ok"],
    )
    await update.message.reply_text(text, reply_markup=control_keyboard())


@authorized
async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pause the running agent."""
    bridge = _get_bridge(context)
    if not bridge or not bridge.is_running():
        await update.message.reply_text("No agent is running.")
        return

    bridge.pause_agent()
    await update.message.reply_text("Agent paused. Use /resume to continue.")


@authorized
async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resume a paused agent."""
    bridge = _get_bridge(context)
    if not bridge:
        await update.message.reply_text("No agent to resume. Use /deploy first.")
        return

    if bridge.is_running():
        await update.message.reply_text("Agent is already running.")
        return

    try:
        bridge.resume_agent()
        await update.message.reply_text("Agent resumed.")
    except Exception as e:
        await update.message.reply_text(f"Failed to resume: {e}")


@authorized
async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop the running agent."""
    bridge = _get_bridge(context)
    if not bridge or not bridge.is_running():
        await update.message.reply_text("No agent is running.")
        return

    summary = bridge.stop_agent()
    text = shutdown_card(
        tick_count=summary.get("tick_count", 0),
        total_placed=summary.get("total_placed", 0),
        total_filled=summary.get("total_filled", 0),
        total_pnl=summary.get("total_pnl", 0.0),
        elapsed_s=summary.get("elapsed_s", 0.0),
    )
    await update.message.reply_text(text)


@authorized
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show account balance."""
    from cli.keystore import list_keystores

    keystores = list_keystores()
    if not keystores:
        await update.message.reply_text("No wallet found. Use /start first.")
        return

    address = keystores[0]["address"]
    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    try:
        from common.credentials import resolve_private_key
        from parent.hl_proxy import HLProxy
        from cli.hl_adapter import DirectHLProxy

        private_key = resolve_private_key(venue="hl")
        raw_hl = HLProxy(private_key=private_key, testnet=(network != "mainnet"))
        hl = DirectHLProxy(raw_hl)
        account = hl.get_account_state()

        bal = 0.0
        if "crossMarginSummary" in account:
            bal = float(account["crossMarginSummary"].get("accountValue", 0))
        elif "marginSummary" in account:
            bal = float(account["marginSummary"].get("accountValue", 0))

        await update.message.reply_text(balance_card(address, bal, network))
    except Exception as e:
        log.error("Balance check failed: %s", e)
        await update.message.reply_text(f"Could not fetch balance: {e}")


@authorized
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show help."""
    await update.message.reply_text(help_card())


async def control_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline control button presses."""
    query = update.callback_query
    await query.answer()

    action = query.data.replace("ctrl_", "")

    if action == "status":
        bridge = _get_bridge(context)
        if not bridge or not bridge.is_running():
            await query.edit_message_text("No agent is running.")
            return
        info = bridge.get_status()
        config = context.bot_data.get("config")
        network = config.default_network if config else "testnet"
        text = status_card(
            strategy=info["strategy"],
            instrument=info["instrument"],
            network=network,
            tick_count=info["tick_count"],
            pos_qty=info["pos_qty"],
            avg_entry=info["avg_entry"],
            upnl=info["upnl"],
            rpnl=info["rpnl"],
            elapsed_s=info["elapsed_s"],
            risk_ok=info["risk_ok"],
        )
        await query.edit_message_text(text, reply_markup=control_keyboard())

    elif action == "pause":
        bridge = _get_bridge(context)
        if bridge and bridge.is_running():
            bridge.pause_agent()
            await query.edit_message_text("Agent paused. Use /resume to continue.")
        else:
            await query.edit_message_text("No agent is running.")

    elif action == "resume":
        bridge = _get_bridge(context)
        if bridge:
            bridge.resume_agent()
            await query.edit_message_text("Agent resumed.", reply_markup=control_keyboard())
        else:
            await query.edit_message_text("No agent to resume.")

    elif action == "stop":
        bridge = _get_bridge(context)
        if bridge and bridge.is_running():
            summary = bridge.stop_agent()
            text = shutdown_card(
                tick_count=summary.get("tick_count", 0),
                total_placed=summary.get("total_placed", 0),
                total_filled=summary.get("total_filled", 0),
                total_pnl=summary.get("total_pnl", 0.0),
                elapsed_s=summary.get("elapsed_s", 0.0),
            )
            await query.edit_message_text(text)
        else:
            await query.edit_message_text("No agent is running.")

    elif action == "balance":
        from cli.keystore import list_keystores
        keystores = list_keystores()
        if not keystores:
            await query.edit_message_text("No wallet found.")
            return
        # Simplified — just show address
        address = keystores[0]["address"]
        await query.edit_message_text(f"Wallet: {address}\nUse /balance for full details.")


def register_control_handlers(app) -> None:
    """Register all control command handlers."""
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(control_button_callback, pattern=r"^ctrl_"))
