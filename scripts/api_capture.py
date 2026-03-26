"""API Scout — Universal API Capture Tool.

Launches a visible browser, captures all API requests while you manually
operate any website, then outputs structured JSON + Markdown summary.

Usage:
    python scripts/api_capture.py --profile doubao
    python scripts/api_capture.py --profile jimeng
    python scripts/api_capture.py --url "https://example.com" --filter "example.com"
    python scripts/api_capture.py  # opens blank page, default profile
"""

import argparse
import asyncio
import base64
import json
import re
import time
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import yaml

# ─── Constants ───────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = PROJECT_ROOT / "profiles"
CAPTURES_DIR = PROJECT_ROOT / "captures"       # raw data (contains sensitive info)
REPORTS_DIR = PROJECT_ROOT / "reports"          # sanitized analysis reports (safe to share)
CREDENTIALS_DIR = PROJECT_ROOT / "credentials"  # extracted cookies/tokens

MAX_BODY_SIZE = 50 * 1024  # 50KB
SKIP_RESOURCE_TYPES = {"image", "font", "stylesheet", "media", "manifest", "other"}
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".avif",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".css", ".map",
    ".mp3", ".mp4", ".webm", ".ogg", ".wav",
}
API_CONTENT_TYPES = {"json", "text", "form", "protobuf", "grpc", "xml"}


# ─── Profile ─────────────────────────────────────────────────────────

def load_profile(name: str | None) -> dict:
    """Load a YAML profile by name. Falls back to _default."""
    if name:
        path = PROFILES_DIR / f"{name}.yaml"
        if not path.exists():
            print(f"Profile '{name}' not found at {path}, using _default")
            name = None

    if not name:
        path = PROFILES_DIR / "_default.yaml"

    if not path.exists():
        return {"name": "default", "ignore_paths": [], "ignore_domains": [],
                "filter_domains": [], "api_categories": {}, "auth_hints": {}}

    with open(path, encoding="utf-8") as f:
        profile = yaml.safe_load(f) or {}

    # Normalize lists
    for key in ("ignore_paths", "ignore_domains", "filter_domains"):
        if key not in profile:
            profile[key] = []

    if "api_categories" not in profile:
        profile["api_categories"] = {}
    if "auth_hints" not in profile:
        profile["auth_hints"] = {}

    return profile


def path_matches_patterns(path: str, patterns: list[str]) -> bool:
    """Check if a URL path matches any of the ignore patterns (supports * glob)."""
    for pattern in patterns:
        if pattern.endswith("*"):
            if path.startswith(pattern[:-1]):
                return True
        elif fnmatch(path, pattern):
            return True
        elif path == pattern or path.rstrip("/") == pattern.rstrip("/"):
            return True
    return False


def categorize_path(path: str, categories: dict[str, list[str]]) -> str | None:
    """Return the category name for a path, or None."""
    for cat_name, patterns in categories.items():
        if path_matches_patterns(path, patterns):
            return cat_name
    return None


# ─── Helpers ─────────────────────────────────────────────────────────

def is_api_request(url: str, content_type: str | None, resource_type: str,
                   profile: dict) -> bool:
    """Determine if a request is an API call (not a static resource)."""
    if resource_type in SKIP_RESOURCE_TYPES:
        return False

    parsed = urlparse(url)

    # Profile: filter_domains — if set, only keep requests to these domains
    if profile["filter_domains"]:
        if not any(d in parsed.netloc for d in profile["filter_domains"]):
            return False

    # Profile: ignore_domains
    if any(d in parsed.netloc for d in profile["ignore_domains"]):
        return False

    # Profile: ignore_paths
    if path_matches_patterns(parsed.path, profile["ignore_paths"]):
        return False

    suffix = Path(parsed.path).suffix.lower()
    if suffix in SKIP_EXTENSIONS:
        return False

    # XHR/Fetch are always API calls
    if resource_type in ("xhr", "fetch"):
        return True

    # Check content type
    if content_type:
        ct = content_type.lower()
        if any(t in ct for t in API_CONTENT_TYPES):
            return True

    # Keep requests with no extension or common API paths
    if not suffix or any(p in parsed.path for p in
                         ("/api/", "/v1/", "/v2/", "/mweb/", "/rpc/", "/graphql")):
        return True

    return False


def parse_sse(text: str) -> dict:
    """Parse SSE text into a summary: sample events + stats."""
    events = []
    current_event = ""
    current_data_lines = []

    for line in text.split("\n"):
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line[5:].strip())
        elif line.startswith("id:"):
            pass  # skip id lines
        elif line == "" and (current_event or current_data_lines):
            data_str = "\n".join(current_data_lines)
            # Try parse data as JSON
            data = data_str
            try:
                data = json.loads(data_str)
            except (json.JSONDecodeError, TypeError):
                pass
            events.append({"event": current_event or "message", "data": data})
            current_event = ""
            current_data_lines = []

    # Flush last event
    if current_event or current_data_lines:
        data_str = "\n".join(current_data_lines)
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            data = data_str
        events.append({"event": current_event or "message", "data": data})

    if not events:
        return None

    # Build summary: first 5 events as samples + event type counts
    event_counts = {}
    for e in events:
        event_counts[e["event"]] = event_counts.get(e["event"], 0) + 1

    return {
        "_sse_summary": True,
        "total_events": len(events),
        "event_counts": event_counts,
        "sample_events": events[:5],
    }


def safe_body(body: bytes | str | None, content_type: str | None = None) -> str | dict | None:
    """Decode and truncate body safely. SSE responses get parsed into summary."""
    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary {len(body)} bytes>"

    # Detect SSE: starts with "id:" or "event:" or "data:"
    stripped = body.lstrip()
    if stripped[:3] in ("id:", "dat") or stripped[:6] == "event:":
        sse = parse_sse(body)
        if sse:
            return sse

    if len(body) > MAX_BODY_SIZE:
        return body[:MAX_BODY_SIZE] + f"\n... <truncated, total {len(body)} chars>"
    # Try parse as JSON
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        pass
    return body


