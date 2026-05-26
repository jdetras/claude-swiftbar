"""
Tests for claude-usage SwiftBar plugin helper functions.

Run with:  python -m pytest tests/ -v
All tests are pure-function; no credentials or network access required.
"""

import json
import os
import stat
import sys
import time
import types
import urllib.error

import pytest

# ── import plugin without executing main() ────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins"))
_src = open(
    os.path.join(os.path.dirname(__file__), "..", "plugins", "claude-usage.5m.py"),
    encoding="utf-8",
).read()
_mod = types.ModuleType("plugin")
exec(compile(_src, "claude-usage.5m.py", "exec"), _mod.__dict__)  # noqa: S102

fmt_reset = _mod.fmt_reset
bar = _mod.bar
pct_color = _mod.pct_color
fmt_k = _mod.fmt_k
recommend = _mod.recommend
friendly_model = _mod.friendly_model
safe_menu_text = _mod.safe_menu_text
parse_latest_session = _mod.parse_latest_session
load_cache = _mod.load_cache
save_cache = _mod.save_cache
_parse_retry_after = _mod._parse_retry_after
_cache_path_safe = _mod._cache_path_safe


# ── safe_menu_text ────────────────────────────────────────────────────────────


class TestSafeMenuText:
    def test_plain_string_passes_through(self):
        assert safe_menu_text("hello world") == "hello world"

    def test_pipe_replaced(self):
        result = safe_menu_text("foo | bar=baz")
        assert "|" not in result
        assert "foo" in result

    def test_newline_collapsed(self):
        result = safe_menu_text("line1\nline2")
        assert "\n" not in result
        assert "line1" in result and "line2" in result

    def test_carriage_return_collapsed(self):
        result = safe_menu_text("a\rb")
        assert "\r" not in result

    def test_truncated_to_max_len(self):
        long_str = "a" * 200
        result = safe_menu_text(long_str, max_len=50)
        assert len(result) <= 50
        assert result.endswith("…")

    def test_non_string_coerced(self):
        assert safe_menu_text(42) == "42"
        assert safe_menu_text(None) == "None"
        assert safe_menu_text(3.14) == "3.14"

    def test_control_characters_removed(self):
        result = safe_menu_text("hello\x00world\x01")
        assert "\x00" not in result
        assert "\x01" not in result

    def test_multiple_pipes_replaced(self):
        result = safe_menu_text("a|b|c|d")
        assert result.count("|") == 0

    def test_empty_string(self):
        assert safe_menu_text("") == ""

    def test_injection_attempt(self):
        # a real xbar injection attempt
        result = safe_menu_text("normal text | bash=evil color=red")
        assert "|" not in result

    def test_unicode_preserved(self):
        result = safe_menu_text("Sonnet 4.6 ✓")
        assert "Sonnet" in result
        assert "✓" in result


# ── fmt_reset ─────────────────────────────────────────────────────────────────


class TestFmtReset:
    def test_none_returns_question_mark(self):
        assert fmt_reset(None) == "?"

    def test_empty_returns_question_mark(self):
        assert fmt_reset("") == "?"

    def test_invalid_iso_returns_question_mark(self):
        assert fmt_reset("not-a-date") == "?"

    def test_past_timestamp_returns_zero_minutes(self):
        assert fmt_reset("2000-01-01T00:00:00Z") == "0m"

    def test_far_future_returns_days(self):
        from datetime import datetime, timedelta, timezone

        future = datetime.now(timezone.utc) + timedelta(days=3, hours=2)
        assert fmt_reset(future.isoformat()).startswith("3d")

    def test_hours_format(self):
        from datetime import datetime, timedelta, timezone

        future = datetime.now(timezone.utc) + timedelta(hours=2, minutes=30)
        result = fmt_reset(future.isoformat())
        assert "h" in result and "m" in result

    def test_minutes_only(self):
        from datetime import datetime, timedelta, timezone

        future = datetime.now(timezone.utc) + timedelta(minutes=45)
        result = fmt_reset(future.isoformat())
        assert result.endswith("m") and "h" not in result


# ── bar ───────────────────────────────────────────────────────────────────────


