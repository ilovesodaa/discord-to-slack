# discord-to-slack

A migration and synchronization tool for Discord and Slack. It provides two main capabilities:

1. **One-time migration** (`migrate.py`) — Mirrors Discord server structure to Slack
2. **Live message sync** (`sync_messages.py`) — Bidirectional real-time message forwarding

**Migration Highlights**

- Mirrors text, announcement, and forum channels to Slack channels.
- Converts roles into private channels named `#role-<name>` (non-`@everyone`).
- Preserves channel topics and handles duplicate names automatically.
- Skips voice/stage channels (not applicable to Slack).

**Message Sync Highlights**

- Real-time bidirectional message forwarding between Discord and Slack
- User attribution showing who sent each message and from which platform
- Channel mapping configuration to link Discord ↔ Slack channels
- Prevents message loops using message tracking

## Requirements

- Python 3.10 or newer
- **For migration (`migrate.py`):**
  - A Discord bot token with **View Channels** permission for the source guild
  - A Slack bot token with channel-management scopes for the destination workspace
- **For message sync (`sync_messages.py`):**
  - Discord bot with **Send Messages** and **Read Message History** permissions
  - Slack app with **Socket Mode** enabled and appropriate scopes

## Quick setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Copy the example env and fill values:

```bash
cp .env.example .env
# then edit .env and add tokens
```

Required environment variables in `.env`:

**For both scripts:**
- `DISCORD_BOT_TOKEN` — Discord bot token
- `SLACK_BOT_TOKEN` — Bot token for the Slack app (xoxb-...)

**For migrate.py only:**
- `DISCORD_GUILD_ID` — Discord server (guild) ID

**For sync_messages.py only:**
- `SLACK_APP_TOKEN` — Slack app-level token for Socket Mode (xapp-...)

## Usage

### One-time Migration

- Preview (dry run):

```bash
python migrate.py --dry-run
```

- Perform migration:

```bash
python migrate.py
```

Example output:

```
Done.  Created: 42  Skipped: 0  Errors: 0
```

### Live Message Sync

1. Configure channel mappings:

```bash
cp channel_mapping.json.example channel_mapping.json
# Edit channel_mapping.json with your Discord ↔ Slack channel pairs
```

The mapping file format:

```json
{
  "mappings": [
    {
      "discord_channel_id": "1234567890123456789",
      "slack_channel_id": "C01234ABCDE",
      "description": "General chat channel"
    }
  ]
}
```

**Finding channel IDs:**
- **Discord:** Enable Developer Mode in Settings → Advanced, then right-click channel → Copy Channel ID
- **Slack:** Click channel name → ⋮ → View channel details → Scroll to bottom for Channel ID

2. Start the sync bot:

```bash
python sync_messages.py
```

The bot will:
- Connect to both Discord and Slack
- Listen for messages in mapped channels
- Forward each message to the corresponding platform with user attribution
- Format: `**Username** (Platform): Message content`

**Note:** Messages from bots are ignored to prevent infinite loops.

## Files

- `migrate.py` — One-time migration entry point and orchestration
- `sync_messages.py` — Live message sync script with bidirectional forwarding
- `discord_fetcher.py` — Reads roles & channels from Discord
- `slack_creator.py` — Creates channels and groups in Slack
- `models.py` — Shared dataclasses used across modules
- `config.py` — Loads configuration from environment
- `channel_mapping.json` — Channel ID mappings for message sync (user-created)

## Notes

**Migration (`migrate.py`):**
- The tool resolves duplicate names by appending `-2`, `-3`, etc.
- Channels restricted from `@everyone` are created as private channels in Slack.
- Voice and stage channels are intentionally skipped.

**Message Sync (`sync_messages.py`):**
- Only messages in mapped channels are forwarded
- Bot messages are automatically ignored to prevent loops
- The sync is real-time and runs continuously until stopped (Ctrl+C)
- Message history is not synced, only new messages after the bot starts

## Setup Guide

### Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to "Bot" section and create a bot
4. **For migrate.py:** Enable "View Channels" permission
5. **For sync_messages.py:**
   - Enable "Message Content Intent" under Privileged Gateway Intents
   - Add "Send Messages" and "Read Message History" permissions
6. Copy the bot token
7. Invite bot to your server using OAuth2 URL Generator with appropriate permissions

### Slack App Setup

**For migrate.py:**
1. Go to [Slack API Apps](https://api.slack.com/apps)
2. Create a new app
3. Add OAuth scopes: `channels:manage`, `channels:write`, `groups:write`
4. Install app to workspace and copy Bot User OAuth Token

**For sync_messages.py:**
1. Create or use existing Slack app
2. Enable **Socket Mode** in Settings
3. Generate an app-level token (starts with `xapp-`) with `connections:write` scope
4. Add OAuth scopes: `chat:write`, `users:read`, `channels:history`, `groups:history`
5. Subscribe to bot events: `message.channels`, `message.groups`
6. Install/reinstall app to workspace
7. Copy both the Bot User OAuth Token and App-Level Token

If you'd like, I can add example `.env.example` values or a small CONTRIBUTING section next.