def normalize_path(path: str) -> str:
    """Replace IDs/UUIDs/numbers in path segments with placeholders for grouping."""
    parts = path.strip("/").split("/")
    normalized = []
    for part in parts:
        if re.match(r"^\d+$", part):
            normalized.append("{id}")
        elif re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", part, re.I):
            normalized.append("{uuid}")
        elif re.match(r"^[0-9a-f]{24,}$", part, re.I):
            normalized.append("{hash}")
        else:
            normalized.append(part)
    return "/" + "/".join(normalized)


def detect_auth_patterns(records: list[dict], profile: dict) -> dict:
    """Analyze captured records to identify authentication patterns."""
    auth_info = {
        "cookie_keys": set(),
        "auth_headers": set(),
        "custom_sign_headers": set(),
        "query_auth_params": set(),
    }

    # Base keywords + profile hints
    sign_keywords = {"sign", "token", "key", "auth", "secret", "signature",
                     "nonce", "timestamp", "bogus"}
    cookie_keywords = {"session", "token", "sid", "uid", "auth", "login"}

    hints = profile.get("auth_hints", {})
    if hints.get("query_params"):
        for p in hints["query_params"]:
            sign_keywords.add(p.lower())
    if hints.get("cookies"):
        for c in hints["cookies"]:
            cookie_keywords.add(c.lower())
    if hints.get("headers"):
        for h in hints["headers"]:
            sign_keywords.add(h.lower())

    for rec in records:
        headers = rec.get("request_headers", {})
        # Cookie analysis
        cookie = headers.get("cookie", headers.get("Cookie", ""))
        if cookie:
            for part in cookie.split(";"):
                if "=" in part:
                    name = part.split("=")[0].strip()
                    if any(k in name.lower() for k in cookie_keywords):
                        auth_info["cookie_keys"].add(name)
        # Auth headers
        for key in headers:
            kl = key.lower()
            if kl == "authorization":
                auth_info["auth_headers"].add(f"{key}: {str(headers[key])[:50]}...")
            elif any(s in kl for s in sign_keywords):
                auth_info["custom_sign_headers"].add(key)
        # Query params
        for key in rec.get("query_params", {}):
            kl = key.lower()
            if any(s in kl for s in sign_keywords):
                auth_info["query_auth_params"].add(key)

    return {k: sorted(v) for k, v in auth_info.items()}


def group_endpoints(records: list[dict]) -> dict[str, list[dict]]:
    """Group records by normalized endpoint."""
    groups = {}
    for rec in records:
        key = f"{rec['method']} {rec['normalized_path']}"
        if key not in groups:
            groups[key] = []
        groups[key].append(rec)
    return groups


# ─── Anomaly Detection ───────────────────────────────────────────────

# Patterns that look like server-side variable aliases / template vars
_ALIAS_PATTERNS = [
    re.compile(r"^sys_\w+$"),          # sys_flowId, sys_accountId
    re.compile(r"^\$\w+$"),            # $flowId
    re.compile(r"^__\w+__$"),          # __flowId__
    re.compile(r"^\{\{?\w+\}?\}$"),    # {flowId} or {{flowId}}
    re.compile(r"^:\w+$"),             # :flowId (Express-style)
]


def _is_alias_segment(segment: str) -> bool:
    """Check if a path segment looks like a server-side variable alias."""
    return any(p.match(segment) for p in _ALIAS_PATTERNS)


def detect_path_anomalies(records: list[dict]) -> dict:
    """Detect unusual path patterns that may require special handling.

    Returns:
        {
            "alias_segments": {
                "sys_flowId": {"count": 12, "location": "path", "endpoints": [...]},
                ...
            },
            "alias_query_params": {
                "transId": {"value": "sys_transId", "count": 8, "endpoints": [...]},
                ...
            },
        }
    """
    alias_segments: dict[str, dict] = {}
    alias_query_params: dict[str, dict] = {}

    for rec in records:
        path = rec.get("path", "")
        endpoint = f"{rec['method']} {rec.get('normalized_path', path)}"

        # Check path segments
        for segment in path.strip("/").split("/"):
            if _is_alias_segment(segment):
                if segment not in alias_segments:
                    alias_segments[segment] = {"count": 0, "location": "path",
                                               "endpoints": set()}
                alias_segments[segment]["count"] += 1
                alias_segments[segment]["endpoints"].add(endpoint)

        # Check query param values for alias patterns
        for key, val in rec.get("query_params", {}).items():
            val_str = val if isinstance(val, str) else str(val)
            if _is_alias_segment(val_str):
                pk = f"{key}={val_str}"
                if pk not in alias_query_params:
                    alias_query_params[pk] = {"param": key, "value": val_str,
                                              "count": 0, "endpoints": set()}
                alias_query_params[pk]["count"] += 1
                alias_query_params[pk]["endpoints"].add(endpoint)

    # Convert sets to sorted lists for JSON serialization
    for v in alias_segments.values():
        v["endpoints"] = sorted(v["endpoints"])
    for v in alias_query_params.values():
        v["endpoints"] = sorted(v["endpoints"])

    return {
        "alias_segments": alias_segments,
        "alias_query_params": alias_query_params,
    }


def detect_set_cookies(records: list[dict]) -> dict[str, list[str]]:
    """Detect which endpoints set new cookies via Set-Cookie headers.

    Returns:
        { "GET /openwebserver/login": ["JSESSIONID", "SERVERID"], ... }
    """
    endpoint_cookies: dict[str, set[str]] = {}

    for rec in records:
        set_cookie_names = rec.get("set_cookies", [])
        if not set_cookie_names:
            continue

        endpoint = f"{rec['method']} {rec.get('normalized_path', rec.get('path', '?'))}"
        if endpoint not in endpoint_cookies:
            endpoint_cookies[endpoint] = set()
        endpoint_cookies[endpoint].update(set_cookie_names)

    return {k: sorted(v) for k, v in endpoint_cookies.items()}


