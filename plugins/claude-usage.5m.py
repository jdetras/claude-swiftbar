#!/usr/bin/env python3
# <xbar.title>Claude Code Usage</xbar.title>
# <xbar.version>v4.0.0</xbar.version>
# <xbar.author>jdetras</xbar.author>
# <xbar.desc>Claude Code usage: context window, rate-limit, compact/clear advice.</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <xbar.refreshOnWake>true</xbar.refreshOnWake>
#
# Security notes
# ──────────────
# • Credentials are read exclusively from the macOS Keychain service
#   "Claude Code-credentials" using /usr/bin/security. No token is ever
#   written to disk or logged.
# • All network calls use HTTPS and target api.anthropic.com only.
# • Claude Code session JSONL files are read locally; no content is
#   transmitted. The script never writes to, renames, or deletes them.
# • The only file this script writes is a last-known-good usage cache at:
#     ~/.cache/claude-swiftbar/last_good.json
#   That file contains only API usage percentages and reset timestamps —
#   never tokens, credentials, or session content.
# • Zero third-party dependencies — stdlib only.

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── constants ─────────────────────────────────────────────────────────────────

MODEL_CONTEXT_LIMIT = 200_000
USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_API_BETA = "oauth-2025-04-20"
PROJECTS_GLOB = os.path.expanduser("~/.claude/projects/**/*.jsonl")
SECURITY_BIN = "/usr/bin/security"

# Cache — last-known-good usage data only; never contains tokens
CACHE_PATH = Path.home() / ".cache" / "claude-swiftbar" / "last_good.json"

RETRY_ATTEMPTS = 2
RETRY_DELAY = 2  # seconds between retries

# JSONL guard rails
MAX_JSONL_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_JSONL_LINES = 50_000

# Retry-After clamp (seconds)
RETRY_AFTER_MIN = 60
RETRY_AFTER_MAX = 3_600
RETRY_AFTER_DEFAULT = 600

# Menu text sanitization
MENU_TEXT_MAX_LEN = 120

MODEL_LABELS = [
    ("opus-4-6", "Opus 4.6"),
    ("opus-4", "Opus 4"),
    ("sonnet-4-6", "Sonnet 4.6"),
    ("sonnet-4-5", "Sonnet 4.5"),
    ("sonnet-4", "Sonnet 4"),
    ("haiku-4-5", "Haiku 4.5"),
    ("haiku-4", "Haiku 4"),
    ("haiku", "Haiku"),
]

# ── output sanitization ───────────────────────────────────────────────────────


def safe_menu_text(value: object, max_len: int = MENU_TEXT_MAX_LEN) -> str:
    """
    Sanitize untrusted text before writing it to xbar/SwiftBar output.

    Prevents:
    - xbar parameter injection via the pipe character '|'
    - Multi-line output breaking menu item layout (newlines, CR)
    - Non-printable / control characters corrupting the menu
    - Excessively long strings overflowing the menu

    Safe to call on any value — always returns a plain str.
    """
    text = str(value)
    # strip non-printable and control characters (keep normal unicode)
    text = "".join(
        ch
        for ch in text
        if unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
        or ch in ("\t",)  # keep tab; strip everything else in C* categories
    )
    # collapse newlines / carriage returns to a space
    text = re.sub(r"[\r\n]+", " ", text)
    # replace pipe to prevent xbar parameter injection
    text = text.replace("|", "｜")  # U+FF5C FULLWIDTH VERTICAL LINE — visually identical
    # truncate
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text.strip()


# ── cache helpers ─────────────────────────────────────────────────────────────


def _cache_path_safe() -> Path | None:
    """
    Return CACHE_PATH only if neither the file nor its parent directory
    is a symlink. Symlinks could redirect writes to unintended locations.
    """
    parent = CACHE_PATH.parent
    if parent.is_symlink():
        return None
    if CACHE_PATH.exists() and CACHE_PATH.is_symlink():
        return None
    return CACHE_PATH


