from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Iterable

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config
from discord_fetcher import fetch_server
from migrate import build_mirror_plan


logger = logging.getLogger(__name__)


def list_slack_channels(client: WebClient) -> dict[str, dict]:
    """Return a mapping of channel name -> channel object."""
    result: dict[str, dict] = {}
    cursor: str | None = None
    while True:
        kwargs = {"limit": 200, "types": "public_channel,private_channel"}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.conversations_list(**kwargs)
        for ch in resp.get("channels", []):
            name = ch.get("name")
            if name:
                result[name] = ch
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return result


def build_mappings(slack_channels: dict[str, dict], plan_items) -> tuple[list[dict], list[dict]]:
    """Match plan items to existing Slack channels.

    Returns (mappings, unmatched_items).
    """
    mappings: list[dict] = []
    unmatched: list[dict] = []

    # Build name -> id mapping for quick lookup
    for item in plan_items:
        if not item.discord_channel_id:
            continue
        slack_name = item.slack_name
        ch = slack_channels.get(slack_name)
        if ch:
            mappings.append({
                "discord_channel_id": item.discord_channel_id,
                "slack_channel_id": ch.get("id"),
                "description": f"#{slack_name}",
            })
        else:
            unmatched.append({
                "discord_channel_id": item.discord_channel_id,
                "expected_slack_name": slack_name,
            })

    return mappings, unmatched


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate channel_mapping.json by matching Discord plan to existing Slack channels")
    parser.add_argument("--dry-run", action="store_true", help="Print suggested mappings but don't save")
    parser.add_argument("--out", default="channel_mapping.json", help="Output file path")
    parser.add_argument("--token", help="Slack bot token (overrides SLACK_BOT_TOKEN)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        discord_token = config.get_discord_bot_token()
        guild_id = config.get_discord_guild_id()
        slack_token = args.token or config.get_slack_bot_token()
    except ValueError as e:
        logger.error(e)
        return 2

    # Fetch Discord snapshot and build plan
    try:
        snapshot = fetch_server(discord_token, guild_id)
    except RuntimeError as e:
        logger.error("Failed to fetch Discord guild: %s", e)
        return 3

    plan = build_mirror_plan(snapshot, guild_id)
    if not plan:
        logger.error("No plan items generated from Discord snapshot")
        return 4

    client = WebClient(token=slack_token)
    try:
        slack_channels = list_slack_channels(client)
    except SlackApiError as e:
        logger.error("Failed to list Slack channels: %s", e)
        return 5

    mappings, unmatched = build_mappings(slack_channels, plan)

    print(f"Matched {len(mappings)} mappings, {len(unmatched)} unmatched")
    if mappings:
        for m in mappings:
            print(f"- {m['discord_channel_id']} -> {m['slack_channel_id']} ({m['description']})")

    if unmatched:
        print("\nUnmatched Discord channels (couldn't find Slack channel with expected name):")
        for u in unmatched:
            print(f"- {u['discord_channel_id']} expected #{u['expected_slack_name']}")

    if args.dry_run:
        print("\nDry run: not saving mapping file.")
        return 0

    out_path = Path(args.out)
    data = {"mappings": mappings}
    out_path.write_text(json.dumps(data, indent=2))
    print(f"\nSaved {len(mappings)} mappings to {out_path}")
    if unmatched:
        print("Some channels were unmatched; please review and edit the mapping file if needed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
