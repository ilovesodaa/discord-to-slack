from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(
            f"Missing required environment variable: {name}\n"
            f"Copy .env.example to .env and fill in your tokens."
        )
    return value


def get_discord_bot_token() -> str:
    return _require("DISCORD_BOT_TOKEN")


def get_discord_guild_id() -> str:
    return _require("DISCORD_GUILD_ID")


def get_slack_bot_token() -> str:
    return _require("SLACK_BOT_TOKEN")