class TestBar:
    def test_zero_pct(self):
        assert bar(0) == "░" * 16

    def test_100_pct(self):
        assert bar(100) == "█" * 16

    def test_50_pct(self):
        assert bar(50, width=10) == "█████░░░░░"

    def test_none_pct(self):
        assert "█" not in bar(None)

    def test_over_100_clamped(self):
        assert bar(150, width=10) == "█" * 10

    def test_negative_clamped(self):
        assert bar(-10, width=10) == "░" * 10

    def test_custom_width(self):
        assert len(bar(50, width=20)) == 20


# ── pct_color ─────────────────────────────────────────────────────────────────


class TestPctColor:
    def test_none_is_gray(self):
        assert pct_color(None) == "gray"

    def test_low_is_green(self):
        assert pct_color(30) == "#30D158"

    def test_60_is_orange(self):
        assert pct_color(60) == "#FF9500"

    def test_85_is_red(self):
        assert pct_color(85) == "#FF4A4A"

    def test_59_is_green(self):
        assert pct_color(59) == "#30D158"

    def test_84_is_orange(self):
        assert pct_color(84) == "#FF9500"


# ── fmt_k ─────────────────────────────────────────────────────────────────────


class TestFmtK:
    def test_small(self):
        assert fmt_k(500) == "500"

    def test_thousands(self):
        assert fmt_k(68_000) == "68k"

    def test_millions(self):
        assert "M" in fmt_k(1_500_000)

    def test_exactly_1000(self):
        assert fmt_k(1_000) == "1k"

    def test_zero(self):
        assert fmt_k(0) == "0"


# ── friendly_model ────────────────────────────────────────────────────────────


class TestFriendlyModel:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("claude-sonnet-4-6-20250514", "Sonnet 4.6"),
            ("claude-opus-4-20250514", "Opus 4"),
            ("claude-haiku-4-5-20251001", "Haiku 4.5"),
            ("claude-haiku-4", "Haiku 4"),
            ("", "unknown"),
            ("some-future-model", "some-future-model"),
        ],
    )
    def test_labels(self, raw, expected):
        assert friendly_model(raw) == expected

    def test_pipe_in_model_name_sanitized(self):
        result = friendly_model("claude-sonnet | bash=evil")
        assert "|" not in result

    def test_newline_in_model_name_sanitized(self):
        result = friendly_model("claude-sonnet\nevil line")
        assert "\n" not in result


# ── recommend ─────────────────────────────────────────────────────────────────


class TestRecommend:
    def test_all_clear(self):
        ico, _ = recommend(30, 30, False)
        assert ico == "🟢"

    def test_context_filling(self):
        ico, msg = recommend(30, 70, False)
        assert ico == "🟡"
        assert "compact" in msg.lower()

    def test_context_after_compact(self):
        ico, msg = recommend(30, 70, True)
        assert ico == "🟡"
        assert "compaction" in msg.lower()

    def test_session_high(self):
        ico, _ = recommend(80, 30, False)
        assert ico == "🟠"

    def test_context_critical(self):
        ico, _ = recommend(30, 90, False)
        assert ico == "🔴"

    def test_both_critical(self):
        ico, _ = recommend(90, 80, False)
        assert ico == "🔴"

    def test_ceiling(self):
        ico, _ = recommend(97, 50, False)
        assert ico == "🚨"

    def test_none_inputs(self):
        ico, _ = recommend(None, None, False)
        assert ico == "🟢"

    def test_session_60_is_yellow(self):
        ico, _ = recommend(60, 10, False)
        assert ico == "🟡"


# ── _parse_retry_after ────────────────────────────────────────────────────────


