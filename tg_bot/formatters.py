"""Telegram message formatting — plain text cards and inline keyboards."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def escape_md(text: str) -> str:
    """Escape special characters for MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    for ch in special:
        text = text.replace(ch, f"\\{ch}")
    return text


# ── Inline Keyboards ──


def wallet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Create New Wallet", callback_data="wallet_create"),
            InlineKeyboardButton("Import Private Key", callback_data="wallet_import"),
        ],
    ])


def strategy_keyboard(strategies: Dict[str, Dict[str, Any]], page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    """Paginated strategy selection keyboard."""
    names = sorted(strategies.keys())
    total_pages = (len(names) + per_page - 1) // per_page
    start = page * per_page
    page_items = names[start:start + per_page]

    rows = []
    for i in range(0, len(page_items), 2):
        row = [InlineKeyboardButton(name, callback_data=f"strat_{name}") for name in page_items[i:i + 2]]
        rows.append(row)

    # Pagination buttons
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("<< Prev", callback_data=f"strat_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next >>", callback_data=f"strat_page_{page + 1}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)


def instrument_keyboard() -> InlineKeyboardMarkup:
    """Common instruments + YEX markets."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ETH-PERP", callback_data="inst_ETH-PERP"),
            InlineKeyboardButton("BTC-PERP", callback_data="inst_BTC-PERP"),
        ],
        [
            InlineKeyboardButton("SOL-PERP", callback_data="inst_SOL-PERP"),
            InlineKeyboardButton("HYPE-PERP", callback_data="inst_HYPE-PERP"),
        ],
        [
            InlineKeyboardButton("VXX-USDYP", callback_data="inst_VXX-USDYP"),
            InlineKeyboardButton("US3M-USDYP", callback_data="inst_US3M-USDYP"),
        ],
        [
            InlineKeyboardButton("BTCSWP-USDYP", callback_data="inst_BTCSWP-USDYP"),
        ],
    ])


def preset_keyboard() -> InlineKeyboardMarkup:
    """Risk preset selection."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Conservative", callback_data="preset_conservative"),
            InlineKeyboardButton("Default", callback_data="preset_default"),
            InlineKeyboardButton("Aggressive", callback_data="preset_aggressive"),
        ],
    ])


def confirm_keyboard(mainnet: bool = False) -> InlineKeyboardMarkup:
    """Deployment confirmation."""
    rows = [
        [
            InlineKeyboardButton("Deploy Agent", callback_data="confirm_deploy"),
            InlineKeyboardButton("Cancel", callback_data="confirm_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def mainnet_confirm_keyboard() -> InlineKeyboardMarkup:
    """Double confirmation for mainnet."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "YES - Deploy on MAINNET with REAL funds",
                callback_data="mainnet_confirm_yes",
            ),
        ],
        [
            InlineKeyboardButton("Cancel", callback_data="mainnet_confirm_no"),
        ],
    ])


def control_keyboard() -> InlineKeyboardMarkup:
    """Agent control buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Pause", callback_data="ctrl_pause"),
            InlineKeyboardButton("Resume", callback_data="ctrl_resume"),
            InlineKeyboardButton("Stop", callback_data="ctrl_stop"),
        ],
        [
            InlineKeyboardButton("Status", callback_data="ctrl_status"),
            InlineKeyboardButton("Balance", callback_data="ctrl_balance"),
        ],
    ])


# ── Message Cards ──


def welcome_card(has_wallet: bool, address: str = "", balance: float = 0.0) -> str:
    if has_wallet:
        return (
            "Nunchi Trading Agent\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Wallet: {address[:8]}...{address[-6:]}\n"
            f"Balance: ${balance:.2f}\n\n"
            "Commands:\n"
            "/deploy - Deploy a trading agent\n"
            "/status - Check agent status\n"
            "/balance - Account balance\n"
            "/stop - Stop running agent\n"
            "/help - All commands"
        )
    return (
        "Nunchi Trading Agent\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Deploy autonomous trading agents on Hyperliquid\n"
        "directly from Telegram.\n\n"
        "First, let's set up your wallet."
    )


