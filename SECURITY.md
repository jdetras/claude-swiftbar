# Security Policy

## Supported versions

Only the latest release on the `main` branch receives security fixes.

## Security posture

### What the plugin accesses

| Resource | How | Purpose |
|---|---|---|
| macOS Keychain (`Claude Code-credentials`) | `/usr/bin/security find-generic-password` | Read OAuth access token |
| `~/.claude/projects/**/*.jsonl` | Local file read (read-only) | Context window token counts |
| `api.anthropic.com/api/oauth/usage` | HTTPS GET | Rate-limit utilisation data |
| `~/.cache/claude-swiftbar/last_good.json` | Local file write | Last-known-good usage cache |

### What the plugin never does

- Never reads the Keychain for any service other than `Claude Code-credentials`
- Never writes to credentials or Claude Code session files
- Never transmits JSONL session contents — only token counts are used locally
- Never contacts any host other than `api.anthropic.com`
- Never uses third-party libraries (zero non-stdlib imports, enforced by CI)
- Never logs or prints the access token

### The one file the plugin writes

The plugin writes a single cache file:

```
~/.cache/claude-swiftbar/last_good.json
```

This file contains **only** API usage percentages and reset timestamps — never
tokens, credentials, or session content. It is written atomically (temp file →
rename) with permissions `0600`. The cache directory is created with
permissions `0700`. Symlinks at either path are refused.

### Credential handling

The OAuth token is:
1. Read from the macOS Keychain via `/usr/bin/security` — the same mechanism Claude Code uses. `shell=False` is enforced; no shell interpolation occurs.
2. Held in memory only for the duration of the single HTTPS request
3. Immediately garbage-collected after the request completes
4. Never stored, cached, logged, or written anywhere

### Output sanitization

All text derived from external sources (JSONL session files, API error strings,
exception messages) is passed through `safe_menu_text()` before being written
to xbar/SwiftBar output. This function:

- Removes non-printable and control characters
- Collapses newlines and carriage returns to a space
- Replaces the pipe character `|` with `｜` (U+FF5C) to prevent xbar parameter injection
- Truncates to a maximum of 120 characters

### Network

All network traffic goes to `api.anthropic.com` over HTTPS (TLS 1.2+). The only
data sent is the `Authorization: Bearer <token>` header. The response contains
only usage percentages and reset timestamps — no personal data.

### JSONL session files

These files are read locally from `~/.claude/projects/`. Only numeric token-count
fields (`input_tokens`, `output_tokens`, `cache_*_tokens`) and the `model` field
are used. Message content, tool calls, and all other fields are ignored. Nothing
from these files is transmitted. Files larger than 50 MB or longer than 50,000
lines are skipped entirely.

### SwiftBar execution context

SwiftBar runs the plugin as your normal user account — no elevated privileges.
The `security` command may prompt macOS for Keychain access on first run; you
may see a "claude-usage wants to use your confidential information" dialog.
Click **Always Allow** to permit it going forward.

### What the installer does and does not do

The installer (`install.sh`):

- Sets a safe `PATH` and uses absolute paths for all system tools
- Downloads **nothing** — installs only the local plugin file from this repository
- Only **checks** for the presence of the Keychain entry; never reads or prints its contents
- Validates the GitHub username from `git config` against a strict regex before
  inserting it into `sed` substitutions
- Creates a temporary patched plugin via `mktemp`, cleaned up by a `trap` on exit
- Installs the plugin atomically (temp file → rename) into the plugins directory
- Refuses to overwrite a symlink at the destination path
- Does not request `sudo`, modify shell profiles, or install any software

### Static analysis

Every push runs:
- `ruff` — style and bug linting
- `mypy` — strict type checking
- `bandit` — Python security linting (level `ll`, `ii` — medium+ severity)
- A custom CI step that asserts zero third-party imports
- A secret-scan step that greps for common token prefixes (`sk-ant-`, etc.)

## Threat model

| Threat | Mitigation |
|---|---|
| Token exfiltration via modified script | Verify SHA-256 after install (printed by installer); pin to a specific commit |
| Supply-chain attack via dependencies | Zero non-stdlib imports, enforced by CI |
| Keychain token exposed in logs | Token never printed; SwiftBar output sanitized via `safe_menu_text()` |
| xbar parameter injection via JSONL content | Pipe character replaced in all externally-derived strings |
| MITM of API call | Standard HTTPS / TLS; `api.anthropic.com` only |
| Malicious JSONL content | Only numeric fields and model string consumed; size + line limits enforced |
| Cache file symlink attack | Symlinks rejected at both cache file and cache directory level |
| Command injection in installer | GitHub username validated against strict regex; absolute tool paths used |
| Temp file hijacking in installer | `mktemp` used; `trap` cleans up on any exit |
| Partial file visible to readers | Atomic rename used for both plugin install and cache writes |

### Verifying the plugin file

The installer prints the SHA-256 of both the source and installed plugin.
You can also verify manually:

```bash
shasum -a 256 plugins/claude-usage.5m.py
# Compare against the hash published in the GitHub release notes
```

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Email the address listed in this repository's GitHub profile (or open a private
security advisory via GitHub) with:
- A description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive an acknowledgement within 48 hours and a fix or mitigation plan
within 7 days.

We follow coordinated disclosure: we will credit you in the release notes unless
you prefer to remain anonymous.