class TestParseRetryAfter:
    def _headers(self, val):
        return {"Retry-After": val}

    def test_valid_value(self):
        assert _parse_retry_after(self._headers("300")) == 300

    def test_missing_header_returns_default(self):
        assert _parse_retry_after({}) == _mod.RETRY_AFTER_DEFAULT

    def test_none_header_returns_default(self):
        assert _parse_retry_after({"Retry-After": None}) == _mod.RETRY_AFTER_DEFAULT

    def test_negative_returns_default(self):
        assert _parse_retry_after(self._headers("-1")) == _mod.RETRY_AFTER_DEFAULT

    def test_zero_clamped_to_min(self):
        assert _parse_retry_after(self._headers("0")) == _mod.RETRY_AFTER_MIN

    def test_too_large_clamped_to_max(self):
        result = _parse_retry_after(self._headers("99999"))
        assert result == _mod.RETRY_AFTER_MAX

    def test_non_numeric_returns_default(self):
        assert _parse_retry_after(self._headers("abc")) == _mod.RETRY_AFTER_DEFAULT

    def test_float_string_truncated(self):
        # int("3.5") raises ValueError → default
        assert _parse_retry_after(self._headers("3.5")) == _mod.RETRY_AFTER_DEFAULT

    def test_boundary_min(self):
        result = _parse_retry_after(self._headers(str(_mod.RETRY_AFTER_MIN)))
        assert result == _mod.RETRY_AFTER_MIN

    def test_boundary_max(self):
        result = _parse_retry_after(self._headers(str(_mod.RETRY_AFTER_MAX)))
        assert result == _mod.RETRY_AFTER_MAX

    def test_none_headers_object(self):
        assert _parse_retry_after(None) == _mod.RETRY_AFTER_DEFAULT


# ── cache helpers ─────────────────────────────────────────────────────────────


class TestCache:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CACHE_PATH", tmp_path / "last_good.json")
        save_cache({"data": {"five_hour": {"utilization": 42.0}}, "ts": "2026-01-01T00:00:00"})
        result = load_cache()
        assert result is not None
        assert result["data"]["five_hour"]["utilization"] == 42.0

    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CACHE_PATH", tmp_path / "nonexistent.json")
        assert load_cache() is None

    def test_save_creates_parent_dirs(self, tmp_path, monkeypatch):
        deep = tmp_path / "a" / "b" / "c" / "last_good.json"
        monkeypatch.setattr(_mod, "CACHE_PATH", deep)
        save_cache({"data": {}, "ts": "2026-01-01"})
        assert deep.exists()

    def test_load_handles_corrupt_file(self, tmp_path, monkeypatch):
        p = tmp_path / "last_good.json"
        p.write_text("not json{{{")
        monkeypatch.setattr(_mod, "CACHE_PATH", p)
        assert load_cache() is None

    def test_load_rejects_non_dict_json(self, tmp_path, monkeypatch):
        p = tmp_path / "last_good.json"
        p.write_text("[1, 2, 3]")
        monkeypatch.setattr(_mod, "CACHE_PATH", p)
        assert load_cache() is None

    def test_cache_file_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CACHE_PATH", tmp_path / "last_good.json")
        save_cache({"data": {}, "ts": "2026-01-01"})
        mode = oct(stat.S_IMODE(os.stat(tmp_path / "last_good.json").st_mode))
        assert mode == oct(0o600)

    def test_cache_dir_permissions(self, tmp_path, monkeypatch):
        target = tmp_path / "newdir" / "last_good.json"
        monkeypatch.setattr(_mod, "CACHE_PATH", target)
        save_cache({"data": {}, "ts": "2026-01-01"})
        mode = oct(stat.S_IMODE(os.stat(tmp_path / "newdir").st_mode))
        assert mode == oct(0o700)

    def test_symlink_file_rejected(self, tmp_path, monkeypatch):
        real = tmp_path / "real.json"
        real.write_text("{}")
        link = tmp_path / "last_good.json"
        link.symlink_to(real)
        monkeypatch.setattr(_mod, "CACHE_PATH", link)
        assert load_cache() is None

    def test_symlink_dir_rejected(self, tmp_path, monkeypatch):
        real_dir = tmp_path / "realdir"
        real_dir.mkdir()
        link_dir = tmp_path / "linkdir"
        link_dir.symlink_to(real_dir)
        monkeypatch.setattr(_mod, "CACHE_PATH", link_dir / "last_good.json")
        assert load_cache() is None

    def test_save_symlink_dir_skipped(self, tmp_path, monkeypatch):
        real_dir = tmp_path / "realdir"
        real_dir.mkdir()
        link_dir = tmp_path / "linkdir"
        link_dir.symlink_to(real_dir)
        monkeypatch.setattr(_mod, "CACHE_PATH", link_dir / "last_good.json")
        save_cache({"data": {}, "ts": "2026-01-01"})  # must not raise
        assert not (real_dir / "last_good.json").exists()

    def test_atomic_write_no_partial_file(self, tmp_path, monkeypatch):
        """Temp file approach means readers never see a partial write."""
        path = tmp_path / "last_good.json"
        monkeypatch.setattr(_mod, "CACHE_PATH", path)
        save_cache({"data": {"x": 1}, "ts": "2026-01-01"})
        # verify final file is valid JSON
        assert json.loads(path.read_text())["data"]["x"] == 1


