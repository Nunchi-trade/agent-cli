"""APEX multi-strategy orchestration mode via Telegram."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from tg_bot.auth import authorized

log = logging.getLogger("tg_bot.apex")

# Track the APEX subprocess
_apex_proc: subprocess.Popen | None = None


@authorized
async def apex_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start APEX multi-slot orchestration."""
    global _apex_proc

    if _apex_proc and _apex_proc.poll() is None:
        await update.message.reply_text(
            "APEX is already running.\n"
            "Use /apex_stop to stop it, or /apex_status to check."
        )
        return

    config = context.bot_data.get("config")
    network = config.default_network if config else "testnet"

    project_root = str(Path(__file__).resolve().parent.parent.parent)
    cmd = [sys.executable, "-m", "cli.main", "apex", "run"]
    if network == "mainnet":
        cmd.append("--mainnet")

    try:
        _apex_proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        await update.message.reply_text(
            f"APEX started (pid={_apex_proc.pid})\n"
            f"Network: {network}\n\n"
            "Use /apex_status to check, /apex_stop to stop."
        )
        log.info("APEX started (pid=%d, network=%s)", _apex_proc.pid, network)
    except Exception as e:
        log.error("Failed to start APEX: %s", e)
        await update.message.reply_text(f"Failed to start APEX: {e}")


@authorized
async def apex_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check APEX status."""
    global _apex_proc

    if not _apex_proc:
        await update.message.reply_text("APEX is not running. Use /apex to start.")
        return

    rc = _apex_proc.poll()
    if rc is not None:
        await update.message.reply_text(f"APEX exited with code {rc}. Use /apex to restart.")
        _apex_proc = None
        return

    # Try to get status from CLI
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "cli.main", "apex", "status"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Strip ANSI codes for Telegram
        import re
        clean = re.sub(r'\033\[[0-9;]*m', '', result.stdout)
        await update.message.reply_text(clean[:4000] if clean else "APEX running (no status output)")
    except Exception as e:
        await update.message.reply_text(f"APEX running (pid={_apex_proc.pid}), status check failed: {e}")


@authorized
async def apex_stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop APEX."""
    global _apex_proc

    if not _apex_proc or _apex_proc.poll() is not None:
        await update.message.reply_text("APEX is not running.")
        _apex_proc = None
        return

    import signal
    _apex_proc.send_signal(signal.SIGTERM)
    try:
        _apex_proc.wait(timeout=15)
        await update.message.reply_text("APEX stopped.")
    except subprocess.TimeoutExpired:
        _apex_proc.kill()
        await update.message.reply_text("APEX killed (did not stop gracefully).")

    _apex_proc = None


def register_apex_handlers(app) -> None:
    """Register APEX command handlers."""
    app.add_handler(CommandHandler("apex", apex_cmd))
    app.add_handler(CommandHandler("apex_status", apex_status_cmd))
    app.add_handler(CommandHandler("apex_stop", apex_stop_cmd))
