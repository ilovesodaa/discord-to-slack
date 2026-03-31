from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Iterable

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import get_slack_bot_token


logger = logging.getLogger(__name__)


def load_mappings(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Mapping file not found: {path}")
    with path.open() as fh:
        data = json.load(fh)
    return data.get("mappings", [])


def unarchive_channel(client: WebClient, channel_id: str) -> bool:
    try:
        info = client.conversations_info(channel=channel_id)
        ch = info.get("channel", {})
        if not ch:
            logger.error("Channel not found: %s", channel_id)
            return False

        if not ch.get("is_archived"):
            logger.info("Channel #%s (id=%s) is already active", ch.get("name"), channel_id)
            return True

        client.conversations_unarchive(channel=channel_id)
        logger.info("Unarchived #%s (id=%s)", ch.get("name"), channel_id)
        return True

    except SlackApiError as e:
        code = e.response.get("error")
        if code == "ratelimited":
            retry_after = int(e.response.headers.get("Retry-After", "1"))
            logger.warning("Rate limited, sleeping %ds", retry_after)
            time.sleep(retry_after)
            return unarchive_channel(client, channel_id)
        logger.error("Failed to unarchive %s: %s", channel_id, code)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Unarchive Slack channels from channel_mapping.json")
    parser.add_argument("--mapping", default="channel_mapping.json", help="Path to mapping file")
    parser.add_argument("--dry-run", action="store_true", help="List actions but don't unarchive")
    parser.add_argument("--force", action="store_true", help="No interactive confirmation")
    parser.add_argument("--token", help="Slack bot token (overrides SLACK_BOT_TOKEN from .env)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    token = args.token or get_slack_bot_token()
    client = WebClient(token=token)

    try:
        mappings = load_mappings(Path(args.mapping))
    except FileNotFoundError as e:
        logger.error(e)
        return 2

    if not mappings:
        logger.info("No mappings found in %s", args.mapping)
        return 0

    print(f"Found {len(mappings)} mapping(s) in {args.mapping}:")
    for m in mappings:
        print(f"- slack_id={m.get('slack_channel_id')} desc={m.get('description')}")

    if args.dry_run:
        print("\nDry run: no channels will be unarchived.")
        return 0

    if not args.force:
        print("\nThis will attempt to unarchive the listed Slack channels (destructive for archived state).")
        try:
            ans = input("Proceed and unarchive channels? [y/N]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 1

    success = 0
    errors = 0

    for m in mappings:
        sid = m.get("slack_channel_id")
        if not sid:
            logger.warning("Skipping mapping without slack_channel_id: %s", m)
            continue
        ok = unarchive_channel(client, sid)
        if ok:
            success += 1
        else:
            errors += 1

    print()
    print(f"Unarchived: {success}")
    print(f"Errors: {errors}")
    return 0 if errors == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