def wallet_created_card(address: str, network: str) -> str:
    return (
        "Wallet Created\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Address: {address}\n"
        f"Network: {network}\n\n"
        f"{'Claim testnet USDyP: /claim' if network == 'testnet' else 'Deposit USDC via Hyperliquid web UI'}\n\n"
        "Your key is encrypted and stored locally.\n"
        "Use /deploy to start a trading agent."
    )


def strategy_info_card(name: str, info: Dict[str, Any]) -> str:
    params = "\n".join(f"  {k}: {v}" for k, v in info.get("params", {}).items())
    return (
        f"Strategy: {name}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{info['description']}\n\n"
        f"Default Parameters:\n{params}"
    )


def deploy_confirm_card(
    strategy: str,
    instrument: str,
    preset: str,
    network: str,
    risk_params: Dict[str, Any],
) -> str:
    return (
        "Deploy Confirmation\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Strategy:   {strategy}\n"
        f"Instrument: {instrument}\n"
        f"Preset:     {preset}\n"
        f"Network:    {network.upper()}\n\n"
        f"Risk Limits:\n"
        f"  Max Position: {risk_params.get('max_position_qty', 'default')}\n"
        f"  Max Notional: ${risk_params.get('max_notional_usd', 'default')}\n"
        f"  Max Leverage: {risk_params.get('max_leverage', 'default')}x\n"
    )


def fill_card(
    side: str,
    quantity: str,
    price: str,
    instrument: str,
    strategy: str,
    tick: int,
) -> str:
    direction = "BUY" if side == "buy" else "SELL"
    return (
        f"Fill: {direction} {quantity} {instrument} @ ${price}\n"
        f"Strategy: {strategy} | Tick: {tick}"
    )


def status_card(
    strategy: str,
    instrument: str,
    network: str,
    tick_count: int,
    pos_qty: float,
    avg_entry: float,
    upnl: float,
    rpnl: float,
    elapsed_s: float,
    risk_ok: bool,
) -> str:
    total_pnl = upnl + rpnl
    sign = lambda v: f"+{v:.2f}" if v >= 0 else f"{v:.2f}"
    elapsed_min = int(elapsed_s // 60)

    return (
        "Agent Status\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Strategy:   {strategy}\n"
        f"Instrument: {instrument}\n"
        f"Network:    {network}\n"
        f"Ticks:      {tick_count} ({elapsed_min}min)\n\n"
        f"Position: {sign(pos_qty)} @ ${avg_entry:.4f}\n"
        f"PnL: uPnL ${sign(upnl)} | rPnL ${sign(rpnl)} | Total ${sign(total_pnl)}\n"
        f"Risk: {'OK' if risk_ok else 'BLOCKED'}"
    )


def shutdown_card(
    tick_count: int,
    total_placed: int,
    total_filled: int,
    total_pnl: float,
    elapsed_s: float,
) -> str:
    sign = lambda v: f"+{v:.2f}" if v >= 0 else f"{v:.2f}"
    return (
        "Agent Stopped\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Ticks:   {tick_count}\n"
        f"Orders:  {total_placed} placed, {total_filled} filled\n"
        f"PnL:     ${sign(total_pnl)}\n"
        f"Runtime: {int(elapsed_s)}s"
    )


def balance_card(address: str, balance: float, network: str) -> str:
    return (
        "Account Balance\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Address: {address[:8]}...{address[-6:]}\n"
        f"Balance: ${balance:.2f}\n"
        f"Network: {network}"
    )


def help_card() -> str:
    return (
        "Nunchi Bot Commands\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/start   - Setup wallet\n"
        "/deploy  - Deploy trading agent\n"
        "/status  - Agent status + PnL\n"
        "/balance - Account balance\n"
        "/pause   - Pause agent\n"
        "/resume  - Resume agent\n"
        "/stop    - Stop agent\n"
        "/switch  - Change strategy\n"
        "/apex    - APEX multi-strategy mode\n"
        "/help    - This message"
    )
