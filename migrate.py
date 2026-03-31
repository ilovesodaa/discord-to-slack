from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import config
from discord_fetcher import (
    CHANNEL_TYPE_VOICE,
    CHANNEL_TYPE_STAGE,
    fetch_server,
)
from models import MirrorItem, ServerSnapshot
from slack_creator import apply_plan

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# Discord permission bit for VIEW_CHANNEL
_VIEW_CHANNEL = 1024


def _sanitize(name: str, max_len: int = 80) -> str:
    """Lowercase a name and replace non-alphanumeric chars with hyphens."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = name.strip("-")
    return name[:max_len] or "channel"


def _deduplicate(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            result.append(name)
        else:
            i = 2
            candidate = f"{name}-{i}"
            while candidate in seen:
                i += 1
                candidate = f"{name}-{i}"
            seen.add(candidate)
            result.append(candidate)
    return result


def build_mirror_plan(snapshot: ServerSnapshot, guild_id: str) -> list[MirrorItem]:
    items: list[MirrorItem] = []

    # 1. Roles → private channels
    for role in snapshot.roles:
        items.append(
            MirrorItem(
                slack_name=_sanitize(f"role-{role.name}"),
                is_private=True,
                purpose=f"Discord role: {role.name}",
            )
        )

    # 2. Channels (text/announcement/forum)
    raw_names: list[str] = []
    raw_items: list[MirrorItem] = []

    for channel in snapshot.channels:
        if channel.type in (CHANNEL_TYPE_VOICE, CHANNEL_TYPE_STAGE):
            logger.warning("Skipping voice/stage channel: #%s", channel.name)
            continue

        # Detect private: @everyone role (id == guild_id) denied VIEW_CHANNEL
        is_private = any(
            int(ow.get("deny", "0")) & _VIEW_CHANNEL
            for ow in channel.permission_overwrites
            if ow.get("id") == guild_id and ow.get("type") == 0
        )

        # Prefix with category name if the channel belongs to one
        cat_name = snapshot.categories.get(channel.parent_id or "")
        if cat_name:
            raw_name = _sanitize(f"{cat_name}-{channel.name}")
        else:
            raw_name = _sanitize(channel.name)

        raw_names.append(raw_name)
        raw_items.append(
            MirrorItem(
                slack_name=raw_name,   # placeholder; deduplicated below
                is_private=is_private,
                purpose=channel.topic or "",
                discord_channel_id=channel.id,  # Include Discord channel ID
            )
        )

    # Deduplicate channel names
    deduped = _deduplicate(raw_names)
    for item, final_name in zip(raw_items, deduped):
        item.slack_name = final_name
        items.append(item)

    return items


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mirror a Discord server's structure (roles + channels) into Slack."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the migration plan without creating any Slack channels.",
    )
    args = parser.parse_args()

    try:
        discord_token = config.get_discord_bot_token()
        guild_id      = config.get_discord_guild_id()
        slack_token   = None if args.dry_run else config.get_slack_bot_token()
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)

    try:
        snapshot = fetch_server(discord_token, guild_id)
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
    plan     = build_mirror_plan(snapshot, guild_id)

    if not plan:
        logger.warning("Nothing to mirror. Exiting.")
        return

    if args.dry_run:
        logger.info("Dry-run mode — no Slack channels will be created.")

    result = apply_plan(slack_token, plan, dry_run=args.dry_run)

    if not args.dry_run:
        print(
            f"\nDone.  Created: {result['created']}  "
            f"Skipped: {result['skipped']}  "
            f"Errors: {len(result['errors'])}"
        )
        if result["errors"]:
            print("\nErrors:")
            for err in result["errors"]:
                print(f"  • {err}")

        # Save channel mappings to channel_mapping.json
        if result["mappings"]:
            mapping_file = Path(__file__).parent / "channel_mapping.json"
            mapping_data = {"mappings": result["mappings"]}

            with open(mapping_file, "w") as f:
                json.dump(mapping_data, f, indent=2)

            logger.info(
                "Saved %d channel mappings to %s for use with sync_messages.py",
                len(result["mappings"]),
                mapping_file
            )
            print(f"\n✓ Channel mappings saved to {mapping_file.name}")
            print("  You can now use sync_messages.py to sync messages between Discord and Slack.")


if __name__ == "__main__":
    main()
