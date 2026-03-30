from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DiscordRole:
    id: str
    name: str


@dataclass
class DiscordChannel:
    id: str
    name: str
    type: int                        # 0=text, 2=voice, 4=category, 5=announcement, 15=forum
    parent_id: Optional[str]
    topic: Optional[str]
    permission_overwrites: list[dict] = field(default_factory=list)


@dataclass
class MirrorItem:
    slack_name: str       # sanitized, ≤80 chars
    is_private: bool
    purpose: str          # becomes Slack channel topic


@dataclass
class ServerSnapshot:
    roles: list[DiscordRole]
    categories: dict[str, str]   # channel_id → category name
    channels: list[DiscordChannel]
