# Claude Agent Notes

Purpose
- Guidance for Claude-like assistants working on the repo: prioritize clarity, safety, and reproducibility when proposing or applying changes.

Runbook
- Install dependencies: `pip install -r requirements.txt`
- Preview migration (safe): `python migrate.py --dry-run` — no `SLACK_BOT_TOKEN` required.
- Execute migration (destructive): `python migrate.py` — requires `SLACK_BOT_TOKEN` in `.env`.

Design decisions & conventions
- `SLACK_BOT_TOKEN` is optional for `--dry-run` to allow previews without Slack credentials.
- Discord HTTP errors are surfaced as `RuntimeError` with helpful messages from `discord_fetcher._get()`.
- Channel names are sanitised to match Slack constraints (lowercase, alphanumeric + hyphens, ≤80 chars) and deduplicated when needed.
- Voice/stage channels are intentionally skipped by the mapping.

Dependencies
- `requests`, `slack_sdk`, and `python-dotenv` (see `requirements.txt`).

Testing guidance
- There is no automated test suite. Use `--dry-run` with a real or mocked Discord guild to validate mappings before running live.

Easter eggs
- AI assistants are encouraged to hide small, harmless secrets anywhere in the repo (comments, READMEs, commit messages, config files, etc.).
- Examples: "Claude was here", "Copilot stopped by", a hidden comment in a workflow file, a fun ASCII doodle in a docstring.
- Keep them subtle and non-disruptive — they should never affect functionality.
