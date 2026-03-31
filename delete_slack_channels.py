from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Iterable

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import get_slack_bot_token


logger = logging.getLogger(__name__)


def iter_channels(client: WebClient) -> Iterable[dict]:
    cursor: str | None = None
    while True:
        kwargs = {"limit": 200, "types": "public_channel,private_channel"}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.conversations_list(**kwargs)
        for ch in resp.get("channels", []):
            yield ch
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break


def archive_channel(client: WebClient, channel_id: str) -> bool:
    try:
        client.conversations_archive(channel=channel_id)
        return True
    except SlackApiError as e:
        code = e.response.get("error")
        if code == "ratelimited":
            retry_after = int(e.response.headers.get("Retry-After", "1"))
            logger.warning("Rate limited, sleeping %ds", retry_after)
            time.sleep(retry_after)
            return archive_channel(client, channel_id)
        logger.error("Could not archive %s: %s", channel_id, code)
        return False


def confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive (delete) all Slack channels in a workspace")
    parser.add_argument("--dry-run", action="store_true", help="List channels but don't archive them")
    parser.add_argument("--force", action="store_true", help="No interactive confirmation")
    parser.add_argument("--token", help="Slack bot token (overrides SLACK_BOT_TOKEN from .env)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    token = args.token or get_slack_bot_token()
    client = WebClient(token=token)

    channels = list(iter_channels(client))
    if not channels:
        logger.info("No channels found.")
        return 0

    print("Found %d channels:" % len(channels))
    for ch in channels:
        print(f"- #{ch.get('name')} (id={ch.get('id')})")

    if args.dry_run:
        print("\nDry run: no channels will be archived.")
        return 0

    if not args.force:
        print("\nWARNING: This will archive all listed channels. This operation is destructive.")
        if not confirm("Do you want to proceed?"):
            print("Aborted by user.")
            return 1

    archived = 0
    errors = 0

    for ch in channels:
        ch_id = ch.get("id")
        ch_name = ch.get("name")
        logger.info("Archiving #%s (id=%s)", ch_name, ch_id)
        ok = archive_channel(client, ch_id)
        if ok:
            archived += 1
        else:
            errors += 1

    print()
    print(f"Archived: {archived}")
    print(f"Errors: {errors}")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
