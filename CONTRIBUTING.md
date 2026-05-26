# Contributing

Thank you for considering a contribution!

## Ground rules

- Keep the plugin **stdlib-only** — no `pip install` required for end users. CI enforces this.
- Keep the plugin **read-only with respect to credentials and session files** — it must
  never write to Keychain entries, Claude Code JSONL files, or any file other than the
  last-known-good cache at `~/.cache/claude-swiftbar/last_good.json`.
- All externally-derived text (model names, API error strings, exception messages) must
  pass through `safe_menu_text()` before appearing in menu output.
- All new logic should have corresponding tests in `tests/test_plugin.py`.
- Run the full check suite before opening a PR (see below).

## Development setup

```bash
git clone https://github.com/jdetras/claude-swiftbar.git
cd claude-swiftbar
pip install pytest ruff mypy bandit  # Python 3.9.6+ required
```

## Running checks locally

```bash
# Tests
python -m pytest tests/ -v

# Lint + format
ruff check plugins/ tests/
ruff format plugins/ tests/

# Type check
mypy plugins/claude-usage.5m.py --ignore-missing-imports --strict

# Security scan
bandit -r plugins/ -ll -ii
```

All four must pass before CI will go green.

## Submitting a PR

1. Fork → feature branch → commit
2. Add or update tests for any changed behaviour
3. Run the checks above
4. Open a PR with a clear description of what changed and why
5. Reference any related issues with `Fixes #123`

## Reporting bugs

Open a GitHub issue and include:
- macOS version (`sw_vers -productVersion`)
- Python version (`python3 --version`)
- SwiftBar / xbar version
- The full text of the menu-bar dropdown when the bug occurs
- Steps to reproduce

Do **not** include your Anthropic access token or any credentials in issue reports.

## Security vulnerabilities

Please follow the process in [SECURITY.md](SECURITY.md) — do not open a public issue.
