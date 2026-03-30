# discord-to-slack — Claude AI Notes

## Project overview
One-time migration script that reads a Discord server's structure (roles + channels) and mirrors it into a Slack workspace. Written in Python 3.10+.

## Repository layout
```
migrate.py           # Entry-point: arg parsing, orchestration, mapping logic
discord_fetcher.py   # Reads guild structure from Discord REST API (v10)
slack_creator.py     # Creates channels in Slack via slack_sdk
models.py            # Shared dataclasses: DiscordRole, DiscordChannel, MirrorItem, ServerSnapshot
config.py            # Loads DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, SLACK_BOT_TOKEN from .env
requirements.txt     # Runtime dependencies
.env.example         # Template for required environment variables
```

## Running the project
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in tokens

# Dry-run (Discord token + guild ID required; Slack token NOT required)
python migrate.py --dry-run

# Live migration (all three tokens required)
python migrate.py
```

## Key design decisions
- `SLACK_BOT_TOKEN` is **not** required for `--dry-run`; only Discord credentials are loaded.
- Discord HTTP errors are caught in `discord_fetcher._get()` and re-raised as `RuntimeError` with a human-friendly message.
- Channel names are sanitised (lowercase, alphanumeric + hyphens), capped at 80 chars, and de-duplicated with a `-2`, `-3`, … suffix.
- Voice and stage channels are skipped; all other channel types are mapped.

## Dependencies
| Package | Purpose |
|---|---|
| `requests` | HTTP calls to Discord REST API |
| `slack_sdk` | Slack Web API client |
| `python-dotenv` | Loads `.env` file |

## Testing
There is no automated test suite yet. Run `python migrate.py --dry-run` against a real (or mocked) Discord guild to validate changes.