# ── parse_latest_session ──────────────────────────────────────────────────────


class TestParseLatestSession:
    def _write_jsonl(self, tmp_path, entries):
        path = os.path.join(tmp_path, "session.jsonl")
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return path

    def test_returns_none_when_no_files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        assert parse_latest_session() is None

    def test_parses_context_tokens(self, monkeypatch, tmp_path):
        entries = [
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6-20250514",
                    "usage": {
                        "input_tokens": 10_000,
                        "output_tokens": 500,
                        "cache_read_input_tokens": 5_000,
                        "cache_creation_input_tokens": 1_000,
                    },
                },
            }
        ]
        self._write_jsonl(tmp_path, entries)
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        result = parse_latest_session()
        assert result is not None
        assert result["context_tokens"] == 16_000
        assert result["model"] == "Sonnet 4.6"
        assert result["session_input"] == 10_000
        assert result["session_output"] == 500
        assert result["had_compaction"] is False

    def test_detects_compaction(self, monkeypatch, tmp_path):
        entries = [
            {"type": "system", "subtype": "compacted", "summary": "..."},
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6-20250514",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 100,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
        ]
        self._write_jsonl(tmp_path, entries)
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        assert parse_latest_session()["had_compaction"] is True

    def test_skips_malformed_lines(self, monkeypatch, tmp_path):
        path = os.path.join(tmp_path, "session.jsonl")
        with open(path, "w") as f:
            f.write("not json\n{}\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "model": "claude-haiku-4-5-20251001",
                            "usage": {
                                "input_tokens": 2000,
                                "output_tokens": 50,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0,
                            },
                        },
                    }
                )
                + "\n"
            )
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        result = parse_latest_session()
        assert result is not None
        assert result["model"] == "Haiku 4.5"

    def test_cumulative_output_tokens(self, monkeypatch, tmp_path):
        entries = [
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6-20250514",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 200,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6-20250514",
                    "usage": {
                        "input_tokens": 2000,
                        "output_tokens": 300,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
        ]
        self._write_jsonl(tmp_path, entries)
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        result = parse_latest_session()
        assert result["session_output"] == 500
        assert result["context_tokens"] == 2000

    def test_skips_file_over_size_limit(self, monkeypatch, tmp_path):
        path = os.path.join(tmp_path, "big.jsonl")
        with open(path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "model": "m",
                            "usage": {
                                "input_tokens": 1,
                                "output_tokens": 1,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0,
                            },
                        },
                    }
                )
                + "\n"
            )
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        monkeypatch.setattr(_mod, "MAX_JSONL_BYTES", 5)  # force limit below file size
        assert parse_latest_session() is None

    def test_stops_at_line_limit(self, monkeypatch, tmp_path):
        path = os.path.join(tmp_path, "long.jsonl")
        entry = json.dumps({"type": "other"})
        with open(path, "w") as f:
            for _ in range(200):
                f.write(entry + "\n")
            # put a real assistant entry at the end — should NOT be reached
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "model": "claude-sonnet-4-6-20250514",
                            "usage": {
                                "input_tokens": 9999,
                                "output_tokens": 1,
                                "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0,
                            },
                        },
                    }
                )
                + "\n"
            )
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        monkeypatch.setattr(_mod, "MAX_JSONL_LINES", 10)
        result = parse_latest_session()
        # line limit hit before the assistant entry — context_tokens should be 0
        assert result is not None
        assert result["context_tokens"] == 0

    def test_negative_token_values_clamped(self, monkeypatch, tmp_path):
        entries = [
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6-20250514",
                    "usage": {
                        "input_tokens": -500,
                        "output_tokens": -100,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        ]
        self._write_jsonl(tmp_path, entries)
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        result = parse_latest_session()
        assert result["context_tokens"] == 0
        assert result["session_output"] == 0

    def test_model_with_pipe_sanitized(self, monkeypatch, tmp_path):
        entries = [
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet | bash=evil",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 10,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        ]
        self._write_jsonl(tmp_path, entries)
        monkeypatch.setattr(_mod, "PROJECTS_GLOB", str(tmp_path / "*.jsonl"))
        result = parse_latest_session()
        assert "|" not in result["model"]


# ── 429 / backoff handling ────────────────────────────────────────────────────


class TestBackoff:
    def test_backoff_written_on_429(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CACHE_PATH", tmp_path / "last_good.json")
        save_cache({"data": {}, "ts": "2026-01-01T00:00:00"})

        class Fake429(urllib.error.HTTPError):
            def __init__(self):
                self.code = 429
                self.headers = {"Retry-After": "300"}
                self.url = "https://api.anthropic.com"
                self.msg = "Too Many Requests"
                self.fp = None

        def _raise_fake429(*a, **kw):
            raise Fake429()

        monkeypatch.setattr(_mod.urllib.request, "urlopen", _raise_fake429)
        data, err = _mod.fetch_usage("sk-ant-" + "fake")
        assert data is None
        assert "rate limited" in (err or "")
        cached = load_cache()
        assert "backoff_until" in cached
        assert cached["backoff_until"] > time.time()

    def test_backoff_respected_in_main_flow(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CACHE_PATH", tmp_path / "last_good.json")
        save_cache(
            {
                "data": {
                    "five_hour": {"utilization": 50.0, "resets_at": None},
                    "seven_day": {"utilization": 20.0, "resets_at": None},
                },
                "ts": "2026-01-01T00:00:00",
                "backoff_until": time.time() + 600,
            }
        )
        called = []

        def _fake_fetch_noop(t):
            called.append(True)
            return {}, None

        monkeypatch.setattr(_mod, "fetch_usage", _fake_fetch_noop)
        monkeypatch.setattr(_mod, "load_token", lambda: "sk-ant-" + "fake")
        monkeypatch.setattr(_mod, "parse_latest_session", lambda: None)
        monkeypatch.setattr(_mod, "_print_all", lambda *a, **kw: None)
        _mod.main()
        assert not called

    def test_backoff_clears_after_expiry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CACHE_PATH", tmp_path / "last_good.json")
        save_cache({"data": {}, "ts": "2026-01-01T00:00:00", "backoff_until": time.time() - 1})
        called = []

        def _fake_fetch_called(t):
            called.append(True)
            return {"five_hour": {}}, None

        monkeypatch.setattr(_mod, "fetch_usage", _fake_fetch_called)
        monkeypatch.setattr(_mod, "load_token", lambda: "sk-ant-" + "fake")
        monkeypatch.setattr(_mod, "parse_latest_session", lambda: None)
        monkeypatch.setattr(_mod, "_print_all", lambda *a, **kw: None)
        monkeypatch.setattr(_mod, "save_cache", lambda d: None)
        _mod.main()
        assert called

    def test_retry_after_missing_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "CACHE_PATH", tmp_path / "last_good.json")
        save_cache({"data": {}, "ts": "2026-01-01T00:00:00"})

        class Fake429NoHeader(urllib.error.HTTPError):
            def __init__(self):
                self.code = 429
                self.headers = {}
                self.url = "https://api.anthropic.com"
                self.msg = "Too Many Requests"
                self.fp = None

        monkeypatch.setattr(
            _mod.urllib.request,
            "urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(Fake429NoHeader()),
        )
        _, err = _mod.fetch_usage("sk-ant-" + "fake")
        cached = load_cache()
        expected_min = time.time() + _mod.RETRY_AFTER_DEFAULT - 5
        assert cached["backoff_until"] >= expected_min
