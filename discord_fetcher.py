from __future__ import annotations

import logging
from typing import Optional

import requests

from models import DiscordChannel, DiscordRole, ServerSnapshot

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"

CHANNEL_TYPE_CATEGORY     = 4
CHANNEL_TYPE_TEXT         = 0
CHANNEL_TYPE_VOICE        = 2
CHANNEL_TYPE_ANNOUNCEMENT = 5
CHANNEL_TYPE_FORUM        = 15
CHANNEL_TYPE_STAGE        = 13


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}"}


def _get(token: str, path: str) -> list | dict:
    url = f"{DISCORD_API_BASE}{path}"
    response = requests.get(url, headers=_headers(token), timeout=15)
    response.raise_for_status()
    return response.json()


def fetch_server(token: str, guild_id: str) -> ServerSnapshot:
    """Fetch all roles and channels from a Discord guild."""
    logger.info("Fetching Discord guild structure for guild_id=%s", guild_id)

    raw_roles: list[dict] = _get(token, f"/guilds/{guild_id}/roles")  # type: ignore[assignment]
    raw_channels: list[dict] = _get(token, f"/guilds/{guild_id}/channels")  # type: ignore[assignment]

    roles = [
        DiscordRole(id=r["id"], name=r["name"])
        for r in raw_roles
        if r["name"] != "@everyone"
    ]

    categories: dict[str, str] = {
        c["id"]: c["name"]
        for c in raw_channels
        if c["type"] == CHANNEL_TYPE_CATEGORY
    }

    channels = [
        DiscordChannel(
            id=c["id"],
            name=c["name"],
            type=c["type"],
            parent_id=c.get("parent_id"),
            topic=c.get("topic") or "",
            permission_overwrites=c.get("permission_overwrites", []),
        )
        for c in raw_channels
        if c["type"] != CHANNEL_TYPE_CATEGORY
    ]

    logger.info(
        "Retrieved %d roles, %d categories, %d non-category channels",
        len(roles),
        len(categories),
        len(channels),
    )
    return ServerSnapshot(roles=roles, categories=categories, channels=channels)