# ─── Request Diagnosis ────────────────────────────────────────────────

# Headers to skip when comparing (too noisy / always different)
_SKIP_DIFF_HEADERS = {
    "cookie", "user-agent", "accept", "accept-language", "accept-encoding",
    "connection", "host", "origin", "referer", "content-length",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
}


def _skeleton_path(path: str) -> str:
    """Replace all variable-looking segments with * for fuzzy matching.

    Both real IDs and server-side aliases become *, so:
      /signflows/sys_flowId/setCacheData  → /signflows/*/setCacheData
      /signflows/13b3276b.../setCacheData → /signflows/*/setCacheData
    """
    parts = path.strip("/").split("/")
    skeleton = []
    for part in parts:
        if (re.match(r"^\d+$", part)
                or re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", part, re.I)
                or re.match(r"^[0-9a-f]{24,}$", part, re.I)
                or _is_alias_segment(part)):
            skeleton.append("*")
        else:
            skeleton.append(part)
    return "/" + "/".join(skeleton)


def _diff_hint(captured_val: str, actual_val: str) -> str:
    """Generate a hint explaining the difference."""
    if _is_alias_segment(captured_val):
        return (f"captured value '{captured_val}' looks like a server-side alias "
                f"— use it literally instead of '{actual_val}'")
    if _is_alias_segment(actual_val):
        return (f"your value '{actual_val}' looks like an alias but captured used "
                f"'{captured_val}' — check which is correct")
    return f"value mismatch: captured '{captured_val}' vs yours '{actual_val}'"


def diagnose_request(failed: dict, report_json_path: str) -> dict:
    """Compare a failed request against captured successful requests to find diffs.

    Args:
        failed: {
            "method": "POST",
            "url": "https://...",
            "headers": {"content-type": "...", "webserver-token": "..."},
            "body": {...} or None,
            "status": 403,  # optional, for context
        }
        report_json_path: Path to a captured report JSON file (captures/ or reports/)

    Returns:
        {
            "matched_record": {"seq": N, "url": "...", "status": 200} or None,
            "diffs": [
                {"field": "path", "segment": 3,
                 "captured": "sys_flowId", "actual": "13b3276b...",
                 "hint": "..."},
                ...
            ],
            "no_diff_fields": ["method", "body", ...],
        }
        or {"error": "...", "candidates": [...]} if no match found.
    """
    # Load report
    report_path = Path(report_json_path)
    if not report_path.exists():
        return {"error": f"report file not found: {report_json_path}", "candidates": []}

    report = json.loads(report_path.read_text(encoding="utf-8"))
    records = report.get("records", [])
    if not records:
        return {"error": "no records in report", "candidates": []}

    # Parse failed request
    failed_parsed = urlparse(failed["url"])
    failed_path = failed_parsed.path
    failed_method = failed["method"].upper()
    failed_normalized = normalize_path(failed_path)
    failed_skeleton = _skeleton_path(failed_path)
    failed_qp = {k: v[0] if len(v) == 1 else v
                 for k, v in parse_qs(failed_parsed.query).items()}
    failed_headers = {k.lower(): v for k, v in failed.get("headers", {}).items()}
    failed_body = failed.get("body")

    # Find matching record: exact normalized match first, then skeleton match
    match = None
    for rec in records:
        if rec["method"].upper() != failed_method:
            continue
        if rec.get("normalized_path") == failed_normalized:
            match = rec
            break

    if not match:
        for rec in records:
            if rec["method"].upper() != failed_method:
                continue
            if _skeleton_path(rec.get("path", "")) == failed_skeleton:
                match = rec
                break

    if not match:
        # Return closest candidates
        candidates = []
        for rec in records:
            if rec["method"].upper() == failed_method:
                candidates.append(f"{rec['method']} {rec.get('path', '?')[:80]}")
        return {
            "error": f"no matching endpoint for {failed_method} {failed_path}",
            "candidates": sorted(set(candidates))[:10],
        }

    # ── Build diffs ──
    diffs = []
    no_diff = []

    # 1. Path diff (segment by segment)
    cap_segments = match["path"].strip("/").split("/")
    fail_segments = failed_path.strip("/").split("/")
    path_has_diff = False
    max_segs = max(len(cap_segments), len(fail_segments))
    for i in range(max_segs):
        cap_seg = cap_segments[i] if i < len(cap_segments) else "<missing>"
        fail_seg = fail_segments[i] if i < len(fail_segments) else "<missing>"
        if cap_seg != fail_seg:
            path_has_diff = True
            diffs.append({
                "field": "path", "segment": i,
                "captured": cap_seg, "actual": fail_seg,
                "hint": _diff_hint(cap_seg, fail_seg),
            })
    if not path_has_diff:
        no_diff.append("path")

    # 2. Query params diff
    cap_qp = match.get("query_params", {})
    qp_has_diff = False
    all_keys = set(cap_qp.keys()) | set(failed_qp.keys())
    for key in sorted(all_keys):
        cap_val = cap_qp.get(key)
        fail_val = failed_qp.get(key)
        if cap_val is not None and fail_val is None:
            qp_has_diff = True
            diffs.append({
                "field": "query_param", "key": key,
                "captured": str(cap_val), "actual": "<missing>",
                "hint": f"missing query param '{key}' (captured value: '{cap_val}')",
            })
        elif cap_val is None and fail_val is not None:
            qp_has_diff = True
            diffs.append({
                "field": "query_param", "key": key,
                "captured": "<missing>", "actual": str(fail_val),
                "hint": f"extra query param '{key}' not seen in capture",
            })
        elif str(cap_val) != str(fail_val):
            qp_has_diff = True
            diffs.append({
                "field": "query_param", "key": key,
                "captured": str(cap_val), "actual": str(fail_val),
                "hint": _diff_hint(str(cap_val), str(fail_val)),
            })
    if not qp_has_diff:
        no_diff.append("query_params")

    # 3. Headers diff (skip noisy ones)
    cap_headers = {k.lower(): v for k, v in match.get("request_headers", {}).items()}
    headers_has_diff = False
    cap_interesting = {k: v for k, v in cap_headers.items() if k not in _SKIP_DIFF_HEADERS}
    fail_interesting = {k: v for k, v in failed_headers.items() if k not in _SKIP_DIFF_HEADERS}
    all_hkeys = set(cap_interesting.keys()) | set(fail_interesting.keys())
    for key in sorted(all_hkeys):
        cap_val = cap_interesting.get(key)
        fail_val = fail_interesting.get(key)
        if cap_val is not None and fail_val is None:
            headers_has_diff = True
            diffs.append({
                "field": "header", "key": key,
                "captured": str(cap_val)[:100], "actual": "<missing>",
                "hint": f"missing header '{key}'",
            })
        elif cap_val is None and fail_val is not None:
            pass  # Extra headers from script are usually fine
        elif str(cap_val) != str(fail_val):
            headers_has_diff = True
            diffs.append({
                "field": "header", "key": key,
                "captured": str(cap_val)[:100], "actual": str(fail_val)[:100],
                "hint": f"header '{key}' value mismatch",
            })
    if not headers_has_diff:
        no_diff.append("headers")

    # 4. Body diff (structural: compare top-level keys if both are dicts)
    cap_body = match.get("request_body")
    body_has_diff = False
    if isinstance(cap_body, dict) and isinstance(failed_body, dict):
        cap_keys = set(cap_body.keys())
        fail_keys = set(failed_body.keys())
        missing = cap_keys - fail_keys
        extra = fail_keys - cap_keys
        if missing:
            body_has_diff = True
            diffs.append({
                "field": "body", "key": "missing_keys",
                "captured": sorted(missing), "actual": "<missing>",
                "hint": f"request body missing keys: {sorted(missing)}",
            })
        if extra:
            body_has_diff = True
            diffs.append({
                "field": "body", "key": "extra_keys",
                "captured": "<not present>", "actual": sorted(extra),
                "hint": f"request body has extra keys not in capture: {sorted(extra)}",
            })
    elif cap_body is not None and failed_body is None:
        body_has_diff = True
        diffs.append({
            "field": "body", "key": "body",
            "captured": "<present>", "actual": "<missing>",
            "hint": "captured request had a body but yours doesn't",
        })
    if not body_has_diff:
        no_diff.append("body")

    return {
        "matched_record": {
            "seq": match.get("seq"),
            "url": match.get("url", "")[:200],
            "status": match.get("response_status"),
        },
        "diffs": diffs,
        "no_diff_fields": no_diff,
    }


