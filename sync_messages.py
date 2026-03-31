#!/usr/bin/env python3
"""
Bidirectional message sync between Discord and Slack channels.

This script listens for messages on both Discord and Slack, then forwards them
to the corresponding channel on the other platform with user attribution.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

# Load environment variables
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Name used for the per-channel Discord webhooks we create/own.
_WEBHOOK_NAME = "Slack Bridge"


class ChannelMapping:
    """Manages Discord ↔ Slack channel mappings."""

    def __init__(self, mapping_file: Path):
        self.mapping_file = mapping_file
        self.discord_to_slack: dict[str, str] = {}
        self.slack_to_discord: dict[str, str] = {}
        self.load_mappings()

    def load_mappings(self) -> None:
        """Load channel mappings from JSON file."""
        if not self.mapping_file.exists():
            raise FileNotFoundError(
                f"Channel mapping file not found: {self.mapping_file}\n"
                f"Copy channel_mapping.json.example to channel_mapping.json "
                f"and configure your channel mappings."
            )

        with open(self.mapping_file) as f:
            data = json.load(f)

        for mapping in data.get("mappings", []):
            discord_id = mapping["discord_channel_id"]
            slack_id = mapping["slack_channel_id"]
            self.discord_to_slack[discord_id] = slack_id
            self.slack_to_discord[slack_id] = discord_id

        logger.info(f"Loaded {len(self.discord_to_slack)} channel mappings")

    def get_slack_channel(self, discord_channel_id: str) -> Optional[str]:
        """Get corresponding Slack channel ID for a Discord channel."""
        return self.discord_to_slack.get(discord_channel_id)

    def get_discord_channel(self, slack_channel_id: str) -> Optional[str]:
        """Get corresponding Discord channel ID for a Slack channel."""
        return self.slack_to_discord.get(slack_channel_id)


class MessageSyncBot:
    """Handles bidirectional message synchronization between Discord and Slack."""

    def __init__(self):
        # Load configuration
        self.discord_token = self._require_env("DISCORD_BOT_TOKEN")
        self.slack_bot_token = self._require_env("SLACK_BOT_TOKEN")
        self.slack_app_token = self._require_env("SLACK_APP_TOKEN")

        # Load channel mappings
        mapping_path = Path(__file__).parent / "channel_mapping.json"
        self.channel_mapping = ChannelMapping(mapping_path)

        # Initialize Discord bot
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        self.discord_bot = commands.Bot(command_prefix="!", intents=intents)
        self._setup_discord_handlers()

        # Initialize Slack async clients
        self.slack_client = AsyncWebClient(token=self.slack_bot_token)
        # Optional overrides for apps granted `chat:write.customize`:
        # - SLACK_CUSTOM_USERNAME: display name to post as (e.g. "DC2Slack")
        # - SLACK_CUSTOM_ICON_URL: URL to avatar image to use for posts
        self.slack_custom_username: Optional[str] = os.environ.get("SLACK_CUSTOM_USERNAME")
        self.slack_custom_icon_url: Optional[str] = os.environ.get("SLACK_CUSTOM_ICON_URL")
        # slack_socket, http_session, and cdn_session are created in run() —
        # aiohttp.ClientSession requires a running event loop
        self.slack_socket: Optional[SocketModeClient] = None
        self.http_session: Optional[aiohttp.ClientSession] = None
        # cdn_session has no default Authorization header so it can safely
        # fetch CDN/S3 signed URLs that embed credentials in the URL itself.
        self._cdn_session: Optional[aiohttp.ClientSession] = None

        # Track processed messages to avoid loops
        self.processed_messages: set[str] = set()

        # Per-channel webhook cache (Discord channel ID → Webhook).
        # Webhooks let us post as the original Slack user (name + avatar).
        self._webhook_cache: dict[str, discord.Webhook] = {}
        # IDs of webhooks we own — used to suppress echo in on_message.
        self._our_webhook_ids: set[int] = set()

    @staticmethod
    def _require_env(name: str) -> str:
        """Get required environment variable or raise error."""
        value = os.environ.get(name)
        if not value:
            raise ValueError(
                f"Missing required environment variable: {name}\n"
                f"Copy .env.example to .env and fill in your tokens."
            )
        return value

    def _setup_discord_handlers(self) -> None:
        """Set up Discord event handlers."""

        @self.discord_bot.event
        async def on_ready():
            logger.info(f"Discord bot connected as {self.discord_bot.user}")
            await self._preload_webhooks()

        @self.discord_bot.event
        async def on_message(message: discord.Message):
            # Ignore bot's own messages
            if message.author == self.discord_bot.user:
                return

            # Ignore messages posted by our own Slack Bridge webhooks
            if message.webhook_id and message.webhook_id in self._our_webhook_ids:
                return

            # Check if this message was already processed (from Slack)
            msg_id = f"discord_{message.id}"
            if msg_id in self.processed_messages:
                return

            # Build the text to forward (content + attachments + embeds).
            # Bail out early if there is nothing to forward.
            text = self._format_discord_message(message)
            if not text:
                return

            # Check if channel is mapped
            slack_channel = self.channel_mapping.get_slack_channel(
                str(message.channel.id)
            )
            if not slack_channel:
                return

            # Determine the sender's avatar URL (if available) so we can post with their
            # display name and avatar on Slack (requires `chat:write.customize`).
            avatar_url: Optional[str] = None
            try:
                avatar_url = str(message.author.display_avatar.url)
            except Exception:
                try:
                    avatar_url = str(message.author.avatar.url)
                except Exception:
                    avatar_url = None

            await self._send_to_slack(
                slack_channel,
                message.author.display_name,
                text,
                message.id,
                avatar_url=avatar_url,
            )

    @staticmethod
    def _slack_escape(text: str) -> str:
        """Escape Slack mrkdwn special characters in plain text segments."""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;")

    def _format_discord_message(self, message: discord.Message) -> str:
        """Build the text to forward to Slack from a Discord message.

        Combines the plain-text content, any file/image attachment URLs, and
        rich embed data (title, description, fields) that bots such as the
        GitHub integration use instead of—or in addition to—plain content.
        """
        parts: list[str] = []

        if message.content:
            parts.append(message.content)

        # File and image attachments — forward their direct URLs so Slack can
        # unfurl them (unfurl_media is intentionally left enabled for these).
        for attachment in message.attachments:
            parts.append(attachment.url)

        # Rich embeds posted by bots (e.g. GitHub commit notifications).
        for embed in message.embeds:
            embed_parts: list[str] = []
            if embed.title:
                safe_title = self._slack_escape(embed.title)
                if embed.url:
                    embed_parts.append(f"*<{embed.url}|{safe_title}>*")
                else:
                    embed_parts.append(f"*{safe_title}*")
            if embed.description:
                embed_parts.append(self._slack_escape(embed.description))
            for field in embed.fields:
                safe_name = self._slack_escape(field.name)
                safe_value = self._slack_escape(field.value)
                embed_parts.append(f"*{safe_name}*: {safe_value}")
            if embed_parts:
                parts.append("\n".join(embed_parts))

        return "\n".join(parts)

    async def _preload_webhooks(self) -> None:
        """Discover and cache any pre-existing Slack Bridge webhooks on startup.

        This populates ``_our_webhook_ids`` before any messages arrive so that
        webhook messages from a previous run are not echoed back to Slack.
        """
        for discord_channel_id in self.channel_mapping.discord_to_slack:
            try:
                channel = self.discord_bot.get_channel(int(discord_channel_id))
                if not channel:
                    channel = await self.discord_bot.fetch_channel(int(discord_channel_id))
                wh = await self._find_existing_webhook(channel)
                if wh:
                    self._webhook_cache[discord_channel_id] = wh
                    self._our_webhook_ids.add(wh.id)
                    logger.info("Preloaded webhook id=%s for channel %s", wh.id, discord_channel_id)
            except Exception as e:
                logger.warning("Could not preload webhooks for channel %s: %s", discord_channel_id, e)

    @staticmethod
    async def _find_existing_webhook(channel: discord.TextChannel) -> Optional[discord.Webhook]:
        """Return the first existing Slack Bridge webhook for *channel*, or ``None``."""
        for wh in await channel.webhooks():
            if wh.name == _WEBHOOK_NAME and wh.token:
                return wh
        return None

    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        """Return the Slack Bridge webhook for *channel*, creating it if needed."""
        channel_id = str(channel.id)
        if channel_id in self._webhook_cache:
            return self._webhook_cache[channel_id]

        # Search existing webhooks first to avoid duplicates across restarts.
        wh = await self._find_existing_webhook(channel)
        if wh:
            self._webhook_cache[channel_id] = wh
            self._our_webhook_ids.add(wh.id)
            return wh

        # None found — create a new one.
        wh = await channel.create_webhook(name=_WEBHOOK_NAME)
        self._webhook_cache[channel_id] = wh
        self._our_webhook_ids.add(wh.id)
        logger.info("Created webhook id=%s for channel %s", wh.id, channel_id)
        return wh

    def _setup_slack_handlers(self) -> None:
        """Set up Slack event handlers."""

        @self.slack_socket.socket_mode_request_listeners.append
        async def handle_socket_mode_request(client: SocketModeClient, req: SocketModeRequest):
            logger.info("Slack socket event: type=%s envelope_id=%s", req.type, req.envelope_id)
            try:
                if req.type == "events_api":
                    await client.send_socket_mode_response(
                        SocketModeResponse(envelope_id=req.envelope_id)
                    )

                    event = req.payload.get("event", {})
                    logger.info("Slack event payload: type=%s subtype=%s channel=%s bot_id=%s",
                                event.get("type"), event.get("subtype"),
                                event.get("channel"), event.get("bot_id"))
                    if event.get("type") == "message":
                        await self._handle_slack_message(event)
            except Exception:
                logger.exception("Error in Slack socket handler")

    async def _handle_slack_message(self, event: dict) -> None:
        """Handle incoming Slack message."""
        logger.info("Processing Slack message: channel=%s user=%s bot_id=%s subtype=%s text=%r",
                    event.get("channel"), event.get("user"),
                    event.get("bot_id"), event.get("subtype"), event.get("text"))

        # Ignore bot messages to prevent loops.
        # Allow file_share subtype so image/file uploads are forwarded.
        subtype = event.get("subtype")
        if event.get("bot_id") or (subtype and subtype != "file_share"):
            logger.info("Skipping: bot_id=%s subtype=%s", event.get("bot_id"), subtype)
            return

        channel_id = event.get("channel")
        text = event.get("text") or ""
        files: list[dict] = event.get("files", [])
        user_id = event.get("user")
        ts = event.get("ts")

        # Need at least text or a file to forward
        if not all([channel_id, ts, user_id]) or (not text and not files):
            return

        # Check if this message was already processed (from Discord)
        msg_id = f"slack_{ts}"
        if msg_id in self.processed_messages:
            return

        # Check if channel is mapped
        discord_channel_id = self.channel_mapping.get_discord_channel(channel_id)
        if not discord_channel_id:
            logger.info("No mapping for Slack channel %s — skipping", channel_id)
            return

        # Get username and avatar
        try:
            user_info = await self.slack_client.users_info(user=user_id)
            profile = user_info["user"]["profile"]
            username = profile.get("display_name") or user_info["user"]["name"]
            avatar_url: Optional[str] = (
                profile.get("image_512")
                or profile.get("image_192")
                or profile.get("image_72")
            )
        except SlackApiError as e:
            logger.warning("Failed to get Slack user info for %s: %s", user_id, e)
            username = "Unknown User"
            avatar_url = None

        logger.info("Forwarding Slack -> Discord: channel=%s user=%s files=%d", channel_id, username, len(files))

        await self._send_to_discord(discord_channel_id, username, text, ts, slack_files=files, avatar_url=avatar_url)

    async def _send_to_slack(
        self, channel_id: str, username: str, text: str, discord_msg_id: int, avatar_url: Optional[str] = None
    ) -> None:
        """Send message to Slack channel."""
        try:
            post_username = username or self.slack_custom_username
            post_icon = avatar_url or self.slack_custom_icon_url

            kwargs = {
                "channel": channel_id,
                "text": text,
                "unfurl_links": False,
                "unfurl_media": True,
                "username": post_username,
            }
            if post_icon:
                kwargs["icon_url"] = post_icon

            await self.slack_client.chat_postMessage(**kwargs)

            self.processed_messages.add(f"discord_{discord_msg_id}")
            logger.info(f"Forwarded Discord message to Slack channel {channel_id}")

        except SlackApiError as e:
            logger.error(f"Failed to send message to Slack: {e}")

    async def _download_slack_file(self, url: str, max_bytes: int) -> Optional[bytes]:
        """Download a Slack private file, handling CDN redirects correctly.

        Slack private URLs require a Bearer token for the initial auth check but
        then redirect to a CDN endpoint (e.g. S3) whose signed URL already
        embeds access credentials.  Sending the Bearer ``Authorization`` header
        to that CDN causes the CDN to return a small error response instead of
        the real file.  We therefore disable auto-redirects on the first request
        and follow any redirect with a plain session that carries no default
        ``Authorization`` header.
        """
        try:
            # Initial authenticated request to Slack — no auto-redirect so we
            # can detect a CDN redirect before forwarding the auth header.
            async with self.http_session.get(url, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    cdn_url = resp.headers.get("Location")
                    if not cdn_url:
                        logger.warning("Redirect from %s has no Location header", url)
                        return None
                    # Follow the CDN URL without auth headers; the redirect URL
                    # already embeds any necessary signed download token.
                    cdn_session = self._cdn_session or aiohttp.ClientSession()
                    async with cdn_session.get(cdn_url) as cdn_resp:
                        return await self._read_response_bytes(cdn_resp, cdn_url, max_bytes)
                else:
                    return await self._read_response_bytes(resp, url, max_bytes)
        except Exception as e:
            logger.warning("Error downloading Slack file %s: %s", url, e)
            return None

    @staticmethod
    async def _read_response_bytes(
        resp: aiohttp.ClientResponse, url: str, max_bytes: int
    ) -> Optional[bytes]:
        """Read and size-check an HTTP response body."""
        if resp.status != 200:
            logger.warning("Failed to download file from %s: HTTP %s", url, resp.status)
            return None
        try:
            content_length = int(resp.headers.get("Content-Length", 0) or 0)
        except (ValueError, TypeError):
            content_length = 0
        if content_length and content_length > max_bytes:
            logger.warning(
                "Skipping file from %s: Content-Length %d exceeds %d-byte limit",
                url, content_length, max_bytes,
            )
            return None
        data = await resp.read()
        if len(data) > max_bytes:
            logger.warning(
                "Skipping file from %s: downloaded size %d exceeds %d-byte limit",
                url, len(data), max_bytes,
            )
            return None
        return data

    async def _send_to_discord(
        self, channel_id: str, username: str, text: str, slack_ts: str,
        slack_files: Optional[list[dict]] = None,
        avatar_url: Optional[str] = None,
    ) -> None:
        """Send a Slack message to Discord via a per-channel webhook.

        Using a webhook (rather than the bot account) lets Discord display the
        original Slack user's display name and profile picture natively.
        Falls back to ``channel.send()`` if webhook creation is not possible.
        """
        try:
            channel = self.discord_bot.get_channel(int(channel_id))
            if not channel:
                try:
                    channel = await self.discord_bot.fetch_channel(int(channel_id))
                except discord.errors.NotFound:
                    logger.error("Discord channel %s not found (404)", channel_id)
                    return
                except discord.errors.DiscordException as e:
                    logger.error("Failed to fetch Discord channel %s: %s", channel_id, e)
                    return

            # Download any Slack file attachments and re-upload them to Discord.
            # Discord's default max upload size is 25 MB.
            _MAX_FILE_BYTES = 25 * 1024 * 1024
            discord_files: list[discord.File] = []
            if slack_files and self.http_session:
                for slack_file in slack_files:
                    url = slack_file.get("url_private_download") or slack_file.get("url_private")
                    raw_name = slack_file.get("name", "file")
                    # Sanitise filename: keep only safe characters
                    filename = "".join(c for c in raw_name if c.isalnum() or c in "-_. ")[:200] or "file"
                    if not url:
                        continue
                    # Respect Slack-reported file size when available
                    reported_size = slack_file.get("size", 0)
                    if reported_size and reported_size > _MAX_FILE_BYTES:
                        logger.warning("Skipping Slack file %s: size %d exceeds limit", filename, reported_size)
                        continue
                    data = await self._download_slack_file(url, _MAX_FILE_BYTES)
                    if data:
                        discord_files.append(discord.File(io.BytesIO(data), filename=filename))

            # Try to post via webhook so the message appears with the Slack
            # user's name and avatar instead of the bot's identity.
            try:
                webhook = await self._get_or_create_webhook(channel)
                send_kwargs: dict[str, Any] = {"username": username}
                if avatar_url:
                    send_kwargs["avatar_url"] = avatar_url
                if text:
                    send_kwargs["content"] = text
                if discord_files:
                    send_kwargs["files"] = discord_files
                await webhook.send(**send_kwargs)
            except discord.errors.Forbidden:
                # Bot lacks Manage Webhooks — fall back to plain channel.send()
                logger.warning(
                    "No Manage Webhooks permission for channel %s; falling back to bot message", channel_id
                )
                formatted_text = f"**{username}** (Slack): {text}" if text else f"**{username}** (Slack):"
                await channel.send(formatted_text, files=discord_files)

            self.processed_messages.add(f"slack_{slack_ts}")
            logger.info(f"Forwarded Slack message to Discord channel {channel_id}")

        except discord.errors.DiscordException as e:
            logger.error(f"Failed to send message to Discord: {e}")

    async def _diagnose_slack_membership(self) -> None:
        """Log which mapped Slack channels the bot is/isn't a member of."""
        logger.info("=== Slack channel membership diagnostic ===")
        try:
            auth = await self.slack_client.auth_test()
            logger.info(
                "Slack auth_test: user_id=%s team=%s bot_id=%s",
                auth.get("user_id"),
                auth.get("team"),
                auth.get("bot_id"),
            )
        except SlackApiError as e:
            logger.warning("Slack auth_test failed: %s", e)
        slack_channels = list(self.channel_mapping.slack_to_discord.keys())
        not_member: list[str] = []
        for ch in slack_channels:
            try:
                info = await self.slack_client.conversations_info(channel=ch)
                is_member = info["channel"].get("is_member", False)
                name = info["channel"].get("name", ch)
                status = "OK (member)" if is_member else "NOT A MEMBER"
                logger.info("  Slack channel #%s (%s): %s", name, ch, status)
                if not is_member:
                    not_member.append(f"#{name} ({ch})")
            except SlackApiError as e:
                logger.warning("  Could not check channel %s: %s", ch, e.response.get("error", e))
        if not_member:
            logger.warning(
                "Bot is NOT a member of these Slack channels — events won't arrive:\n    %s\n"
                "Fix: invite the bot with /invite @<bot-name> in each channel.",
                "\n    ".join(not_member),
            )
        else:
            logger.info("Bot is a member of all mapped Slack channels.")
        logger.info("===========================================")

    async def run(self) -> None:
        """Run both bots concurrently."""
        logger.info("Starting message sync bot...")

        # Create shared HTTP session and socket client here —
        # aiohttp.ClientSession requires a running event loop
        self.http_session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.slack_bot_token}"}
        )
        # Plain session without auth headers — used to follow CDN redirects
        # whose signed URLs already embed access credentials.
        self._cdn_session = aiohttp.ClientSession()
        self.slack_socket = SocketModeClient(
            app_token=self.slack_app_token,
            web_client=self.slack_client,
        )
        self._setup_slack_handlers()

        await self._diagnose_slack_membership()

        logger.info("Connecting to Slack Socket Mode...")
        await self.slack_socket.connect()
        logger.info("Slack socket connected.")

        try:
            # Start Discord bot (blocks until disconnected)
            await self.discord_bot.start(self.discord_token)
        finally:
            await self.http_session.close()
            if self._cdn_session:
                await self._cdn_session.close()


def main():
    """Entry point for the message sync script."""
    try:
        bot = MessageSyncBot()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
