"""Tests for api_capture.py core logic.

Run: python -m pytest tests/ -v
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.api_capture import (
    load_profile,
    path_matches_patterns,
    categorize_path,
    is_api_request,
    safe_body,
    parse_sse,
    normalize_path,
    detect_auth_patterns,
    group_endpoints,
    extract_credentials,
    sanitize_record,
    generate_markdown,
    _mask,
)


# ─── Profile Loading ────────────────────────────────────────────────

class TestLoadProfile:
    def test_default_profile(self):
        p = load_profile(None)
        assert p["name"] == "default"
        assert isinstance(p["ignore_paths"], list)
        assert isinstance(p["api_categories"], dict)

    def test_named_profile(self):
        p = load_profile("doubao")
        assert "doubao" in p["name"].lower() or "豆包" in p["name"]
        assert len(p["filter_domains"]) > 0
        assert len(p["ignore_paths"]) > 0
        assert "chat" in p["api_categories"]

    def test_all_profiles_load(self):
        for name in ["doubao", "jimeng", "xyq"]:
            p = load_profile(name)
            assert p["name"], f"Profile {name} has no name"
            assert isinstance(p["ignore_paths"], list)
            assert isinstance(p["filter_domains"], list)
            assert isinstance(p["api_categories"], dict)
            assert isinstance(p["auth_hints"], dict)

    def test_missing_profile_falls_back(self, capsys):
        p = load_profile("nonexistent_xyz")
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower() or p["name"] == "default"


# ─── Path Matching ───────────────────────────────────────────────────

class TestPathMatching:
    def test_exact_match(self):
        assert path_matches_patterns("/list", ["/list"])
        assert not path_matches_patterns("/list2", ["/list"])

    def test_glob_star(self):
        assert path_matches_patterns("/monitor_browser/collect/batch", ["/monitor_browser/collect/*"])
        assert path_matches_patterns("/im/chain/single", ["/im/*"])
        assert not path_matches_patterns("/chat/completion", ["/im/*"])

    def test_trailing_slash(self):
        assert path_matches_patterns("/list/", ["/list"])
        assert path_matches_patterns("/list", ["/list/"])

    def test_empty_patterns(self):
        assert not path_matches_patterns("/anything", [])

    def test_categorize(self):
        categories = {
            "chat": ["/chat/completion", "/samantha/chat/completion"],
            "im": ["/im/*"],
        }
        assert categorize_path("/chat/completion", categories) == "chat"
        assert categorize_path("/im/chain/single", categories) == "im"
        assert categorize_path("/unknown/path", categories) is None


# ─── Request Filtering ───────────────────────────────────────────────

class TestIsApiRequest:
    def _profile(self, **overrides):
        base = {"filter_domains": [], "ignore_domains": [], "ignore_paths": []}
        base.update(overrides)
        return base

    def test_skip_static_resources(self):
        for rt in ("image", "font", "stylesheet", "media"):
            assert not is_api_request("https://x.com/api", "application/json", rt, self._profile())

    def test_keep_xhr_fetch(self):
        assert is_api_request("https://x.com/api", None, "xhr", self._profile())
        assert is_api_request("https://x.com/api", None, "fetch", self._profile())

    def test_skip_static_extensions(self):
        assert not is_api_request("https://x.com/style.css", None, "document", self._profile())
        assert not is_api_request("https://x.com/logo.png", None, "document", self._profile())

    def test_filter_domains(self):
        p = self._profile(filter_domains=["example.com"])
        assert is_api_request("https://example.com/api", "application/json", "xhr", p)
        assert not is_api_request("https://other.com/api", "application/json", "xhr", p)

    def test_ignore_paths(self):
        p = self._profile(ignore_paths=["/list", "/monitor/*"])
        assert not is_api_request("https://x.com/list", "application/json", "xhr", p)
        assert not is_api_request("https://x.com/monitor/batch", "application/json", "xhr", p)
        assert is_api_request("https://x.com/api/data", "application/json", "xhr", p)

    def test_ignore_domains(self):
        p = self._profile(ignore_domains=["analytics.com"])
        assert not is_api_request("https://analytics.com/track", "application/json", "xhr", p)

    def test_api_content_types(self):
        p = self._profile()
        assert is_api_request("https://x.com/data", "application/json", "document", p)
        assert is_api_request("https://x.com/data", "text/plain", "document", p)

    def test_common_api_paths(self):
        p = self._profile()
        assert is_api_request("https://x.com/api/users", None, "document", p)
        assert is_api_request("https://x.com/v1/chat", None, "document", p)
        assert is_api_request("https://x.com/mweb/v1/generate", None, "document", p)


# ─── Body Processing ────────────────────────────────────────────────

class TestSafeBody:
    def test_none(self):
        assert safe_body(None) is None

    def test_json(self):
        result = safe_body(b'{"key": "value"}')
        assert result == {"key": "value"}

    def test_plain_text(self):
        result = safe_body(b"hello world")
        assert result == "hello world"

    def test_binary(self):
        result = safe_body(bytes(range(256)) * 10)
        assert "binary" in str(result)

    def test_large_text_truncated(self):
        big = "x" * 100000
        result = safe_body(big)
        assert "truncated" in str(result)
        assert len(str(result)) < 60000

    def test_sse_detection(self):
        sse = "event: heartbeat\ndata: {}\n\nevent: chunk\ndata: {\"seq\":1}\n\n"
        result = safe_body(sse.encode())
        assert isinstance(result, dict)
        assert result["_sse_summary"] is True
        assert result["total_events"] == 2

    def test_sse_from_string(self):
        sse = "id: 0\nevent: ACK\ndata: {\"ok\":true}\n\n"
        result = safe_body(sse)
        assert isinstance(result, dict)
        assert result["_sse_summary"] is True


class TestParseSSE:
    def test_basic(self):
        text = "event: ping\ndata: {}\n\nevent: data\ndata: {\"x\":1}\n\n"
        result = parse_sse(text)
        assert result["total_events"] == 2
        assert result["event_counts"] == {"ping": 1, "data": 1}
        assert len(result["sample_events"]) == 2

    def test_sample_limit(self):
        lines = ""
        for i in range(20):
            lines += f"event: chunk\ndata: {{\"i\":{i}}}\n\n"
        result = parse_sse(lines)
        assert result["total_events"] == 20
        assert len(result["sample_events"]) == 5  # max 5 samples

    def test_multiline_data(self):
        text = "event: msg\ndata: line1\ndata: line2\n\n"
        result = parse_sse(text)
        assert result["total_events"] == 1
        assert result["sample_events"][0]["data"] == "line1\nline2"

    def test_empty(self):
        assert parse_sse("") is None
        assert parse_sse("just plain text") is None


# ─── Path Normalization ──────────────────────────────────────────────

class TestNormalizePath:
    def test_numeric_id(self):
        assert normalize_path("/api/users/12345") == "/api/users/{id}"

    def test_uuid(self):
        assert normalize_path("/api/task/550e8400-e29b-41d4-a716-446655440000") == "/api/task/{uuid}"

    def test_hash(self):
        assert normalize_path("/api/obj/abcdef1234567890abcdef1234") == "/api/obj/{hash}"

    def test_no_change(self):
        assert normalize_path("/api/chat/completion") == "/api/chat/completion"

    def test_mixed(self):
        assert normalize_path("/api/users/123/posts/456") == "/api/users/{id}/posts/{id}"


# ─── Auth Detection ──────────────────────────────────────────────────

class TestDetectAuth:
    def _records(self, headers=None, query_params=None):
        return [{
            "request_headers": headers or {},
            "query_params": query_params or {},
        }]

    def _profile(self, **hints):
        return {"auth_hints": hints}

    def test_cookie_detection(self):
        recs = self._records(headers={"cookie": "sessionid=abc; theme=dark; uid_tt=xyz"})
        result = detect_auth_patterns(recs, self._profile())
        assert "sessionid" in result["cookie_keys"]
        assert "uid_tt" in result["cookie_keys"]
        assert "theme" not in result["cookie_keys"]  # not auth-related

    def test_auth_header(self):
        recs = self._records(headers={"authorization": "Bearer xxx123"})
        result = detect_auth_patterns(recs, self._profile())
        assert len(result["auth_headers"]) == 1

    def test_sign_headers(self):
        recs = self._records(headers={"Sign": "abc", "Device-Time": "123", "x-token": "xyz"})
        result = detect_auth_patterns(recs, self._profile())
        assert "Sign" in result["custom_sign_headers"]
        assert "x-token" in result["custom_sign_headers"]

    def test_query_params(self):
        recs = self._records(query_params={"msToken": "abc", "a_bogus": "xyz", "page": "1"})
        result = detect_auth_patterns(recs, self._profile())
        assert "msToken" in result["query_auth_params"]
        assert "a_bogus" in result["query_auth_params"]
        assert "page" not in result["query_auth_params"]

    def test_profile_hints_expand_detection(self):
        recs = self._records(
            headers={"x-custom-sign": "val"},
            query_params={"my_token": "val"}
        )
        result = detect_auth_patterns(recs, self._profile(
            headers=["x-custom-sign"],
            query_params=["my_token"]
        ))
        assert "x-custom-sign" in result["custom_sign_headers"]
        assert "my_token" in result["query_auth_params"]


# ─── Endpoint Grouping ───────────────────────────────────────────────

class TestGroupEndpoints:
    def test_basic(self):
        records = [
            {"method": "GET", "normalized_path": "/api/users"},
            {"method": "GET", "normalized_path": "/api/users"},
            {"method": "POST", "normalized_path": "/api/users"},
        ]
        groups = group_endpoints(records)
        assert len(groups) == 2
        assert len(groups["GET /api/users"]) == 2
        assert len(groups["POST /api/users"]) == 1


# ─── Credential Extraction ───────────────────────────────────────────

class TestExtractCredentials:
    def test_extracts_cookies(self):
        records = [{
            "request_headers": {"cookie": "sessionid=abc123; sid_tt=def456; theme=dark"},
            "query_params": {},
        }]
        creds = extract_credentials(records)
        assert creds["cookies"]["sessionid"] == "abc123"
        assert creds["cookies"]["sid_tt"] == "def456"
        assert "theme" not in creds["cookies"]

    def test_extracts_tokens(self):
        records = [{
            "request_headers": {"authorization": "Bearer xyz"},
            "query_params": {"msToken": "tok123", "a_bogus": "bog456"},
        }]
        creds = extract_credentials(records)
        assert "header:authorization" in creds["tokens"]
        assert creds["tokens"]["query:msToken"] == "tok123"

    def test_full_cookie_string(self):
        records = [{
            "request_headers": {"cookie": "a=1; b=2; sessionid=x"},
            "query_params": {},
        }]
        creds = extract_credentials(records)
        assert creds["full_cookie_string"] == "a=1; b=2; sessionid=x"


# ─── Sanitization ────────────────────────────────────────────────────

class TestSanitize:
    def test_mask_short(self):
        assert _mask("abc") == "abc"
        assert _mask("abcdef") == "abcdef"

    def test_mask_long(self):
        masked = _mask("abcdef1234567890")
        assert masked.startswith("abcdef")
        assert "*" in masked
        assert "1234567890" not in masked

    def test_sanitize_cookies(self):
        rec = {
            "request_headers": {"cookie": "sessionid=secret123456; theme=dark"},
            "query_params": {},
            "url": "https://x.com/api",
        }
        s = sanitize_record(rec)
        assert "secret123456" not in s["request_headers"]["cookie"]
        assert "theme=dark" in s["request_headers"]["cookie"]

    def test_sanitize_query_params(self):
        rec = {
            "request_headers": {},
            "query_params": {"msToken": "longtokenvalue123", "page": "1"},
            "url": "https://x.com/api?msToken=longtokenvalue123&page=1",
        }
        s = sanitize_record(rec)
        assert "longtokenvalue123" not in s["query_params"]["msToken"]
        assert s["query_params"]["page"] == "1"  # non-sensitive unchanged

    def test_sanitize_url(self):
        rec = {
            "request_headers": {},
            "query_params": {"a_bogus": "xyz"},
            "url": "https://x.com/api?a_bogus=xyz123&aid=100",
        }
        s = sanitize_record(rec)
        assert "xyz123" not in s["url"]
        assert "aid=100" in s["url"]

    def test_sanitize_auth_header(self):
        rec = {
            "request_headers": {"authorization": "Bearer supersecrettoken123"},
            "query_params": {},
            "url": "https://x.com/api",
        }
        s = sanitize_record(rec)
        assert "supersecrettoken123" not in s["request_headers"]["authorization"]

    def test_no_mutation(self):
        """Sanitize should not modify the original record."""
        rec = {
            "request_headers": {"cookie": "sessionid=secret"},
            "query_params": {"msToken": "abc"},
            "url": "https://x.com",
        }
        sanitize_record(rec)
        assert rec["request_headers"]["cookie"] == "sessionid=secret"
        assert rec["query_params"]["msToken"] == "abc"


# ─── Markdown Generation ─────────────────────────────────────────────

class TestGenerateMarkdown:
    def _make_data(self):
        records = [{
            "seq": 1, "method": "POST", "path": "/api/chat",
            "normalized_path": "/api/chat", "url": "https://x.com/api/chat",
            "domain": "x.com", "response_status": 200, "response_body_size": 100,
            "request_headers": {}, "query_params": {}, "request_body": {"msg": "hi"},
            "response_body": {"reply": "hello"},
        }]
        auth = {"cookie_keys": ["sid"], "auth_headers": [], "custom_sign_headers": [], "query_auth_params": []}
        groups = group_endpoints(records)
        profile = {"name": "test", "api_categories": {"chat": ["/api/chat"]}, "auth_hints": {}}
        return records, auth, groups, profile

    def test_basic_output(self):
        records, auth, groups, profile = self._make_data()
        md = generate_markdown(records, auth, groups, profile)
        assert "# API Capture Report" in md
        assert "Authentication Analysis" in md
        assert "Request Timeline" in md
        assert "Endpoint Details" in md
        assert "POST /api/chat" in md

    def test_categories_shown(self):
        records, auth, groups, profile = self._make_data()
        md = generate_markdown(records, auth, groups, profile)
        assert "API Categories" in md
        assert "chat" in md.lower()

    def test_ws_section(self):
        records, auth, groups, profile = self._make_data()
        ws = [{"ws_seq": 1, "conn_id": 1, "timestamp": "", "elapsed_seconds": 0,
               "direction": "sent", "url": "wss://x.com/ws", "domain": "x.com",
               "path": "/ws", "payload": {"type": "ping"}, "payload_size": 10}]
        md = generate_markdown(records, auth, groups, profile, ws)
        assert "WebSocket" in md
        assert "SENT" in md

    def test_no_ws_section_when_empty(self):
        records, auth, groups, profile = self._make_data()
        md = generate_markdown(records, auth, groups, profile, [])
        assert "## 4. WebSocket Connections" not in md
