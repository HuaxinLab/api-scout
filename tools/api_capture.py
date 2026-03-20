"""API Scout — Universal API Capture Tool.

Launches a visible browser, captures all API requests while you manually
operate any website, then outputs structured JSON + Markdown summary.

Usage:
    python tools/api_capture.py --url "https://www.doubao.com"
    python tools/api_capture.py --url "https://example.com" --filter "example.com"
    python tools/api_capture.py  # opens blank page
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ─── Config ──────────────────────────────────────────────────────────

MAX_BODY_SIZE = 50 * 1024  # 50KB, truncate beyond this
SKIP_RESOURCE_TYPES = {"image", "font", "stylesheet", "media", "manifest", "other"}
SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".avif",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".css", ".map",
    ".mp3", ".mp4", ".webm", ".ogg", ".wav",
}
API_CONTENT_TYPES = {"json", "text", "form", "protobuf", "grpc", "xml", "html"}


# ─── Helpers ─────────────────────────────────────────────────────────

def is_api_request(url: str, content_type: str | None, resource_type: str) -> bool:
    """Determine if a request is an API call (not a static resource)."""
    if resource_type in SKIP_RESOURCE_TYPES:
        return False
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in SKIP_EXTENSIONS:
        return False
    # Keep everything that looks like an API call
    if resource_type in ("xhr", "fetch"):
        return True
    # Check content type
    if content_type:
        ct = content_type.lower()
        if any(t in ct for t in API_CONTENT_TYPES):
            return True
    # Keep requests with no extension or common API paths
    if not suffix or any(p in parsed.path for p in ("/api/", "/v1/", "/v2/", "/mweb/", "/rpc/", "/graphql")):
        return True
    return False


def safe_body(body: bytes | str | None, content_type: str | None = None) -> str | dict | None:
    """Decode and truncate body safely."""
    if body is None:
        return None
    if isinstance(body, bytes):
        if len(body) > MAX_BODY_SIZE:
            return f"<binary {len(body)} bytes, truncated>"
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary {len(body)} bytes>"
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


def detect_auth_patterns(records: list[dict]) -> dict:
    """Analyze captured records to identify authentication patterns."""
    auth_info = {
        "cookie_keys": set(),
        "auth_headers": set(),
        "custom_sign_headers": set(),
        "query_auth_params": set(),
    }
    sign_keywords = {"sign", "token", "key", "auth", "secret", "signature", "nonce", "timestamp", "bogus"}

    for rec in records:
        headers = rec.get("request_headers", {})
        # Cookie analysis
        cookie = headers.get("cookie", headers.get("Cookie", ""))
        if cookie:
            for part in cookie.split(";"):
                if "=" in part:
                    name = part.split("=")[0].strip().lower()
                    if any(k in name for k in ("session", "token", "sid", "uid", "auth", "login")):
                        auth_info["cookie_keys"].add(part.split("=")[0].strip())
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

    # Convert sets to lists for JSON serialization
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


# ─── Markdown Report ─────────────────────────────────────────────────

def generate_markdown(records: list[dict], auth_info: dict, groups: dict, domain: str) -> str:
    """Generate a human-readable Markdown analysis report."""
    lines = [
        f"# API Capture Report — {domain}",
        f"",
        f"Captured at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total API requests: {len(records)}",
        f"Unique endpoints: {len(groups)}",
        "",
        "---",
        "",
        "## 1. Authentication Analysis",
        "",
    ]

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

    # Timeline
    lines += [
        "---",
        "",
        "## 2. Request Timeline",
        "",
        "| # | Method | URL | Status | Size |",
        "|---|--------|-----|--------|------|",
    ]
    for i, rec in enumerate(records, 1):
        url_short = rec["path"]
        if len(url_short) > 60:
            url_short = url_short[:57] + "..."
        size = rec.get("response_body_size", "?")
        lines.append(f"| {i} | {rec['method']} | `{url_short}` | {rec.get('response_status', '?')} | {size} |")
    lines.append("")

    # Grouped endpoints
    lines += [
        "---",
        "",
        "## 3. Endpoint Details",
        "",
    ]

    for endpoint, recs in groups.items():
        first = recs[0]
        lines.append(f"### `{endpoint}`")
        lines.append(f"")
        lines.append(f"- Calls: {len(recs)}")
        lines.append(f"- Example URL: `{first['url'][:120]}`")
        lines.append(f"- Response Status: {first.get('response_status', '?')}")
        lines.append("")

        # Request headers (deduplicated, show interesting ones)
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

        # Response body (first 500 chars)
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

    return "\n".join(lines)


# ─── Main Capture Logic ─────────────────────────────────────────────

async def run_capture(url: str | None, domain_filter: str | None):
    from playwright.async_api import async_playwright

    records: list[dict] = []
    seq = 0
    start_time = time.time()

    print("\n╔══════════════════════════════════════════════════╗")
    print("║          API Scout — API Capture Tool            ║")
    print("╠══════════════════════════════════════════════════╣")
    print("║  Browser is opening. Please:                     ║")
    print("║  1. Log in / navigate to the target site         ║")
    print("║  2. Perform the actions you want to capture      ║")
    print("║  3. Close the browser when done                  ║")
    print("╚══════════════════════════════════════════════════╝\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )

        page = await context.new_page()

        async def on_response(response):
            nonlocal seq
            request = response.request
            req_url = request.url
            resource_type = request.resource_type

            # Domain filter
            if domain_filter:
                parsed = urlparse(req_url)
                if domain_filter not in parsed.netloc:
                    return

            # Get response content type
            resp_ct = None
            try:
                resp_ct = response.headers.get("content-type", "")
            except Exception:
                pass

            # Check if this is an API request
            req_ct = request.headers.get("content-type", "")
            if not is_api_request(req_url, resp_ct or req_ct, resource_type):
                return

            seq += 1
            parsed = urlparse(req_url)
            elapsed = round(time.time() - start_time, 2)

            # Capture request body
            req_body = None
            try:
                req_body = request.post_data
            except Exception:
                pass

            # Capture response body
            resp_body = None
            resp_body_size = 0
            try:
                raw = await response.body()
                resp_body_size = len(raw)
                resp_body = safe_body(raw, resp_ct)
            except Exception:
                pass

            record = {
                "seq": seq,
                "timestamp": datetime.now().isoformat(),
                "elapsed_seconds": elapsed,
                "method": request.method,
                "url": req_url,
                "path": parsed.path,
                "normalized_path": normalize_path(parsed.path),
                "query_params": {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()},
                "domain": parsed.netloc,
                "resource_type": resource_type,
                "request_headers": dict(request.headers),
                "request_body": safe_body(req_body, req_ct),
                "response_status": response.status,
                "response_headers": dict(response.headers),
                "response_body": resp_body,
                "response_body_size": resp_body_size,
            }
            records.append(record)

            # Live output
            status_icon = "✓" if 200 <= response.status < 400 else "✗"
            print(f"  [{seq:3d}] {status_icon} {request.method:6s} {response.status} {parsed.path[:80]}")

        page.on("response", on_response)

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

        try:
            await context.close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass

    return records


def save_results(records: list[dict], url: str | None):
    """Save capture results to JSON and Markdown files."""
    if not records:
        print("\nNo API requests captured.")
        return None, None

    # Determine output filenames
    domain = "unknown"
    if url:
        domain = urlparse(url).netloc.replace(".", "_").replace(":", "_")
    elif records:
        domain = urlparse(records[0]["url"]).netloc.replace(".", "_").replace(":", "_")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent.parent / "captures"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{domain}_{timestamp}.json"
    md_path = out_dir / f"{domain}_{timestamp}.md"

    # Analysis
    auth_info = detect_auth_patterns(records)
    groups = group_endpoints(records)

    # Save JSON
    output = {
        "meta": {
            "captured_at": datetime.now().isoformat(),
            "url": url,
            "domain": domain,
            "total_requests": len(records),
            "unique_endpoints": len(groups),
        },
        "auth_analysis": auth_info,
        "endpoints": {k: len(v) for k, v in groups.items()},
        "records": records,
    }
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # Save Markdown
    md_content = generate_markdown(records, auth_info, groups, domain)
    md_path.write_text(md_content, encoding="utf-8")

    print(f"\n{'='*50}")
    print(f"Capture complete!")
    print(f"  Requests: {len(records)}")
    print(f"  Endpoints: {len(groups)}")
    print(f"  JSON: {json_path}")
    print(f"  Report: {md_path}")
    print(f"{'='*50}")

    return json_path, md_path


def main():
    parser = argparse.ArgumentParser(description="API Scout — Universal API Capture Tool")
    parser.add_argument("--url", "-u", help="Starting URL to navigate to")
    parser.add_argument("--filter", "-f", help="Only capture requests matching this domain")
    args = parser.parse_args()

    import asyncio
    records = asyncio.run(run_capture(args.url, args.filter))
    save_results(records, args.url)


if __name__ == "__main__":
    main()