# ─── Credential Extraction & Sanitization ────────────────────────────

SENSITIVE_COOKIE_KEYS = {
    "sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt", "uid_tt_ss",
    "passport_csrf_token", "ttwid", "msToken", "odin_tt",
}
SENSITIVE_QUERY_KEYS = {"msToken", "a_bogus", "token", "sign"}
SENSITIVE_HEADER_KEYS = {"cookie", "authorization", "x-tt-passport-csrf-token"}


def extract_credentials(records: list[dict]) -> dict:
    """Extract all unique cookies, tokens, and auth headers from captured records."""
    cookies: dict[str, str] = {}
    tokens: dict[str, str] = {}
    full_cookie_strings: list[str] = []

    for rec in records:
        headers = rec.get("request_headers", {})

        # Extract cookies
        raw_cookie = headers.get("cookie", "")
        if raw_cookie and raw_cookie not in full_cookie_strings:
            full_cookie_strings.append(raw_cookie)
        for part in raw_cookie.split(";"):
            if "=" in part:
                name, _, value = part.partition("=")
                name = name.strip()
                value = value.strip()
                if value and (name in SENSITIVE_COOKIE_KEYS or
                              any(k in name.lower() for k in ("session", "token", "sid", "uid", "auth"))):
                    cookies[name] = value

        # Extract auth headers
        for key in ("authorization", "x-tt-passport-csrf-token"):
            if key in headers and headers[key]:
                tokens[f"header:{key}"] = headers[key]

        # Extract auth query params
        for key in ("msToken", "a_bogus", "token"):
            val = rec.get("query_params", {}).get(key)
            if val:
                tokens[f"query:{key}"] = val if isinstance(val, str) else val[0]

    return {
        "cookies": cookies,
        "tokens": tokens,
        "full_cookie_string": full_cookie_strings[0] if full_cookie_strings else "",
    }


def _mask(value: str, show: int = 6) -> str:
    """Mask a sensitive value, showing only first N chars."""
    if len(value) <= show:
        return value
    return value[:show] + "*" * min(8, len(value) - show)


def sanitize_record(rec: dict) -> dict:
    """Return a copy of a record with sensitive values masked."""
    rec = json.loads(json.dumps(rec))  # deep copy

    # Mask cookie header
    headers = rec.get("request_headers", {})
    if "cookie" in headers:
        parts = []
        for part in headers["cookie"].split(";"):
            if "=" in part:
                name, _, value = part.partition("=")
                name = name.strip()
                if name.lower() in {k.lower() for k in SENSITIVE_COOKIE_KEYS} or \
                   any(k in name.lower() for k in ("session", "token", "sid", "uid")):
                    parts.append(f"{name}={_mask(value.strip())}")
                else:
                    parts.append(part.strip())
            else:
                parts.append(part.strip())
        headers["cookie"] = "; ".join(parts)

    # Mask auth headers
    for key in ("authorization", "x-tt-passport-csrf-token"):
        if key in headers:
            headers[key] = _mask(headers[key])

    # Mask sensitive query params
    qp = rec.get("query_params", {})
    for key in SENSITIVE_QUERY_KEYS:
        if key in qp:
            qp[key] = _mask(qp[key]) if isinstance(qp[key], str) else qp[key]

    # Mask URL (replace sensitive query param values)
    url = rec.get("url", "")
    for key in SENSITIVE_QUERY_KEYS:
        url = re.sub(rf"({key}=)[^&]+", rf"\1***", url)
    rec["url"] = url

    return rec


