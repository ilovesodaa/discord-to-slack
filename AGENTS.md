# Agent Notes

Purpose
- Short guidance for assistant agents working on this repo: run, extend, and maintain the Discord→Slack migration tool safely and predictably.

Key points for agents
- Prefer non-destructive operations and `--dry-run` workflows when interacting with live APIs.
- Use `config.get_*` helpers for environment values and never hardcode secrets.
- When making changes that affect Slack/Discord, clearly document the risk and require user confirmation.

Quick run
- Install: `pip install -r requirements.txt`
- Preview: `python migrate.py --dry-run`
- Execute: `python migrate.py` (requires `SLACK_BOT_TOKEN` set)

Important files
- `migrate.py` — orchestrator and plan builder
- `discord_fetcher.py` — Discord REST client and snapshot model
- `slack_creator.py` — Slack Web API client wrappers and channel creation logic
- `models.py` — dataclass definitions
- `config.py` — `.env` loader and `get_*` helpers

Conventions
- Python 3.10+ features are used (annotations, `|` unions).
- Use `logging` consistently; reserve `print()` for final user-facing summary tables.

Adding channel types
- Add constants in `discord_fetcher.py`, update `build_mirror_plan()` in `migrate.py`, and document changes in `README.md`.

Safety notes
- Slack channel names: lowercase, alphanumeric and hyphens, ≤80 chars — sanitised by `_sanitize()`.
- Preserve existing rate-limit handling in `slack_creator.py` when adding retries.