def load_cache() -> dict | None:
    path = _cache_path_safe()
    if path is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def save_cache(data: dict) -> None:
    path = _cache_path_safe()
    if path is None:
        return
    try:
        parent = path.parent
        # create dir with 0700 — no group or world access
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o700)

        payload = json.dumps(data)

        # atomic write: temp file → os.replace (rename) into place
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".cache_tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.chmod(tmp, 0o600)  # 0600 before it becomes visible at final path
            os.replace(tmp, path)
        except Exception:
            # clean up the temp file if the replace fails
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:  # noqa: S110
        pass  # cache write failures are non-fatal


# ── credentials ───────────────────────────────────────────────────────────────


def load_token() -> str | None:
    """
    Read the Claude Code OAuth access token from the macOS Keychain using
    the absolute path /usr/bin/security. shell=False is enforced by passing
    a list. stderr is suppressed. Token is never written to disk or logged.
    """
    for attempt in range(RETRY_ATTEMPTS):
        try:
            raw = (
                subprocess.check_output(
                    [SECURITY_BIN, "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    timeout=5,
                )
                .decode()
                .strip()
            )
            token = json.loads(raw).get("claudeAiOauth", {}).get("accessToken")
            if token:
                return token
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ):
            pass
        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(RETRY_DELAY)
    return None


# ── usage API ─────────────────────────────────────────────────────────────────


def _parse_retry_after(headers: object) -> int:
    """
    Parse the Retry-After header value robustly.
    Returns a value clamped to [RETRY_AFTER_MIN, RETRY_AFTER_MAX].
    Falls back to RETRY_AFTER_DEFAULT on any parse failure.
    """
    try:
        raw = headers.get("Retry-After")  # type: ignore[union-attr]
        if raw is None:
            return RETRY_AFTER_DEFAULT
        value = int(str(raw).strip())
        if value < 0:
            return RETRY_AFTER_DEFAULT
        return max(RETRY_AFTER_MIN, min(RETRY_AFTER_MAX, value))
    except (ValueError, TypeError, AttributeError):
        return RETRY_AFTER_DEFAULT


