# discord-to-slack

A small migration helper that reads a Discord server's roles and channels and recreates them in a Slack workspace. It's intended for one-time migrations where you want to preserve channel names, topics, and privacy settings.

**Highlights**

- Mirrors text, announcement, and forum channels to Slack channels.
- Converts roles into private channels named `#role-<name>` (non-`@everyone`).
- Preserves channel topics and handles duplicate names automatically.
- Skips voice/stage channels (not applicable to Slack).

## Requirements

- Python 3.10 or newer
- A Discord bot token with **View Channels** permission for the source guild
- A Slack bot token with channel-management scopes for the destination workspace

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

- `DISCORD_BOT_TOKEN` — Discord bot token
- `DISCORD_GUILD_ID` — Discord server (guild) ID
- `SLACK_BOT_TOKEN` — Bot token for the Slack app (xoxb-...)

## Usage

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

## Files

- `migrate.py` — Entry point and orchestration
- `discord_fetcher.py` — Reads roles & channels from Discord
- `slack_creator.py` — Creates channels and groups in Slack
- `models.py` — Shared dataclasses used across modules
- `config.py` — Loads configuration from environment

## Notes

- The tool resolves duplicate names by appending `-2`, `-3`, etc.
- Channels restricted from `@everyone` are created as private channels in Slack.
- Voice and stage channels are intentionally skipped.

If you'd like, I can add example `.env.example` values or a small CONTRIBUTING section next.
