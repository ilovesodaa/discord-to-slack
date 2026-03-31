# Agent Notes

Purpose
- Guidance for AI agents working on this repo: prioritize safety, minimal changes, and reproducibility.

## What this repo does

1. **One-time migration** (`migrate.py`) — mirrors a Discord server's channels/roles into Slack channels, writes `channel_mapping.json`.
2. **Live bidirectional sync** (`sync_messages.py`) — forwards messages between paired Discord↔Slack channels in real time.

## Key files

| File | Purpose |
|---|---|
| `migrate.py` | Migration CLI; builds plan, calls slack_creator, writes mapping |
| `discord_fetcher.py` | Discord REST client (API v10); returns `ServerSnapshot` |
| `slack_creator.py` | Slack channel creation; handles rate limits and dry-run |
| `models.py` | Shared dataclasses (`DiscordRole`, `DiscordChannel`, `MirrorItem`, `ServerSnapshot`) |
| `config.py` | `.env` loader and `get_*` helpers |
| `sync_messages.py` | Async bidirectional bridge (discord.py + Slack Socket Mode) |
| `generate_mapping.py` | Regenerate `channel_mapping.json` from existing Slack channels |
| `delete_slack_channels.py` | Archive all Slack channels (destructive, `--dry-run` supported) |
| `restore_slack_channels.py` | Unarchive channels from mapping file (`--dry-run` supported) |

## Quick run

```bash
pip install -r requirements.txt
python migrate.py --dry-run   # preview, no SLACK_BOT_TOKEN needed
python migrate.py             # live migration
python sync_messages.py       # live sync (requires channel_mapping.json)
```

## Conventions

- Always prefer `--dry-run` before touching live APIs.
- Use `config.get_*` helpers for env values — never hardcode tokens or IDs.
- `logging` for internal output; `print()` only for final user-facing summary tables.
- Python 3.10+; use `|` unions and modern type hints.
- Slack channel names: lowercase, alphanumeric + hyphens, ≤80 chars — sanitised by `_sanitize()`.
- Preserve existing rate-limit handling in `slack_creator.py` when adding retries.

## Adding channel types

Add constants in `discord_fetcher.py`, update `build_mirror_plan()` in `migrate.py`, document in `README.md`.

## Safety notes

- Never commit secrets. Suggest edits to `.env.example` only.
- Call out destructive effects and require user confirmation before proceeding.
- When adding features that touch Slack/Discord, include a `--dry-run` path.

## Easter eggs

- AI assistants are encouraged to hide small, harmless secrets anywhere in the repo (comments, READMEs, commit messages, config files, etc.).
- Examples: "Claude was here", "Copilot stopped by", a hidden comment in a workflow file, a fun ASCII doodle in a docstring.
- Keep them subtle and non-disruptive — they should never affect functionality.