def fetch_usage(token: str) -> tuple[dict | None, str | None]:
    """
    Call the Anthropic OAuth usage endpoint with retry.
    Returns (data, error_string). On success error_string is None.
    On 429, stores a bounded backoff deadline in the cache.
    Token is sent only to api.anthropic.com over HTTPS; never logged.
    """
    last_err: str | None = None
    for attempt in range(RETRY_ATTEMPTS):
        req = urllib.request.Request(
            USAGE_API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": USAGE_API_BETA,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                return json.loads(resp.read()), None

        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = _parse_retry_after(exc.headers)
                backoff_until = time.time() + retry_after
                cached = load_cache() or {}
                cached["backoff_until"] = backoff_until
                save_cache(cached)
                mins = round(retry_after / 60)
                return None, f"rate limited (retry in {mins}m)"
            last_err = f"HTTP {exc.code}"
            if 400 <= exc.code < 500:
                break  # 4xx won't be fixed by retrying

        except urllib.error.URLError as exc:
            last_err = safe_menu_text(f"Network: {exc.reason}", max_len=60)
        except Exception as exc:  # noqa: BLE001
            last_err = safe_menu_text(str(exc), max_len=60)

        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(RETRY_DELAY)

    return None, last_err


# ── JSONL session parsing ─────────────────────────────────────────────────────


def friendly_model(raw: str) -> str:
    """Map a raw model string to a short display label. Input is sanitized."""
    sanitized = safe_menu_text(raw, max_len=60)
    for fragment, label in MODEL_LABELS:
        if fragment in sanitized:
            return label
    return sanitized or "unknown"


def parse_latest_session() -> dict | None:
    """
    Find the most-recently-modified Claude Code JSONL session file and extract
    context window and token usage data.

    Hardening:
    - Skips files larger than MAX_JSONL_BYTES (50 MB).
    - Stops after MAX_JSONL_LINES lines to bound refresh-time work.
    - Handles permission errors, missing files, and file races gracefully.
    - Reads locally only — no content is transmitted.
    - Only numeric token fields and the model name are consumed.
    """
    try:
        files = glob.glob(PROJECTS_GLOB, recursive=True)
    except Exception:
        return None

    if not files:
        return None

    try:
        latest = max(files, key=os.path.getmtime)
    except (ValueError, OSError):
        return None

    # size guard — skip files that are implausibly large
    try:
        if os.path.getsize(latest) > MAX_JSONL_BYTES:
            return None
    except OSError:
        return None

    last_input = last_cache_read = last_cache_create = 0
    total_input = total_output = 0
    last_model: str | None = None
    had_compaction = False
    line_count = 0

    try:
        with open(latest, encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line_count += 1
                if line_count > MAX_JSONL_LINES:
                    break  # bound parsing time

                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(entry, dict):
                    continue

                entry_type = entry.get("type", "")

                if entry_type == "system" and entry.get("subtype") == "compacted":
                    had_compaction = True

                if entry_type != "assistant":
                    continue

                msg = entry.get("message")
                if not isinstance(msg, dict):
                    continue

                usage = msg.get("usage")
                if isinstance(usage, dict):
                    # coerce to int defensively — malformed JSONL could have strings
                    def _int(v: object) -> int:
                        try:
                            return max(0, int(v))  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            return 0

                    last_input = _int(usage.get("input_tokens"))
                    last_cache_read = _int(usage.get("cache_read_input_tokens"))
                    last_cache_create = _int(usage.get("cache_creation_input_tokens"))
                    total_input += last_input
                    total_output += _int(usage.get("output_tokens"))

                model_raw = msg.get("model")
                if isinstance(model_raw, str) and model_raw:
                    last_model = model_raw  # sanitized later via friendly_model()

    except OSError:
        return None

    return {
        "context_tokens": last_input + last_cache_read + last_cache_create,
        "model": friendly_model(last_model or ""),
        "session_input": total_input,
        "session_output": total_output,
        "had_compaction": had_compaction,
    }


# ── formatting helpers ────────────────────────────────────────────────────────


def fmt_reset(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        secs = max(0, int((t - datetime.now(timezone.utc)).total_seconds()))
        mins, _ = divmod(secs, 60)
        hours, m = divmod(mins, 60)
        days, h = divmod(hours, 24)
        if days:
            return f"{days}d {h}h"
        if hours:
            return f"{h}h {m}m"
        return f"{m}m"
    except (ValueError, OverflowError, TypeError):
        return "?"


def bar(pct: int | None, width: int = 16) -> str:
    p = max(0, min(100, pct or 0))
    filled = round(width * p / 100)
    return "█" * filled + "░" * (width - filled)


def pct_color(pct: int | None) -> str:
    if pct is None:
        return "gray"
    if pct >= 85:
        return "#FF4A4A"
    if pct >= 60:
        return "#FF9500"
    return "#30D158"


def fmt_k(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1000)}k"
    return str(n)


def recommend(s_pct: int | None, ctx_pct: int | None, had_compaction: bool) -> tuple[str, str]:
    s = s_pct or 0
    ctx = ctx_pct or 0
    if s >= 95:
        return "🚨", "Rate limit ceiling — stop and wait for reset"
    if s >= 85 and ctx >= 75:
        return "🔴", "/clear now — high rate limit + full context"
    if ctx >= 85:
        return "🔴", "Context almost full — /compact or /clear now"
    if s >= 80:
        return "🟠", "Rate limit high — finish task, then /clear"
    if ctx >= 65:
        if had_compaction:
            return "🟡", "Context filling again after compaction — consider /clear"
        return "🟡", "Context filling up — /compact soon to preserve state"
    if s >= 60:
        return "🟡", "Session past halfway — pace yourself"
    return "🟢", "All good — keep going"


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    token = load_token()
    if not token:
        cached = load_cache()
        print("🤖 -- | color=gray")
        print("---")
        print("Keychain read failed | color=#FF4A4A size=12")
        print("Install and log in to Claude Code first | size=11 color=gray")
        if cached and isinstance(cached.get("data"), dict):
            print("---")
            print("Last known values (stale) | size=11 color=gray")
            _print_body(cached, stale=True)
        return

    # skip API call if inside a 429 backoff window
    cached_pre = load_cache()
    backoff_until = float((cached_pre or {}).get("backoff_until", 0))
    if time.time() < backoff_until:
        remaining = round((backoff_until - time.time()) / 60)
        data, api_err = None, f"rate limited (retry in {remaining}m)"
    else:
        data, api_err = fetch_usage(token)

    session = parse_latest_session()

    if data is not None:
        save_cache({"data": data, "ts": datetime.now().isoformat()})
        _print_all(data, session, api_err=None)
    else:
        cached = load_cache()
        if cached and isinstance(cached.get("data"), dict):
            _print_all(cached["data"], session, api_err=api_err, stale_ts=cached.get("ts", ""))
        else:
            print("🤖 -- | color=gray")
            print("---")
            err_display = safe_menu_text(api_err or "unknown error", max_len=60)
            print(f"API error: {err_display} | color=#FF4A4A size=12")
            print("Will retry on next refresh | size=11 color=gray")


def _pct(d: dict, key: str = "utilization") -> int | None:
    v = d.get(key)
    try:
        return round(float(v)) if v is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _print_all(
    data: dict,
    session: dict | None,
    *,
    api_err: str | None,
    stale_ts: str = "",
) -> None:
    fh = data.get("five_hour") or {}
    wk = data.get("seven_day") or {}
    ops = data.get("seven_day_opus") or {}
    son = data.get("seven_day_sonnet") or {}
    ext = data.get("extra_usage") or {}

    s_pct = _pct(fh)
    w_pct = _pct(wk)
    op_pct = _pct(ops)
    sn_pct = _pct(son)

    ctx_tokens = (session or {}).get("context_tokens")
    ctx_pct = round(ctx_tokens / MODEL_CONTEXT_LIMIT * 100) if ctx_tokens else None

    # menu bar label — all values are numeric/formatted; no sanitization needed
    parts: list[str] = []
    if s_pct is not None:
        parts.append(f"{s_pct}%")
    if ctx_tokens:
        parts.append(fmt_k(ctx_tokens))
    label = " · ".join(parts) or "--"
    peak = max(v for v in [s_pct, ctx_pct, 0] if v is not None)
    stale_marker = " ·" if stale_ts else ""
    print(f"🤖 {label}{stale_marker} | color={pct_color(peak)}")
    print("---")

    if stale_ts:
        try:
            dt = datetime.fromisoformat(str(stale_ts))
            age = datetime.now() - dt
            mins = int(age.total_seconds() / 60)
            age_str = f"{mins}m ago" if mins < 60 else f"{mins // 60}h ago"
        except Exception:
            age_str = "unknown"
        if api_err:
            err_display = safe_menu_text(api_err, max_len=60)
            stale_line = f"⚠ API error ({err_display}) — showing cached data from {age_str}"
            print(f"{stale_line} | size=12 color=#FF9500")
        else:
            print(f"⚠ Showing cached data from {age_str} | size=12 color=#FF9500")
        print("---")

    _print_body(
        {
            "data": data,
            "session": session,
            "s_pct": s_pct,
            "w_pct": w_pct,
            "op_pct": op_pct,
            "sn_pct": sn_pct,
            "ctx_pct": ctx_pct,
            "ctx_tokens": ctx_tokens,
            "fh": fh,
            "wk": wk,
            "ext": ext,
        }
    )


def _print_body(state: dict, stale: bool = False) -> None:
    if "data" in state and "s_pct" not in state:
        data = state.get("data") or {}
        session = None
        fh = data.get("five_hour") or {}
        wk = data.get("seven_day") or {}
        ops = data.get("seven_day_opus") or {}
        son = data.get("seven_day_sonnet") or {}
        ext = data.get("extra_usage") or {}
        s_pct = _pct(fh)
        w_pct = _pct(wk)
        op_pct = _pct(ops)
        sn_pct = _pct(son)
        ctx_pct = ctx_tokens = None
    else:
        session = state.get("session")
        s_pct = state.get("s_pct")
        w_pct = state.get("w_pct")
        op_pct = state.get("op_pct")
        sn_pct = state.get("sn_pct")
        ctx_pct = state.get("ctx_pct")
        ctx_tokens = state.get("ctx_tokens")
        fh = state.get("fh") or {}
        wk = state.get("wk") or {}
        ext = state.get("ext") or {}

    had_compact = bool((session or {}).get("had_compaction", False))

    ico, advice = recommend(s_pct, ctx_pct, had_compact)
    print(f"{ico} {advice} | size=13")
    print("---")

    if session:
        ctx_pct_d = ctx_pct or 0
        compact_note = "  · compacted" if had_compact else ""
        # model comes through friendly_model() which already sanitizes
        model_label = safe_menu_text(session.get("model", "unknown"), max_len=30)
        print(f"Context · {model_label}{compact_note} | size=13 color=#888888")
        print(
            f"{bar(ctx_pct_d)} "
            f"{fmt_k(ctx_tokens or 0)} / 200k  ({ctx_pct_d}%)"
            f" | font=Menlo size=12 color={pct_color(ctx_pct)}"
        )
        s_in = fmt_k(session.get("session_input") or 0)
        s_out = fmt_k(session.get("session_output") or 0)
        print(f"Session I/O: ↑{s_in} ↓{s_out} | size=11 color=gray")
    else:
        print("Context window: no active session | size=12 color=gray")
        print("Start a Claude Code session to see context data | size=11 color=gray")
    print("---")

    if s_pct is not None:
        print("Session · 5h window | size=13 color=#888888")
        print(f"{bar(s_pct)} {s_pct}% | font=Menlo size=12 color={pct_color(s_pct)}")
        print(f"Resets in {fmt_reset(fh.get('resets_at'))} | size=11 color=gray")
        print("---")

    if w_pct is not None:
        print("Weekly · 7d window | size=13 color=#888888")
        print(f"{bar(w_pct)} {w_pct}% | font=Menlo size=12 color={pct_color(w_pct)}")
        print(f"Resets in {fmt_reset(wk.get('resets_at'))} | size=11 color=gray")
        if op_pct is not None or sn_pct is not None:
            print(f"  Opus:   {op_pct or 0}% | size=11 color=gray font=Menlo")
            print(f"  Sonnet: {sn_pct or 0}% | size=11 color=gray font=Menlo")
        print("---")

    if ext.get("is_enabled"):
        used = ext.get("used_credits") or 0
        limit = ext.get("monthly_limit")
        ex_pct = _pct(ext)
        limit_str = f"${limit}" if limit else "unlimited"
        print("Extra usage | size=13 color=#888888")
        try:
            print(f"${float(used):.2f} used of {limit_str} | size=12 color=gray")
        except (TypeError, ValueError):
            print(f"? used of {limit_str} | size=12 color=gray")
        if ex_pct is not None:
            print(f"{bar(ex_pct)} {ex_pct}% | font=Menlo size=12 color={pct_color(ex_pct)}")
        print("---")

    now = datetime.now().strftime("%H:%M")
    stale_note = " (cached)" if stale else ""
    print(f"Updated {now}{stale_note} | size=11 color=gray")
    print("---")
    print("Refresh | refresh=true")
    print("Open Claude.ai | href=https://claude.ai color=#CC5500")


if __name__ == "__main__":
    main()
