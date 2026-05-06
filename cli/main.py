"""hl — Autonomous Hyperliquid trading CLI."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

# Ensure project root is importable
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

app = typer.Typer(
    name="hl",
    help="Autonomous Hyperliquid trader — direct HL API execution.",
    no_args_is_help=True,
    add_completion=False,
)

from cli.commands.run import run_cmd
from cli.commands.status import status_cmd
from cli.commands.trade import trade_cmd
from cli.commands.account import account_cmd
from cli.commands.strategies import strategies_cmd
from cli.commands.guard import guard_app
from cli.commands.radar import radar_app
from cli.commands.pulse import pulse_app
from cli.commands.apex import apex_app
from cli.commands.builder import builder_app
from cli.commands.reflect import reflect_app
from cli.commands.wallet import wallet_app
from cli.commands.setup import setup_app
from cli.commands.mcp import mcp_app
from cli.commands.skills import skills_app
from cli.commands.journal import journal_app
from cli.commands.keys import keys_app
from cli.commands.telegram_cmd import telegram_app

app.command("run", help="Start autonomous trading with a strategy")(run_cmd)
app.command("status", help="Show positions, PnL, and risk state")(status_cmd)
app.command("trade", help="Place a single manual order")(trade_cmd)
app.command("account", help="Show HL account state")(account_cmd)
app.command("strategies", help="List available strategies")(strategies_cmd)
app.add_typer(guard_app, name="guard", help="Guard trailing stop system")
app.add_typer(radar_app, name="radar", help="Radar — screen HL perps for setups")
app.add_typer(pulse_app, name="pulse", help="Pulse — detect assets with capital inflow")
app.add_typer(apex_app, name="apex", help="APEX — autonomous multi-slot trading")
app.add_typer(builder_app, name="builder", help="Builder fee — revenue collection on trades")
app.add_typer(reflect_app, name="reflect", help="Reflect — performance review and self-improvement")
app.add_typer(wallet_app, name="wallet", help="Encrypted keystore wallet management")
app.add_typer(setup_app, name="setup", help="Environment validation and setup")
app.add_typer(mcp_app, name="mcp", help="MCP server — AI agent tool discovery")
app.add_typer(skills_app, name="skills", help="Skill discovery and registry")
app.add_typer(journal_app, name="journal", help="Trade journal — structured position records with reasoning")
app.add_typer(keys_app, name="keys", help="Unified key management across backends")
app.add_typer(telegram_app, name="telegram", help="Telegram bot — deploy agents from chat")


def main():
    app()


if __name__ == "__main__":
    main()
