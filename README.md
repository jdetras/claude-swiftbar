# claude-swiftbar

A macOS SwiftBar / xbar menu bar plugin that shows your **Claude Code** session
and weekly rate-limit usage, live context-window fill, and a smart **compact /
clear** recommendation — all in one glance.

![Python 3.9.6+](https://img.shields.io/badge/python-3.9%2B-blue)
![stdlib only](https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

```
🤖 42% · 68k                         ← menu bar: session % · context tokens
────────────────────────────────
🟡 Context filling up — /compact soon
────────────────────────────────
Context · Sonnet 4.6
████████████░░░░  68k / 200k  (34%)
Session I/O: ↑340k ↓28k

Session · 5h window
████████░░░░░░░░  42%
Resets in 2h 18m

Weekly · 7d window
███░░░░░░░░░░░░░  18%
Resets in 4d 6h
  Opus:   0%
  Sonnet: 18%
────────────────────────────────
Updated 10:42
Refresh
Open Claude.ai
```

## Features

| | |
|---|---|
| **Context window** | Live token count from your most recent JSONL session, shown as a fill bar against the 200 k limit |
| **Session (5 h)** | Current rate-limit utilisation for the rolling 5-hour window |
| **Weekly (7 d)** | Rolling 7-day utilisation with per-model breakdown (Opus / Sonnet) |
| **Recommendation** | 🟢🟡🟠🔴🚨 advice line — tells you exactly when to `/compact` or `/clear` |
| **Extra usage** | Shows credit balance if your plan has an overage allowance |
| **Zero dependencies** | Pure Python 3 stdlib — nothing to `pip install` |

## Requirements

- macOS 13 or later
- [SwiftBar](https://swiftbar.app) or [xbar](https://xbarapp.com) (`brew install swiftbar`)
- Python 3.9.6+ (`python3 --version`)
- [Claude Code](https://code.claude.com) installed and logged in

> **Plan requirement:** The 5-hour and 7-day utilisation data is only returned
> for Claude **Pro**, **Max**, **Team**, and **Enterprise** subscriptions.

## Installation

```bash
# 1. Clone
git clone https://github.com/jdetras/claude-swiftbar.git
cd claude-swiftbar

# 2. Run the installer — reads your git config automatically
bash install.sh
```

The installer will:
1. Check Python 3.9.6+ is available
2. Read and validate your GitHub username from `git config` and patch the plugin
3. Print the SHA-256 of the source and installed plugin for verification
4. Only check whether your Claude Code Keychain entry exists (never reads it)
5. Detect your SwiftBar / xbar plugins folder automatically
6. Install the plugin atomically and make it executable
7. Prompt SwiftBar to reload

The installer downloads nothing. It installs only the local plugin file from
this repository.

## How it works

```
macOS Keychain  ──►  access token  ──►  api.anthropic.com/api/oauth/usage
                                                      │
                                              rate-limit JSON
                                                      │
~/.claude/projects/**/*.jsonl  ──►  context tokens ──┘
                                                      │
                                             SwiftBar output
                                                      │
                          ~/.cache/claude-swiftbar/last_good.json
                          (last-known-good cache — usage % only, never tokens)
```

1. **Credentials** are read from the macOS Keychain via `/usr/bin/security`.
   No token ever touches the filesystem or logs.
2. **Rate-limit data** is fetched from the Anthropic OAuth usage endpoint over HTTPS.
   On HTTP 429, the `Retry-After` header is respected and the API is not called
   again until the backoff window expires.
3. **Context window** is calculated by parsing the most-recently-modified JSONL
   session file in `~/.claude/projects/` (max 50 MB / 50,000 lines). No file
   contents leave your machine.
4. A **recommendation** is computed locally from both signals.
5. If the API call fails, **cached data** from the last successful poll is shown
   with a staleness warning. The cache contains only usage percentages and reset
   timestamps — never tokens or session content.

## Security

See [SECURITY.md](SECURITY.md) for the full security posture, threat model,
and responsible disclosure policy.

Quick summary:

- ✅ Credentials read from Keychain only — via `/usr/bin/security`, never a file
- ✅ Token transmitted only to `api.anthropic.com` over TLS 1.2+
- ✅ JSONL session files read locally; no content transmitted; size limits enforced
- ✅ All externally-derived text sanitized before menu output (pipe injection prevention)
- ✅ Cache written atomically with `0600` permissions; symlinks refused
- ✅ Installer uses absolute tool paths, validates git username before `sed`, and cleans up temp files via `trap`
- ✅ Zero third-party dependencies (enforced by CI)
- ✅ Static analysis with `ruff` + `mypy` + `bandit` on every push

## Customisation

| What | How |
|---|---|
| Refresh interval | Rename the file, e.g. `claude-usage.2m.py` (2 min). The default `5m` is recommended — polling faster risks HTTP 429. |
| Bar width | Edit the `width=16` default in the `bar()` call |
| Recommendation thresholds | Edit the `recommend()` function |
| JSONL size limit | Edit `MAX_JSONL_BYTES` and `MAX_JSONL_LINES` constants |

## Troubleshooting

**`🤖 --` / "Keychain read failed"**

```bash
# Verify the Keychain entry exists (presence check only — does not print contents)
/usr/bin/security find-generic-password -s "Claude Code-credentials" -l
```

If the entry is absent: `claude auth login`

**`⚠ rate limited (retry in Nm)` in the dropdown**

The Anthropic usage API returned HTTP 429. The plugin respects the `Retry-After`
header and will resume polling automatically. Polling every 5 minutes (the
default filename interval) is the recommended rate.

**No context window data shown**

Claude Code session files live in `~/.claude/projects/`. Start a Claude Code
session and the data will appear on the next refresh.

**`python3` not found**

```bash
brew install python   # or: xcode-select --install
```

## Development

```bash
# install dev tools
pip install pytest ruff mypy bandit

# run tests
python -m pytest tests/ -v

# lint + format
ruff check plugins/ tests/
ruff format plugins/ tests/

# type check
mypy plugins/claude-usage.5m.py --ignore-missing-imports --strict

# security scan
bandit -r plugins/ -ll -ii
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) — © 2026 jdetras
