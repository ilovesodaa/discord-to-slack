# discord-to-slack — AI Agent Notes

## What this project does
Migrates a Discord server's structure (roles, categories, text/announcement/forum channels) into a Slack workspace as channels.

## Repository layout
```
migrate.py           # CLI entry-point and mapping/orchestration logic
discord_fetcher.py   # Fetches guild structure from Discord API v10
slack_creator.py     # Creates Slack channels via slack_sdk
models.py            # Shared dataclasses
config.py            # Reads .env / environment variables
requirements.txt
.env.example
```

## Environment variables
| Variable | Required for | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Always | Bot token for Discord API |
| `DISCORD_GUILD_ID` | Always | ID of the Discord server to mirror |
| `SLACK_BOT_TOKEN` | Live migration only | Bot token for Slack API |

## How to run
```bash
pip install -r requirements.txt
cp .env.example .env        # edit with real tokens

python migrate.py --dry-run  # preview — Slack token not needed
python migrate.py            # execute migration
```

## Code conventions
- Python 3.10+ (`from __future__ import annotations`, union types with `|`)
- `logging` for all informational output; `print()` only for the final summary table
- All public functions are type-annotated
- HTTP errors from Discord are converted to `RuntimeError` with a clear message in `discord_fetcher._get()`
- `SLACK_BOT_TOKEN` is loaded as `None` during `--dry-run` so that a missing Slack token never blocks a dry-run

## Adding new channel types
1. Add the type constant to `discord_fetcher.py` (e.g. `CHANNEL_TYPE_MEDIA = 16`)
2. Import it in `migrate.py` and add it to the skip-list or include-list in `build_mirror_plan`
3. Document it in the mapping table in `README.md`

## Common pitfalls
- Slack channel names must be ≤80 chars, lowercase, alphanumeric + hyphens only → handled by `_sanitize()`
- Duplicate names across categories are resolved by `_deduplicate()` → `-2`, `-3`, …
- The `deny` field in Discord permission overwrites is a string bitfield integer
