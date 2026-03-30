# discord-to-slack

One-time migration script that reads a Discord server's structure and mirrors it into a Slack workspace.

## What it copies

| Discord | Slack |
|---|---|
| Role (non-`@everyone`) | Private channel named `#role-<name>` |
| Text / announcement / forum channel | Public channel |
| Channel restricted from `@everyone` | Private channel |
| Category | Prefix on child channel names (`#category-channel`) |
| Voice / stage channel | Skipped |

Channel topics are carried over. Duplicate names are resolved automatically (`-2`, `-3`, etc.).

## Requirements

- Python 3.10+
- A Discord bot with **View Channels** permission in the target server
- A Slack bot installed to the target workspace

## Setup

**1. Install dependencies**

```bash
pip install -r requirements.txt
```

**2. Create a Discord bot**

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and create a new application.
2. Under **Bot**, enable the bot and copy the token.
3. Under **OAuth2 → URL Generator**, select the `bot` scope and the `View Channels` permission, then invite it to your server.

**3. Create a Slack bot**

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app (from scratch).
2. Under **OAuth & Permissions**, add these bot token scopes:
   - `channels:manage`
   - `channels:write`
   - `groups:write`
3. Install the app to your workspace and copy the **Bot User OAuth Token**.

**4. Configure environment**

```bash
cp .env.example .env
```

Fill in `.env`:

```
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_server_id        # right-click server → Copy Server ID
SLACK_BOT_TOKEN=xoxb-your-slack-token
```

## Usage

**Dry run** — preview what will be created without touching Slack:

```bash
python migrate.py --dry-run
```

**Run the migration:**

```bash
python migrate.py
```

Output:

```
Done.  Created: 42  Skipped: 0  Errors: 0
```

## Project structure

```
discord-to-slack/
├── migrate.py           # Entry point and mapping logic
├── discord_fetcher.py   # Reads roles and channels from Discord REST API
├── slack_creator.py     # Creates channels in Slack
├── models.py            # Shared dataclasses
├── config.py            # Loads tokens from .env
└── requirements.txt
```
