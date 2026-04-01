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
import re
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
        # Our own Slack bot_id — used to suppress echo without blocking other apps.
        self._self_bot_id: Optional[str] = None
        # Slack user ID → display name cache (avoids repeated API calls).
        self._slack_user_cache: dict[str, str] = {}
        # Bidirectional message-ID maps for cross-platform edits.
        # Discord message ID → Slack ts (for editing Slack when Discord edits).
        self._msg_map_d2s: dict[int, str] = {}
        # Slack ts → Discord message ID (for editing Discord when Slack edits).
        self._msg_map_s2d: dict[str, int] = {}
        # Discord parent message ID → Discord thread ID (cache to avoid duplicate threads).
        self._discord_thread_map: dict[int, int] = {}

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
                thread_ts=self._msg_map_d2s.get(message.reference.message_id)
                if message.reference and message.reference.message_id
                else None,
            )

        @self.discord_bot.event
        async def on_message_edit(before: discord.Message, after: discord.Message):
            # Only process if we forwarded the original message.
            slack_ts = self._msg_map_d2s.get(after.id)
            if not slack_ts:
                return
            # Skip if content didn't actually change (e.g. embed-only update).
            if before.content == after.content:
                return
            slack_channel = self.channel_mapping.get_slack_channel(
                str(after.channel.id)
            )
            if not slack_channel:
                return
            new_text = self._resolve_discord_mentions(after) if after.content else ""
            if not new_text:
                return
            try:
                await self.slack_client.chat_update(
                    channel=slack_channel,
                    ts=slack_ts,
                    text=new_text,
                )
                logger.info("Edited Slack message ts=%s for Discord edit %s", slack_ts, after.id)
            except SlackApiError as e:
                logger.error("Failed to edit Slack message: %s", e)

    @staticmethod
    def _slack_to_discord_links(text: str) -> str:
        """Convert Slack mrkdwn links to Discord-friendly format.

        Slack encodes URLs as ``<url>`` or ``<url|label>``.  Without conversion,
        the angle brackets suppress Discord's auto-embed.  This turns:
        - ``<https://example.com>`` → ``https://example.com`` (Discord auto-embeds)
        - ``<https://example.com|click here>`` → ``[click here](https://example.com)``

        Channel mentions ``<#C123|general>`` become ``#general``.
        User/special mentions (``<@U123>``, ``<!here>``) are left for the async
        resolver or passed through as-is.
        """
        def _replace(m: re.Match) -> str:
            inner = m.group(1)
            # Channel mentions: <#C123|general> → #general
            if inner.startswith("#"):
                if "|" in inner:
                    _, name = inner.split("|", 1)
                    return f"#{name}"
                return m.group(0)
            # User mentions and special commands — leave for async resolver
            if inner.startswith(("@", "!")):
                return m.group(0)
            if "|" in inner:
                url, label = inner.split("|", 1)
                return f"[{label}]({url})"
            return inner

        return re.sub(r"<([^>]+)>", _replace, text)

    async def _resolve_slack_mentions(self, text: str) -> str:
        """Resolve Slack ``<@UXXXX>`` user mentions to ``@display_name``.

        Also converts broadcast mentions (``<!here>``, ``<!channel>``,
        ``<!everyone>``) to their readable ``@here`` / ``@channel`` /
        ``@everyone`` equivalents.  Results are cached so repeated pings
        of the same user don't hammer the Slack API.
        """
        # Broadcast mentions
        text = text.replace("<!here>", "@here")
        text = text.replace("<!channel>", "@channel")
        text = text.replace("<!everyone>", "@everyone")
        # Also handle <!here|here> variant Slack sometimes sends
        text = re.sub(r"<!here\|here>", "@here", text)
        text = re.sub(r"<!channel\|channel>", "@channel", text)
        text = re.sub(r"<!everyone\|everyone>", "@everyone", text)

        # User mentions: <@U12345> or <@U12345|old_name>
        user_ids = set(re.findall(r"<@(U[A-Z0-9]+)(?:\|[^>]*)?>", text))
        for uid in user_ids:
            if uid not in self._slack_user_cache:
                try:
                    info = await self.slack_client.users_info(user=uid)
                    profile = info["user"]["profile"]
                    name = profile.get("display_name") or info["user"]["name"]
                    self._slack_user_cache[uid] = name
                except SlackApiError:
                    self._slack_user_cache[uid] = uid  # graceful fallback
            # Replace both <@U123> and <@U123|old_name> forms
            text = re.sub(rf"<@{uid}(?:\|[^>]*)?>", f"@{self._slack_user_cache[uid]}", text)
        return text

    @staticmethod
    def _slack_escape(text: str) -> str:
        """Escape Slack mrkdwn special characters in plain text segments."""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;")

    @staticmethod
    def _resolve_discord_mentions(message: discord.Message) -> str:
        """Replace Discord mention tokens with readable @name text for Slack.

        Discord encodes mentions as ``<@ID>``, ``<@!ID>`` (nick), ``<#ID>``
        (channel), and ``<@&ID>`` (role).  We swap them out for human-readable
        ``@DisplayName``, ``#channel-name``, and ``@RoleName`` so Slack users
        can tell who was pinged.
        """
        text = message.content
        for user in message.mentions:
            text = text.replace(f"<@!{user.id}>", f"@{user.display_name}")
            text = text.replace(f"<@{user.id}>", f"@{user.display_name}")
        for channel in message.channel_mentions:
            text = text.replace(f"<#{channel.id}>", f"#{channel.name}")
        for role in message.role_mentions:
            text = text.replace(f"<@&{role.id}>", f"@{role.name}")
        return text

    def _format_discord_message(self, message: discord.Message) -> str:
        """Build the text to forward to Slack from a Discord message.

        Combines the plain-text content, any file/image attachment URLs, and
        rich embed data (title, description, fields) that bots such as the
        GitHub integration use instead of—or in addition to—plain content.
        """
        parts: list[str] = []

        if message.content:
            parts.append(self._resolve_discord_mentions(message))

        # File and image attachments — forward their direct URLs so Slack can
        # unfurl them (unfurl_media is intentionally left enabled for these).
        for attachment in message.attachments:
            parts.append(attachment.url)
        # I dont think this works. Too Bad!
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

    async def _get_or_create_discord_thread(
        self, discord_channel_id: str, parent_msg_id: int
    ) -> Optional[int]:
        """Return the Discord thread ID for *parent_msg_id*, creating it if needed."""
        if parent_msg_id in self._discord_thread_map:
            return self._discord_thread_map[parent_msg_id]
        try:
            channel = self.discord_bot.get_channel(int(discord_channel_id))
            if not channel:
                channel = await self.discord_bot.fetch_channel(int(discord_channel_id))
            parent_msg = await channel.fetch_message(parent_msg_id)
            thread = parent_msg.thread or await parent_msg.create_thread(name="Thread")
            self._discord_thread_map[parent_msg_id] = thread.id
            return thread.id
        except discord.errors.DiscordException as e:
            logger.error("Failed to get/create Discord thread for msg %s: %s", parent_msg_id, e)
            return None

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
        subtype = event.get("subtype")

        # For message_changed events the actual content (text, files, attachments,
        # bot_id, ts, user) lives inside event["message"], not at the top level.
        # Normalise early so the rest of the method is subtype-agnostic.
        msg = event.get("message", event) if subtype == "message_changed" else event

        files: list[dict] = msg.get("files", [])
        attachments: list[dict] = msg.get("attachments", [])
        blocks: list[dict] = msg.get("blocks", [])
        bot_id = msg.get("bot_id") or event.get("bot_id")

        # Any attachment that carries an image URL is worth forwarding —
        # this covers Giphy, Tenor, link unfurls with preview images, etc.
        image_attachments = [
            att for att in attachments
            if att.get("image_url") or att.get("thumb_url")
        ]

        # Slack's GIF picker (and some apps) put images in blocks, not
        # attachments.  Extract image URLs from image-type blocks.
        block_image_urls: list[str] = []
        for block in blocks:
            if block.get("type") == "image" and block.get("image_url"):
                block_image_urls.append(block["image_url"])

        logger.info(
            "Processing Slack message: channel=%s user=%s bot_id=%s subtype=%s "
            "files=%d attachments=%d image_attachments=%d block_images=%d text=%r",
            event.get("channel"), msg.get("user") or event.get("user"),
            bot_id, subtype, len(files), len(attachments), len(image_attachments),
            len(block_image_urls), msg.get("text"),
        )

        # Filter: skip messages from our own bridge bot to prevent echo loops.
        # Other app/bot messages are forwarded normally.
        if bot_id and bot_id == self._self_bot_id:
            logger.info("Skipping: message from our own bot (bot_id=%s)", bot_id)
            return

        # Allow file_share, bot_message, and message_changed (for bot image posts).
        # Skip everything else (message_deleted, channel_join, huddle, etc.).
        allowed_subtypes = ("file_share", "bot_message", "message_changed")
        if subtype and subtype not in allowed_subtypes:
            logger.info("Skipping: unhandled subtype=%s", subtype)
            return

        # message_changed: if we forwarded the original, edit it on Discord.
        if subtype == "message_changed":
            original_ts = msg.get("ts")
            discord_msg_id = self._msg_map_s2d.get(original_ts) if original_ts else None
            if discord_msg_id:
                ch_id = event.get("channel")
                d_ch_id = self.channel_mapping.get_discord_channel(ch_id) if ch_id else None
                if d_ch_id:
                    new_text = msg.get("text", "")
                    if new_text:
                        new_text = await self._resolve_slack_mentions(new_text)
                        new_text = self._slack_to_discord_links(new_text)
                    webhook = self._webhook_cache.get(d_ch_id)
                    if webhook and new_text:
                        try:
                            await webhook.edit_message(discord_msg_id, content=new_text)
                            logger.info("Edited Discord message %s for Slack edit ts=%s", discord_msg_id, original_ts)
                        except discord.errors.DiscordException as e:
                            logger.error("Failed to edit Discord message: %s", e)
                return
            # No mapping — if it has image attachments, forward as new; otherwise skip.
            if not image_attachments:
                logger.info("Skipping: message_changed with no mapping and no image attachments")
                return

        channel_id = event.get("channel")
        text = msg.get("text") or ""
        user_id = msg.get("user") or event.get("user")
        ts = msg.get("ts") or event.get("ts")
        slack_thread_ts = msg.get("thread_ts") or event.get("thread_ts")

        if not all([channel_id, ts]) or (not text and not files and not image_attachments and not block_image_urls):
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

        # Resolve display name and avatar.  Bot/app messages may have no user_id;
        # fall back to the event's own username field (e.g. "Giphy", "GitHub").
        avatar_url: Optional[str] = None
        if user_id:
            try:
                user_info = await self.slack_client.users_info(user=user_id)
                profile = user_info["user"]["profile"]
                username = profile.get("display_name") or user_info["user"]["name"]
                avatar_url = (
                    profile.get("image_512")
                    or profile.get("image_192")
                    or profile.get("image_72")
                )
            except SlackApiError as e:
                logger.warning("Failed to get Slack user info for %s: %s", user_id, e)
                username = msg.get("username") or event.get("username") or "Unknown User"
        else:
            username = msg.get("username") or event.get("username") or "App"

        logger.info(
            "Forwarding Slack -> Discord: channel=%s user=%s files=%d image_attachments=%d block_images=%d",
            channel_id, username, len(files), len(image_attachments), len(block_image_urls),
        )

        # If this is a Slack thread reply, post it into the corresponding Discord thread.
        discord_thread_id: Optional[int] = None
        is_reply = slack_thread_ts and slack_thread_ts != ts
        if is_reply:
            parent_discord_msg_id = self._msg_map_s2d.get(slack_thread_ts)
            if parent_discord_msg_id:
                discord_thread_id = await self._get_or_create_discord_thread(
                    discord_channel_id, parent_discord_msg_id
                )

        await self._send_to_discord(
            discord_channel_id, username, text, ts,
            slack_files=files,
            slack_attachments=image_attachments,
            avatar_url=avatar_url,
            block_image_urls=block_image_urls,
            discord_thread_id=discord_thread_id,
        )

    async def _send_to_slack(
        self, channel_id: str, username: str, text: str, discord_msg_id: int,
        avatar_url: Optional[str] = None, thread_ts: Optional[str] = None,
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
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            result = await self.slack_client.chat_postMessage(**kwargs)

            self.processed_messages.add(f"discord_{discord_msg_id}")
            # Track the mapping so Discord edits can update this Slack message.
            slack_ts = result.get("ts")
            if slack_ts:
                self._msg_map_d2s[discord_msg_id] = slack_ts
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
        content_type = resp.headers.get("Content-Type", "")
        # An HTML or JSON response is an error page, not the actual file.
        # This happens when the bot token lacks files:read scope or the URL expired.
        if content_type.startswith("text/html") or content_type.startswith("application/json"):
            try:
                preview = await resp.text(encoding="utf-8", errors="replace")
            except Exception:
                preview = "<unreadable>"
            logger.warning(
                "Skipping file from %s: got Content-Type %r instead of a file "
                "(check bot token has files:read scope). Response preview: %.200s",
                url, content_type, preview,
            )
            return None
        logger.debug("Downloading from %s: Content-Type=%s", url, content_type)
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
        slack_attachments: Optional[list[dict]] = None,
        avatar_url: Optional[str] = None,
        block_image_urls: Optional[list[str]] = None,
        discord_thread_id: Optional[int] = None,
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
                        # Warn if the downloaded data is much smaller than Slack reports —
                        # this indicates we fetched a thumbnail or error page instead of
                        # the real file (e.g. missing files:read scope or CDN mismatch).
                        if (
                            reported_size
                            and len(data) < reported_size * 0.5
                            and (reported_size - len(data)) > 5120
                        ):
                            logger.warning(
                                "Downloaded %d bytes for %r but Slack reports %d bytes — "
                                "may be a thumbnail; check bot token scopes (files:read)",
                                len(data), filename, reported_size,
                            )
                        discord_files.append(discord.File(io.BytesIO(data), filename=filename))

            # Extract image URLs from Slack attachments (Giphy, Tenor, link unfurls, etc.)
            # and append them to the Discord message content so Discord auto-embeds them.
            attachment_urls: list[str] = []
            for att in (slack_attachments or []):
                img_url = att.get("image_url") or att.get("thumb_url")
                if img_url:
                    attachment_urls.append(img_url)

            # Build Discord embeds for block images (GIF picker, etc.) so the
            # image renders cleanly without a raw URL cluttering the message.
            discord_embeds: list[discord.Embed] = []
            for img_url in (block_image_urls or []):
                discord_embeds.append(discord.Embed().set_image(url=img_url))

            # Build final content: original text + any attachment image URLs
            # Resolve Slack user mentions and convert Slack-formatted URLs
            # so they display properly on Discord.
            converted_text = text
            if converted_text:
                converted_text = await self._resolve_slack_mentions(converted_text)
                converted_text = self._slack_to_discord_links(converted_text)
            content_parts = [p for p in [converted_text] + attachment_urls if p]
            discord_content = "\n".join(content_parts)

            # Try to post via webhook so the message appears with the Slack
            # user's name and avatar instead of the bot's identity.
            try:
                webhook = await self._get_or_create_webhook(channel)
                send_kwargs: dict[str, Any] = {"username": username}
                if avatar_url:
                    send_kwargs["avatar_url"] = avatar_url
                if discord_content:
                    send_kwargs["content"] = discord_content
                if discord_files:
                    send_kwargs["files"] = discord_files
                if discord_embeds:
                    send_kwargs["embeds"] = discord_embeds
                if discord_thread_id:
                    send_kwargs["thread_id"] = discord_thread_id
                sent_msg = await webhook.send(wait=True, **send_kwargs)
                # Track the mapping so Slack edits can update this Discord message.
                if sent_msg:
                    self._msg_map_s2d[slack_ts] = sent_msg.id
            except discord.errors.Forbidden:
                # Bot lacks Manage Webhooks — fall back to plain channel.send()
                logger.warning(
                    "No Manage Webhooks permission for channel %s; falling back to bot message", channel_id
                )
                formatted_text = f"**{username}** (Slack): {discord_content}" if discord_content else f"**{username}** (Slack):"
                sent_msg = await channel.send(formatted_text, files=discord_files)
                if sent_msg:
                    self._msg_map_s2d[slack_ts] = sent_msg.id

            self.processed_messages.add(f"slack_{slack_ts}")
            logger.info(f"Forwarded Slack message to Discord channel {channel_id}")

        except discord.errors.DiscordException as e:
            logger.error(f"Failed to send message to Discord: {e}")

    async def _diagnose_slack_membership(self) -> None:
        """Log which mapped Slack channels the bot is/isn't a member of."""
        logger.info("=== Slack channel membership diagnostic ===")
        try:
            auth = await self.slack_client.auth_test()
            self._self_bot_id = auth.get("bot_id")
            logger.info(
                "Slack auth_test: user_id=%s team=%s bot_id=%s",
                auth.get("user_id"),
                auth.get("team"),
                self._self_bot_id,
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