# ─── Markdown Report ─────────────────────────────────────────────────

def generate_markdown(records: list[dict], auth_info: dict, groups: dict,
                      profile: dict, ws_records: list[dict] | None = None,
                      anomalies: dict | None = None,
                      set_cookie_map: dict | None = None) -> str:
    """Generate a human-readable Markdown analysis report."""
    name = profile.get("name", "unknown")
    ws_records = ws_records or []
    anomalies = anomalies or {}
    set_cookie_map = set_cookie_map or {}
    lines = [
        f"# API Capture Report — {name}",
        "",
        f"Profile: `{profile.get('name', 'default')}`",
        f"Captured at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total API requests: {len(records)}",
        f"Unique endpoints: {len(groups)}",
        f"WebSocket messages: {len(ws_records)}",
        "",
    ]

    # Categories summary (if profile defines them)
    categories = profile.get("api_categories", {})
    if categories:
        lines += ["---", "", "## 0. API Categories", ""]
        categorized = {}
        uncategorized = []
        for endpoint in groups:
            method, path = endpoint.split(" ", 1)
            cat = categorize_path(path, categories)
            if cat:
                categorized.setdefault(cat, []).append(endpoint)
            else:
                uncategorized.append(endpoint)

        for cat_name, endpoints in categorized.items():
            lines.append(f"**{cat_name}:**")
            for ep in endpoints:
                count = len(groups[ep])
                lines.append(f"- `{ep}` ({count} calls)")
            lines.append("")

        if uncategorized:
            lines.append("**uncategorized:**")
            for ep in uncategorized:
                count = len(groups[ep])
                lines.append(f"- `{ep}` ({count} calls)")
            lines.append("")

    # Anomaly warnings
    alias_segs = anomalies.get("alias_segments", {})
    alias_qps = anomalies.get("alias_query_params", {})
    has_anomalies = alias_segs or alias_qps or set_cookie_map
    if has_anomalies:
        lines += ["---", "", "## ⚠️ Anomaly Alerts", ""]

        if alias_segs or alias_qps:
            lines.append("### Server-side Variable Aliases")
            lines.append("")
            lines.append("> The following path segments / query params appear to be "
                         "server-side variable aliases.")
            lines.append("> They may need to be used **as literal strings** in the URL, "
                         "NOT replaced with real IDs.")
            lines.append("")
            for seg, info in sorted(alias_segs.items()):
                ep_count = len(info["endpoints"])
                lines.append(f"- **`{seg}`** — appears in {info['count']} requests "
                             f"across {ep_count} endpoint(s)")
            for key, info in sorted(alias_qps.items()):
                ep_count = len(info["endpoints"])
                lines.append(f"- **`{key}`** (query param) — appears in "
                             f"{info['count']} requests across {ep_count} endpoint(s)")
            lines.append("")

        if set_cookie_map:
            lines.append("### Set-Cookie Tracking")
            lines.append("")
            lines.append("> These endpoints set new cookies via `Set-Cookie` response "
                         "headers.")
            lines.append("> Subsequent requests may depend on these cookies — "
                         "capture and merge them.")
            lines.append("")
            for endpoint, cookie_names in sorted(set_cookie_map.items()):
                names_str = ", ".join(f"`{n}`" for n in cookie_names)
                lines.append(f"- `{endpoint}` → {names_str}")
            lines.append("")

    # Auth analysis
    lines += ["---", "", "## 1. Authentication Analysis", ""]

    if auth_info["cookie_keys"]:
        lines.append("**Session Cookies:**")
        for k in auth_info["cookie_keys"]:
            lines.append(f"- `{k}`")
        lines.append("")
    if auth_info["auth_headers"]:
        lines.append("**Authorization Headers:**")
        for h in auth_info["auth_headers"]:
            lines.append(f"- `{h}`")
        lines.append("")
    if auth_info["custom_sign_headers"]:
        lines.append("**Custom Sign/Token Headers:**")
        for h in auth_info["custom_sign_headers"]:
            lines.append(f"- `{h}`")
        lines.append("")
    if auth_info["query_auth_params"]:
        lines.append("**Auth-related Query Params:**")
        for p in auth_info["query_auth_params"]:
            lines.append(f"- `{p}`")
        lines.append("")

    if not any(auth_info.values()):
        lines.append("No obvious authentication patterns detected.")
        lines.append("")

    # Profile hints
    hints = profile.get("auth_hints", {})
    if hints:
        lines += ["**Profile auth hints (known patterns):**"]
        for k, v in hints.items():
            if v:
                lines.append(f"- {k}: {', '.join(f'`{x}`' for x in v)}")
        lines.append("")

    # Timeline
    lines += [
        "---", "",
        "## 2. Request Timeline", "",
        "| # | Method | URL | Status | Size | Category |",
        "|---|--------|-----|--------|------|----------|",
    ]
    for i, rec in enumerate(records, 1):
        url_short = rec["path"]
        if len(url_short) > 60:
            url_short = url_short[:57] + "..."
        size = rec.get("response_body_size", "?")
        cat = categorize_path(rec["path"], categories) or ""
        lines.append(
            f"| {i} | {rec['method']} | `{url_short}` | "
            f"{rec.get('response_status', '?')} | {size} | {cat} |"
        )
    lines.append("")

    # Grouped endpoints
    lines += ["---", "", "## 3. Endpoint Details", ""]

    for endpoint, recs in groups.items():
        first = recs[0]
        cat = categorize_path(first["path"], categories)
        cat_label = f" `[{cat}]`" if cat else ""

        lines.append(f"### `{endpoint}`{cat_label}")
        lines.append("")
        lines.append(f"- Calls: {len(recs)}")
        lines.append(f"- Domain: `{first.get('domain', '?')}`")
        lines.append(f"- Example URL: `{first['url'][:150]}`")
        lines.append(f"- Response Status: {first.get('response_status', '?')}")
        lines.append("")

        # Notable headers
        interesting_headers = {}
        skip = {"cookie", "user-agent", "accept", "accept-language", "accept-encoding",
                "connection", "host", "origin", "referer", "content-length",
                "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
                "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site"}
        for k, v in first.get("request_headers", {}).items():
            if k.lower() not in skip:
                interesting_headers[k] = str(v)[:100]
        if interesting_headers:
            lines.append("**Notable Headers:**")
            for k, v in interesting_headers.items():
                lines.append(f"- `{k}`: `{v}`")
            lines.append("")

        # Query params
        if first.get("query_params"):
            lines.append("**Query Params:**")
            lines.append("```json")
            lines.append(json.dumps(first["query_params"], ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")

        # Request body
        if first.get("request_body"):
            lines.append("**Request Body:**")
            lines.append("```json")
            body_str = first["request_body"]
            if isinstance(body_str, dict):
                body_str = json.dumps(body_str, ensure_ascii=False, indent=2)
            lines.append(str(body_str)[:2000])
            lines.append("```")
            lines.append("")

        # Response body
        if first.get("response_body"):
            lines.append("**Response Body (sample):**")
            lines.append("```json")
            body_str = first["response_body"]
            if isinstance(body_str, dict):
                body_str = json.dumps(body_str, ensure_ascii=False, indent=2)
            lines.append(str(body_str)[:2000])
            lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append("")

    # WebSocket section
    if ws_records:
        lines += ["## 4. WebSocket Connections", ""]

        # Group by conn_id
        ws_by_conn: dict[int, list[dict]] = {}
        for wr in ws_records:
            cid = wr["conn_id"]
            ws_by_conn.setdefault(cid, []).append(wr)

        for conn_id, msgs in ws_by_conn.items():
            first = msgs[0]
            sent = sum(1 for m in msgs if m["direction"] == "sent")
            recv = sum(1 for m in msgs if m["direction"] == "received")
            lines.append(f"### WebSocket #{conn_id}")
            lines.append("")
            lines.append(f"- URL: `{first['url'][:150]}`")
            lines.append(f"- Domain: `{first['domain']}`")
            lines.append(f"- Messages: {len(msgs)} ({sent} sent, {recv} received)")
            lines.append("")

            # Show first 10 messages as samples
            lines.append("**Sample Messages:**")
            lines.append("")
            lines.append("| # | Dir | Size | Payload (preview) |")
            lines.append("|---|-----|------|-------------------|")
            for msg in msgs[:10]:
                direction = "→ SENT" if msg["direction"] == "sent" else "← RECV"
                size = msg.get("payload_size", 0)
                payload = msg.get("payload", "")
                if isinstance(payload, dict):
                    preview = json.dumps(payload, ensure_ascii=False)[:80]
                else:
                    preview = str(payload)[:80]
                preview = preview.replace("|", "\\|")
                lines.append(f"| {msg['ws_seq']} | {direction} | {size} | `{preview}` |")

            if len(msgs) > 10:
                lines.append(f"| ... | ... | ... | *{len(msgs) - 10} more messages* |")
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


# ─── Main Capture Logic ─────────────────────────────────────────────

def _extract_set_cookie_names(headers: dict) -> list[str]:
    """Extract cookie names from response headers (CDP format).

    CDP may provide Set-Cookie as a single \\n-separated string or as
    individual entries depending on the browser version.
    """
    names = []
    for key, val in headers.items():
        if key.lower() == "set-cookie":
            for line in val.split("\n"):
                line = line.strip()
                if line and "=" in line:
                    name = line.split("=", 1)[0].strip()
                    if name:
                        names.append(name)
    return names


async def run_capture(profile: dict, url_override: str | None, filter_override: str | None):
    from playwright.async_api import async_playwright

    url = url_override or profile.get("url")
    records: list[dict] = []
    ws_records: list[dict] = []
    seq = 0
    ws_seq = 0
    start_time = time.time()

    profile_name = profile.get("name", "default")
    print(f"\n╔══════════════════════════════════════════════════╗")
    print(f"║          API Scout — API Capture Tool            ║")
    print(f"╠══════════════════════════════════════════════════╣")
    print(f"║  Profile: {profile_name:<39s}║")
    print(f"║  Browser is opening. Please:                     ║")
    print(f"║  1. Log in / navigate to the target site         ║")
    print(f"║  2. Perform the actions you want to capture      ║")
    print(f"║  3. Close the browser when done                  ║")
    print(f"╚══════════════════════════════════════════════════╝\n")

    # Apply filter override to profile
    if filter_override:
        profile["filter_domains"] = [filter_override]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            no_viewport=True,
            locale="zh-CN",
        )

        page = await context.new_page()

        # ── CDP-based network capture ──────────────────────────────
        # Using CDP gives us precise Set-Cookie headers, request
        # initiators, and proper redirect chain tracking that
        # Playwright's high-level API merges or loses.

        cdp = await context.new_cdp_session(page)
        await cdp.send("Network.enable")

        # Track pending requests (requestId → request info)
        _pending: dict[str, dict] = {}

        def on_request_will_be_sent(params):
            """CDP Network.requestWillBeSent — store request details."""
            req = params["request"]
            _pending[params["requestId"]] = {
                "method": req["method"],
                "url": req["url"],
                "headers": req.get("headers", {}),
                "post_data": req.get("postData"),
                "resource_type": params.get("type", "Other"),
                "initiator": params.get("initiator", {}),
                "redirect_chain": [],
            }

        def on_request_will_be_sent_redirect(params):
            """Handle redirects — preserve Set-Cookie from intermediate hops."""
            rid = params["requestId"]
            redirect_resp = params.get("redirectResponse")
            if rid in _pending and redirect_resp:
                # Save redirect hop info
                _pending[rid].setdefault("redirect_chain", []).append({
                    "url": redirect_resp.get("url", ""),
                    "status": redirect_resp.get("status"),
                    "set_cookies": _extract_set_cookie_names(
                        redirect_resp.get("headers", {})),
                })
            # Update request info for the new destination
            on_request_will_be_sent(params)

        async def on_response_received(params):
            """CDP Network.responseReceived — match with pending request, build record."""
            nonlocal seq
            rid = params["requestId"]
            pending = _pending.get(rid)
            if not pending:
                return

            resp = params["response"]
            req_url = resp.get("url") or pending["url"]
            resource_type = params.get("type", pending.get("resource_type", "Other")).lower()

            # Content types
            resp_headers = resp.get("headers", {})
            resp_ct = resp_headers.get("content-type", resp_headers.get("Content-Type", ""))
            req_ct = pending["headers"].get("content-type",
                                            pending["headers"].get("Content-Type", ""))

            # Apply profile-aware filtering
            if not is_api_request(req_url, resp_ct or req_ct, resource_type, profile):
                return

            # Fetch response body via CDP
            resp_body = None
            resp_body_size = 0
            try:
                body_result = await cdp.send("Network.getResponseBody",
                                             {"requestId": rid})
                raw_body = body_result.get("body", "")
                if body_result.get("base64Encoded"):
                    raw_bytes = base64.b64decode(raw_body)
                    resp_body_size = len(raw_bytes)
                    resp_body = safe_body(raw_bytes, resp_ct)
                else:
                    resp_body_size = len(raw_body.encode("utf-8", errors="replace"))
                    resp_body = safe_body(raw_body, resp_ct)
            except Exception:
                pass

            # Extract Set-Cookie — CDP gives separate headers properly
            set_cookie_names = _extract_set_cookie_names(resp_headers)

            # Also collect Set-Cookies from redirect chain
            for hop in pending.get("redirect_chain", []):
                set_cookie_names.extend(hop.get("set_cookies", []))
            # Deduplicate while preserving order
            seen = set()
            unique_sc = []
            for name in set_cookie_names:
                if name not in seen:
                    seen.add(name)
                    unique_sc.append(name)
            set_cookie_names = unique_sc

            seq += 1
            parsed = urlparse(req_url)
            elapsed = round(time.time() - start_time, 2)

            # Normalize request headers to lowercase keys (match Playwright behavior)
            req_headers = {k.lower(): v for k, v in pending["headers"].items()}

            record = {
                "seq": seq,
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": elapsed,
                "method": pending["method"],
                "url": req_url,
                "path": parsed.path,
                "normalized_path": normalize_path(parsed.path),
                "query_params": {k: v[0] if len(v) == 1 else v
                                 for k, v in parse_qs(parsed.query).items()},
                "domain": parsed.netloc,
                "resource_type": resource_type,
                "request_headers": req_headers,
                "request_body": safe_body(pending.get("post_data"), req_ct),
                "response_status": resp["status"],
                "response_headers": {k.lower(): v for k, v in resp_headers.items()},
                "response_body": resp_body,
                "response_body_size": resp_body_size,
                "set_cookies": set_cookie_names,
                "initiator_type": pending.get("initiator", {}).get("type", ""),
            }

            # Add category if profile defines one
            categories = profile.get("api_categories", {})
            cat = categorize_path(parsed.path, categories)
            if cat:
                record["category"] = cat

            records.append(record)

            # Live output
            status_icon = "✓" if 200 <= resp["status"] < 400 else "✗"
            cat_str = f" [{cat}]" if cat else ""
            print(f"  [{seq:3d}] {status_icon} {pending['method']:6s} {resp['status']} "
                  f"{parsed.path[:70]}{cat_str}")

        cdp.on("Network.requestWillBeSent", lambda params:
               on_request_will_be_sent_redirect(params)
               if "redirectResponse" in params
               else on_request_will_be_sent(params))
        cdp.on("Network.responseReceived", lambda params:
               asyncio.ensure_future(on_response_received(params)))

        # ── CDP-based WebSocket capture ────────────────────────────

        _ws_conns: dict[str, dict] = {}  # requestId → {conn_id, url, domain, path}

        def on_ws_created(params):
            nonlocal ws_seq
            ws_url = params.get("url", "")
            parsed_ws = urlparse(ws_url)

            # Apply domain filter
            if profile["filter_domains"]:
                if not any(d in parsed_ws.netloc for d in profile["filter_domains"]):
                    return

            ws_seq += 1
            conn_id = ws_seq
            _ws_conns[params["requestId"]] = {
                "conn_id": conn_id, "url": ws_url,
                "domain": parsed_ws.netloc, "path": parsed_ws.path,
            }
            print(f"  [WS {conn_id}] Connected: {parsed_ws.netloc}{parsed_ws.path[:60]}")

        def _on_ws_frame(params, direction):
            nonlocal ws_seq
            conn = _ws_conns.get(params["requestId"])
            if not conn:
                return
            resp = params.get("response", {})
            data = resp.get("payloadData", "")
            ws_seq += 1
            payload = safe_body(data)
            ws_records.append({
                "ws_seq": ws_seq,
                "conn_id": conn["conn_id"],
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": round(time.time() - start_time, 2),
                "direction": direction,
                "url": conn["url"],
                "domain": conn["domain"],
                "path": conn["path"],
                "payload": payload,
                "payload_size": len(data) if isinstance(data, (str, bytes)) else 0,
            })
            arrow = "→ SENT" if direction == "sent" else "← RECV"
            preview = str(payload)[:60] if payload else ""
            print(f"  [WS {conn['conn_id']}] {arrow}  {preview}")

        def on_ws_closed(params):
            conn = _ws_conns.pop(params.get("requestId"), None)
            if conn:
                print(f"  [WS {conn['conn_id']}] Closed")

        cdp.on("Network.webSocketCreated", on_ws_created)
        cdp.on("Network.webSocketFrameSent",
               lambda p: _on_ws_frame(p, "sent"))
        cdp.on("Network.webSocketFrameReceived",
               lambda p: _on_ws_frame(p, "received"))
        cdp.on("Network.webSocketClosed", on_ws_closed)

        # Navigate
        if url:
            print(f"Navigating to: {url}\n")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"Navigation warning: {e}")
        else:
            print("Blank page opened. Navigate to your target site.\n")

        # Wait for browser to close
        try:
            await page.wait_for_event("close", timeout=0)
        except Exception:
            pass

        # Detach CDP session before closing
        try:
            await cdp.detach()
        except Exception:
            pass

        # Extract cookies from browser context before closing
        browser_cookies = {}
        try:
            raw_cookies = await context.cookies()
            for c in raw_cookies:
                browser_cookies[c["name"]] = c["value"]
            if browser_cookies:
                cookie_str = "; ".join(f"{k}={v}" for k, v in browser_cookies.items())
                # Inject cookie header into all records that lack it
                for rec in records:
                    if not rec["request_headers"].get("cookie"):
                        rec["request_headers"]["cookie"] = cookie_str
                print(f"\n  Extracted {len(browser_cookies)} cookies from browser context")
        except Exception:
            pass

        try:
            await context.close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass

    return records, ws_records


