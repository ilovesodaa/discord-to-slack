from __future__ import annotations

import logging
import time
from typing import TypedDict

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from models import MirrorItem

logger = logging.getLogger(__name__)


class ApplyResult(TypedDict):
    created: int
    skipped: int
    errors: list[str]
    mappings: list[dict[str, str]]  # Discord channel ID -> Slack channel ID mappings


def apply_plan(token: str | None, items: list[MirrorItem], dry_run: bool = False) -> ApplyResult:
    """Create Slack channels from a list of MirrorItems.

    If dry_run is True, nothing is created — only the plan is printed.
    """
    result: ApplyResult = {"created": 0, "skipped": 0, "errors": [], "mappings": []}

    if dry_run:
        _print_plan(items)
        return result

    if not token:
        raise ValueError("SLACK_BOT_TOKEN is required for a live migration run.")

    client = WebClient(token=token)

    for item in items:
        _create_channel(client, item, result)

    return result


def _create_channel(client: WebClient, item: MirrorItem, result: ApplyResult) -> None:
    try:
        response = client.conversations_create(
            name=item.slack_name,
            is_private=item.is_private,
        )
        channel_id: str = response["channel"]["id"]
        logger.info("Created %s channel #%s", "private" if item.is_private else "public", item.slack_name)

        if item.purpose:
            try:
                client.conversations_setTopic(channel=channel_id, topic=item.purpose[:250])
            except SlackApiError as e:
                logger.warning("Could not set topic for #%s: %s", item.slack_name, e.response["error"])

        result["created"] += 1

        # Store mapping if this is a Discord channel (not a role)
        if item.discord_channel_id:
            result["mappings"].append({
                "discord_channel_id": item.discord_channel_id,
                "slack_channel_id": channel_id,
                "description": f"#{item.slack_name}"
            })

    except SlackApiError as e:
        error_code = e.response["error"]

        if error_code == "name_taken":
            logger.warning("Channel #%s already exists — skipping", item.slack_name)
            result["skipped"] += 1

        elif error_code == "ratelimited":
            retry_after = int(e.response.headers.get("Retry-After", 1))
            logger.warning("Rate limited — sleeping %ds before retry", retry_after)
            time.sleep(retry_after)
            _create_channel(client, item, result)  # single retry

        else:
            msg = f"Failed to create #{item.slack_name}: {error_code}"
            logger.error(msg)
            result["errors"].append(msg)


def _print_plan(items: list[MirrorItem]) -> None:
    col_name = max((len(i.slack_name) for i in items), default=10)
    header = f"{'CHANNEL NAME':<{col_name}}  {'TYPE':<8}  PURPOSE"
    print(header)
    print("-" * len(header))
    for item in items:
        kind = "private" if item.is_private else "public "
        purpose = (item.purpose[:60] + "…") if len(item.purpose) > 60 else item.purpose
        print(f"#{item.slack_name:<{col_name - 1}}  {kind}  {purpose}")
    print()
    print(f"Total: {len(items)} channel(s) to create")
