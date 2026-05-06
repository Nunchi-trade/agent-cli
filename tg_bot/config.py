"""Telegram bot configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TelegramBotConfig:
    """Configuration for the Telegram trading bot."""

    bot_token: str = ""
    allowed_chat_ids: List[int] = field(default_factory=list)
    default_network: str = "testnet"
    mainnet_confirmation: bool = True
    notification_interval_s: int = 60
    tick_summary_interval_s: int = 300
    max_concurrent_agents: int = 1

    @classmethod
    def from_env(cls) -> "TelegramBotConfig":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_ids_raw = os.environ.get("TELEGRAM_CHAT_ID", "")
        chat_ids = []
        if chat_ids_raw:
            chat_ids = [int(x.strip()) for x in chat_ids_raw.split(",") if x.strip()]

        network = "mainnet" if os.environ.get("HL_TESTNET", "true").lower() == "false" else "testnet"

        return cls(
            bot_token=token,
            allowed_chat_ids=chat_ids,
            default_network=network,
        )

    @property
    def is_mainnet(self) -> bool:
        return self.default_network == "mainnet"
