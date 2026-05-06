"""hl telegram — start the Telegram bot interface."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

telegram_app = typer.Typer()


@telegram_app.command("start")
def telegram_start(
    mainnet: bool = typer.Option(
        False, "--mainnet",
        help="Connect to mainnet (default: testnet)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Agents run in dry-run mode (no real orders)",
    ),
):
    """Start the Telegram bot for deploying and controlling trading agents."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    from tg_bot.config import TelegramBotConfig
    from tg_bot.bot import run_bot

    config = TelegramBotConfig.from_env()

    if mainnet:
        config.default_network = "mainnet"

    if not config.bot_token:
        typer.echo(
            "ERROR: TELEGRAM_BOT_TOKEN not set.\n"
            "1. Create a bot via @BotFather on Telegram\n"
            "2. Set TELEGRAM_BOT_TOKEN=<your-token> in your environment\n"
            "3. Run this command again",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Network: {config.default_network}")
    typer.echo(f"Chat IDs: {config.allowed_chat_ids or 'auto-detect on first /start'}")
    typer.echo("Bot starting... (Ctrl+C to stop)")
    typer.echo("")

    run_bot(config)
