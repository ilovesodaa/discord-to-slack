# discord-to-slack — GitHub Copilot Notes

## Project overview
Python CLI that reads a Discord server's roles and channels via the Discord REST API (v10) and recreates the structure as Slack channels.

## File map
| File | Responsibility |
|---|---|
| `migrate.py` | CLI entry-point, `build_mirror_plan()`, orchestration |
| `discord_fetcher.py` | `fetch_server()` — REST calls to Discord, returns `ServerSnapshot` |
| `slack_creator.py` | `apply_plan()` — creates channels in Slack, handles rate limits |
| `models.py` | `DiscordRole`, `DiscordChannel`, `MirrorItem`, `ServerSnapshot` |
| `config.py` | `get_discord_bot_token()`, `get_discord_guild_id()`, `get_slack_bot_token()` |

## Key behaviours
- `--dry-run` flag: prints a plan table; **does not** require `SLACK_BOT_TOKEN`
- Discord HTTP/network errors surface as `RuntimeError` with a user-friendly message (caught in `main()`)
- Channel names: sanitised to `[a-z0-9-]`, max 80 chars, deduplicated with numeric suffixes
- Voice/stage channels are skipped; categories become name prefixes

## Environment setup
```
DISCORD_BOT_TOKEN=...   # always required
DISCORD_GUILD_ID=...    # always required
SLACK_BOT_TOKEN=...     # required only for live migration (not --dry-run)
```

## Quick start
```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env, then:
python migrate.py --dry-run
python migrate.py
```

## Extending the mapping
- New channel types: add constant to `discord_fetcher.py`, update skip/include logic in `migrate.py:build_mirror_plan()`
- New Slack operations: extend `slack_creator.py` and `models.MirrorItem`
- All public APIs are fully type-annotated (Python 3.10+ union syntax)
