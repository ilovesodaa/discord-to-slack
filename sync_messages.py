#!/usr/bin/env python3
"""
Bidirectional message sync between Discord and Slack channels.

This script listens for messages on both Discord and Slack, then forwards them
to the corresponding channel on the other platform with user attribution.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

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

        # Initialize Slack client
        self.slack_client = WebClient(token=self.slack_bot_token)
        self.slack_socket = SocketModeClient(
            app_token=self.slack_app_token,
            web_client=self.slack_client,
        )
        self._setup_slack_handlers()

        # Track processed messages to avoid loops
        self.processed_messages: set[str] = set()

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

        @self.discord_bot.event
        async def on_message(message: discord.Message):
            # Ignore bot's own messages
            if message.author == self.discord_bot.user:
                return

            # Ignore messages from other bots to prevent loops
            if message.author.bot:
                return

            # Check if this message was already processed (from Slack)
            msg_id = f"discord_{message.id}"
            if msg_id in self.processed_messages:
                return

            # Check if channel is mapped
            slack_channel = self.channel_mapping.get_slack_channel(
                str(message.channel.id)
            )
            if not slack_channel:
                return

            # Forward to Slack
            await self._send_to_slack(
                slack_channel,
                message.author.display_name,
                message.content,
                message.id,
            )

    def _setup_slack_handlers(self) -> None:
        """Set up Slack event handlers."""

        @self.slack_socket.socket_mode_request_listeners.append
        def handle_socket_mode_request(client: SocketModeClient, req: SocketModeRequest):
            if req.type == "events_api":
                # Acknowledge the event immediately
                response = SocketModeResponse(envelope_id=req.envelope_id)
                client.send_socket_mode_response(response)

                # Process the event
                event = req.payload.get("event", {})
                if event.get("type") == "message":
                    # Run async event handler in the Discord bot's event loop
                    asyncio.create_task(self._handle_slack_message(event))

    async def _handle_slack_message(self, event: dict) -> None:
        """Handle incoming Slack message."""
        # Ignore bot messages to prevent loops
        if event.get("bot_id") or event.get("subtype"):
            return

        channel_id = event.get("channel")
        text = event.get("text")
        user_id = event.get("user")
        ts = event.get("ts")

        if not all([channel_id, text, user_id, ts]):
            return

        # Check if this message was already processed (from Discord)
        msg_id = f"slack_{ts}"
        if msg_id in self.processed_messages:
            return

        # Check if channel is mapped
        discord_channel_id = self.channel_mapping.get_discord_channel(channel_id)
        if not discord_channel_id:
            return

        # Get username
        try:
            user_info = self.slack_client.users_info(user=user_id)
            username = user_info["user"]["profile"].get("display_name") or user_info[
                "user"
            ]["name"]
        except SlackApiError as e:
            logger.error(f"Failed to get Slack user info: {e}")
            username = "Unknown User"

        # Forward to Discord
        await self._send_to_discord(discord_channel_id, username, text, ts)

    async def _send_to_slack(
        self, channel_id: str, username: str, text: str, discord_msg_id: int
    ) -> None:
        """Send message to Slack channel."""
        try:
            # Format message with user attribution
            formatted_text = f"**{username}** (Discord): {text}"

            result = self.slack_client.chat_postMessage(
                channel=channel_id,
                text=formatted_text,
                unfurl_links=False,
                unfurl_media=False,
            )

            # Mark as processed
            self.processed_messages.add(f"discord_{discord_msg_id}")
            logger.info(f"Forwarded Discord message to Slack channel {channel_id}")

        except SlackApiError as e:
            logger.error(f"Failed to send message to Slack: {e}")

    async def _send_to_discord(
        self, channel_id: str, username: str, text: str, slack_ts: str
    ) -> None:
        """Send message to Discord channel."""
        try:
            channel = self.discord_bot.get_channel(int(channel_id))
            if not channel:
                logger.error(f"Discord channel {channel_id} not found")
                return

            # Format message with user attribution
            formatted_text = f"**{username}** (Slack): {text}"

            await channel.send(formatted_text)

            # Mark as processed
            self.processed_messages.add(f"slack_{slack_ts}")
            logger.info(f"Forwarded Slack message to Discord channel {channel_id}")

        except discord.errors.DiscordException as e:
            logger.error(f"Failed to send message to Discord: {e}")

    async def start_discord(self) -> None:
        """Start Discord bot."""
        await self.discord_bot.start(self.discord_token)

    def start_slack(self) -> None:
        """Start Slack socket mode client."""
        self.slack_socket.connect()

    async def run(self) -> None:
        """Run both bots concurrently."""
        logger.info("Starting message sync bot...")

        # Start Slack socket in a separate thread
        import threading

        slack_thread = threading.Thread(target=self.start_slack, daemon=True)
        slack_thread.start()

        # Start Discord bot (this blocks until stopped)
        await self.start_discord()


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
