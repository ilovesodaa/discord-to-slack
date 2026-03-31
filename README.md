# discord-to-slack

A migration and synchronization tool for Discord and Slack. It provides two main capabilities:

1. **One-time migration** (`migrate.py`) — Mirrors Discord server structure to Slack
2. **Live message sync** (`sync_messages.py`) — Bidirectional real-time message forwarding

**Migration Highlights**

- Mirrors text, announcement, and forum channels to Slack channels.
- Converts roles into private channels named `#role-<name>` (non-`@everyone`).
- Preserves channel topics and handles duplicate names automatically.
- Skips voice/stage channels — this tool does NOT support voice channels.

**Message Sync Highlights**

- Real-time bidirectional message forwarding between Discord and Slack
- User attribution showing who sent each message and from which platform
- **Automatic channel mapping** — Generated during migration, or manually configured
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

✓ Channel mappings saved to channel_mapping.json
  You can now use sync_messages.py to sync messages between Discord and Slack.
```

The migration script automatically creates `channel_mapping.json` with Discord ↔ Slack channel ID mappings. This file is used by `sync_messages.py` for message forwarding.

### Live Message Sync

**Option 1: Use auto-generated mappings (recommended)**

If you've already run `migrate.py`, the `channel_mapping.json` file is created automatically. Simply start the sync bot:

```bash
python sync_messages.py
```

**Option 2: Manual configuration**

If you didn't use `migrate.py` or want to customize mappings:

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

**Running the sync bot:**

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
4. **For migrate.py:** Enable the following permission:
   - **View Channels** — to read channels and roles from the server
5. **For sync_messages.py:**
   - Under **Privileged Gateway Intents**, enable **Message Content Intent**
   - Enable the following permissions:
     - **Read Message History** — to see messages in channels
     - **Send Messages** — to forward Slack messages into Discord as the bot
     - **Manage Webhooks** — required to create per-channel "Slack Bridge" webhooks so that Slack users' names and avatars appear natively in Discord. Without this permission the bot falls back to plain bot messages.
6. Copy the bot token
7. Invite the bot to your server using the OAuth2 URL Generator:
   - Under **OAuth2 → URL Generator**, select the `bot` scope
   - Tick all the permissions listed above and copy the generated URL

### Slack App Setup

**For migrate.py:**
1. Go to [Slack API Apps](https://api.slack.com/apps)
2. Create a new app (from scratch)
3. Go to **OAuth & Permissions** and add the following **Bot Token Scopes**:
   - `channels:manage` — create and archive public channels
   - `channels:write` — modify public channel settings
   - `groups:write` — create and modify private channels
4. Install app to workspace and copy the **Bot User OAuth Token**

**For sync_messages.py:**
1. Create or use an existing Slack app
2. Enable **Socket Mode** in Settings → Socket Mode
3. Generate an **App-Level Token** (starts with `xapp-`) with the `connections:write` scope
4. Go to **OAuth & Permissions** and add the following **Bot Token Scopes**:
   - `chat:write` — post messages as the bot
   - `users:read` — look up Slack user display names and avatars
   - `channels:history` — read messages in public channels
   - `groups:history` — read messages in private channels
   - `files:read` — download files/attachments so they can be forwarded to Discord (**required for file forwarding**)
5. Go to **Event Subscriptions**, turn it **On**, and subscribe to these bot events:
   - `message.channels` — messages in public channels
   - `message.groups` — messages in private channels
6. Install/reinstall the app to your workspace
7. Copy both the **Bot User OAuth Token** and the **App-Level Token**

## Troubleshooting

- **Slack messages appear in Discord as the bot instead of with the original user's name**: The Discord bot is missing the **Manage Webhooks** permission. Without it, `sync_messages.py` cannot create the per-channel "Slack Bridge" webhooks used to post messages with Slack user names and avatars, and falls back to plain bot messages:
  1. In the [Discord Developer Portal](https://discord.com/developers/applications), open your app
  2. Under **OAuth2 → URL Generator**, regenerate the invite URL with the **Manage Webhooks** permission added
  3. Use the new URL to re-invite / re-authorize the bot to your server

- **Files/attachments not forwarding from Slack to Discord**: If text messages sync correctly but files and images from Slack don't appear in Discord, your Slack bot is missing the `files:read` scope:
  1. Go to your app at https://api.slack.com/apps
  2. Navigate to **OAuth & Permissions**
  3. Under **Scopes** → **Bot Token Scopes**, add `files:read`
  4. **Reinstall the app** to your workspace to apply the new scope
  5. Restart `sync_messages.py`

- **Slack messages not forwarding to Discord**: If the bot connects and Discord → Slack works but Slack → Discord is silent, the Slack app is likely missing event subscriptions. Even with Socket Mode enabled, Slack won't push message events unless the app explicitly subscribes to them:
  1. Go to your app at https://api.slack.com/apps
  2. Open **Event Subscriptions** and make sure it is **On**
  3. Under **Subscribe to bot events**, add `message.channels` (public channels) and `message.groups` (private channels)
  4. Save changes and **reinstall the app** to your workspace

- **Voice support**: This project does NOT support voice channels. If you see warnings like `PyNaCl is not installed` or `davey is not installed`, they pertain only to optional voice features and can be safely ignored.

- **Privileged intents error**: If you see an error mentioning "PrivilegedIntentsRequired" (example: "Shard ID None is requesting privileged intents"), enable the **Message Content Intent** for your bot in the Discord Developer Portal:
  1. Open your app in https://discord.com/developers/applications
  2. Go to **Bot** → **Privileged Gateway Intents**
  3. Enable **Message Content Intent** and save
  4. Reinstall/restart the bot if necessary.

  Alternatively, you can disable requesting message content in `sync_messages.py` (set `intents.message_content = False`) but note that without message content the sync bot cannot read or forward message bodies.