def save_results(records: list[dict], ws_records: list[dict],
                 profile: dict, url: str | None):
    """Save capture results to three locations:

    - captures/{domain}_{ts}.json  — raw data with sensitive info (gitignored)
    - reports/{domain}_{ts}.md     — sanitized analysis report (safe to share/commit)
    - credentials/{domain}.json    — extracted cookies/tokens (gitignored)
    """
    if not records and not ws_records:
        print("\nNo API requests captured.")
        return None, None

    # Determine output filenames
    profile_name = profile.get("name", "unknown")
    domain = profile_name
    if url:
        domain = urlparse(url).netloc.replace(".", "_").replace(":", "_")
    elif records:
        domain = urlparse(records[0]["url"]).netloc.replace(".", "_").replace(":", "_")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for d in (CAPTURES_DIR, REPORTS_DIR, CREDENTIALS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # ── 1. Raw capture (contains sensitive data) ──
    raw_json_path = CAPTURES_DIR / f"{domain}_{timestamp}.json"
    auth_info = detect_auth_patterns(records, profile)
    groups = group_endpoints(records)
    anomalies = detect_path_anomalies(records)
    set_cookie_map = detect_set_cookies(records)

    raw_output = {
        "meta": {
            "captured_at": datetime.now().isoformat(),
            "profile": profile_name,
            "url": url or profile.get("url"),
            "domain": domain,
            "total_requests": len(records),
            "total_ws_messages": len(ws_records),
            "unique_endpoints": len(groups),
        },
        "profile": profile,
        "auth_analysis": auth_info,
        "anomalies": anomalies,
        "set_cookie_map": set_cookie_map,
        "endpoints": {k: len(v) for k, v in groups.items()},
        "records": records,
        "ws_records": ws_records,
    }
    raw_json_path.write_text(
        json.dumps(raw_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── 2. Credentials (extracted cookies/tokens) ──
    creds = extract_credentials(records)
    creds_path = CREDENTIALS_DIR / f"{domain}.json"

    # Merge with existing credentials (don't overwrite previous captures)
    if creds_path.exists():
        try:
            existing = json.loads(creds_path.read_text(encoding="utf-8"))
            existing.setdefault("cookies", {}).update(creds["cookies"])
            existing.setdefault("tokens", {}).update(creds["tokens"])
            if creds["full_cookie_string"]:
                existing["full_cookie_string"] = creds["full_cookie_string"]
            existing["last_updated"] = datetime.now().isoformat()
            creds = existing
        except (json.JSONDecodeError, KeyError):
            pass

    creds.setdefault("last_updated", datetime.now().isoformat())
    creds_path.write_text(
        json.dumps(creds, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── 3. Sanitized report (safe to share) ──
    sanitized_records = [sanitize_record(r) for r in records]
    sanitized_groups = group_endpoints(sanitized_records)
    report_md_path = REPORTS_DIR / f"{domain}_{timestamp}.md"
    report_json_path = REPORTS_DIR / f"{domain}_{timestamp}.json"

    # Sanitized JSON (no raw cookies/tokens)
    sanitized_output = {
        "meta": raw_output["meta"],
        "profile": profile,
        "auth_analysis": auth_info,
        "anomalies": anomalies,
        "set_cookie_map": set_cookie_map,
        "endpoints": {k: len(v) for k, v in sanitized_groups.items()},
        "records": sanitized_records,
        "ws_records": ws_records,
    }
    report_json_path.write_text(
        json.dumps(sanitized_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Sanitized Markdown
    md_content = generate_markdown(sanitized_records, auth_info, sanitized_groups,
                                   profile, ws_records, anomalies, set_cookie_map)
    report_md_path.write_text(md_content, encoding="utf-8")

    print(f"\n{'='*55}")
    print(f"  Capture complete! (profile: {profile_name})")
    print(f"  Requests:    {len(records)}")
    print(f"  Endpoints:   {len(groups)}")
    if ws_records:
        ws_conns = len(set(r["conn_id"] for r in ws_records))
        print(f"  WebSocket:   {len(ws_records)} messages across {ws_conns} connection(s)")
    print(f"{'='*55}")
    print(f"  Raw data:    {raw_json_path}")
    print(f"  Credentials: {creds_path}")
    print(f"  Report (md): {report_md_path}")
    print(f"  Report (json): {report_json_path}")
    print(f"{'='*55}")

    return report_md_path, report_json_path


def main():
    parser = argparse.ArgumentParser(description="API Scout — Universal API Capture Tool")
    parser.add_argument("--profile", "-p", help="Profile name (loads profiles/<name>.yaml)")
    parser.add_argument("--url", "-u", help="Starting URL (overrides profile url)")
    parser.add_argument("--filter", "-f", help="Domain filter (overrides profile filter_domains)")
    args = parser.parse_args()

    profile = load_profile(args.profile)
    url = args.url or profile.get("url")

    import asyncio
    records, ws_records = asyncio.run(run_capture(profile, args.url, args.filter))
    save_results(records, ws_records, profile, url)


if __name__ == "__main__":
    main()
