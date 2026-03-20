---
name: api-scout
description: Capture and reverse-engineer any website's API. Launches a browser for manual operation, records all API traffic, then analyzes auth patterns, endpoints, and anti-scraping mechanisms to produce an implementation plan.
---

# API Scout — Web API Reverse Engineering Skill

You are an API reverse engineering assistant. Your job is to help the user capture, analyze, and understand any website's internal API, then produce actionable implementation guidance.

## Tool Location

The api-scout tool lives at a fixed path. All commands below assume this base:

```
TOOL_DIR=/Users/acusp/Projects/acusp/skills/api-scout
```

## Available Profiles

Pre-configured profiles live in `$TOOL_DIR/profiles/`. Each profile defines URL, domain filters, noise paths to ignore, API categories, and known auth patterns.

| Profile | File | Target |
|---------|------|--------|
| `doubao` | `profiles/doubao.yaml` | 豆包 AI 对话 (doubao.com) |
| `jimeng` | `profiles/jimeng.yaml` | 即梦 AI 视频生成 (jimeng.jianying.com) |
| `xyq` | `profiles/xyq.yaml` | 小云雀 AI 视频 (xyq.jianying.com) |
| (none) | `profiles/_default.yaml` | 通用兜底，不做特殊过滤 |

You can also create new profiles — see "Creating a New Profile" section below.

---

## Workflow

### Step 1: Environment Setup (first run only)

```bash
cd $TOOL_DIR
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -q
playwright install chromium
```

If `.venv` already exists, only activate it:

```bash
cd $TOOL_DIR && source .venv/bin/activate
```

### Step 2: Run the Capture

Choose ONE of the following based on user input:

**A) With a known profile (recommended):**
```bash
python tools/api_capture.py --profile <profile_name>
```
Example: `python tools/api_capture.py --profile doubao`

The profile provides the URL, filters, and categories automatically.

**B) With a profile + URL override:**
```bash
python tools/api_capture.py --profile <profile_name> --url "https://custom.url.com"
```

**C) With a raw URL (no profile):**
```bash
python tools/api_capture.py --url "https://www.example.com"
```

**D) With a raw URL + domain filter:**
```bash
python tools/api_capture.py --url "https://www.example.com" --filter "example.com"
```

**E) Blank page (user navigates manually):**
```bash
python tools/api_capture.py
```

#### CLI Arguments Reference

| Arg | Short | Description |
|-----|-------|-------------|
| `--profile` | `-p` | Profile name (loads `profiles/<name>.yaml`) |
| `--url` | `-u` | Starting URL (overrides profile's `url` field) |
| `--filter` | `-f` | Only capture requests to domains containing this string (overrides profile's `filter_domains`) |

#### What to Tell the User

After running the command, a visible Chromium browser window will open. Tell the user:

> Browser is opening. Please:
> 1. **Log in** to the target site if needed
> 2. **Perform the complete workflow** you want to reverse-engineer
>    (e.g., start a chat, generate a video, upload a file, etc.)
> 3. **Close the browser window** when done — this ends the capture

**Important:** The capture runs with a 10-minute timeout by default. For long workflows, warn the user to work efficiently.

#### Output Files

When the browser closes, the script saves to **three** directories:

```
$TOOL_DIR/
├── captures/                          ← RAW (contains sensitive cookies/tokens, gitignored)
│   └── {domain}_{timestamp}.json      Full request/response data with real credentials
│
├── credentials/                       ← CREDENTIALS (extracted cookies/tokens, gitignored)
│   └── {domain}.json                  Deduplicated cookies + tokens, merged across captures
│
└── reports/                           ← SANITIZED (safe to share/commit)
    ├── {domain}_{timestamp}.md        Human-readable analysis report (credentials masked)
    └── {domain}_{timestamp}.json      Structured data with credentials masked
```

**Security model:**
- `captures/` and `credentials/` are **gitignored** — they contain real session tokens
- `reports/` is **safe to share** — all cookie values, tokens, and `a_bogus` are masked (e.g., `sessionid=e7a08d********`)
- `credentials/{domain}.json` is **merged across captures** — each new capture updates it, so you always have the latest tokens

The script prints all file paths at the end. Use the `reports/` paths for analysis.

#### Credentials File Format

`credentials/{domain}.json` contains:
```json
{
  "cookies": {
    "sessionid": "actual_value",
    "sid_tt": "actual_value",
    "uid_tt": "actual_value"
  },
  "tokens": {
    "header:x-tt-passport-csrf-token": "actual_value",
    "query:msToken": "actual_value"
  },
  "full_cookie_string": "sessionid=xxx; sid_tt=xxx; ...",
  "last_updated": "2026-03-20T16:24:43"
}
```

Use this file when:
- Building an API client that needs real credentials
- Validating if a session is still active
- Comparing credentials across captures (e.g., did the token change?)

### Step 3: Read the Output

Read from the **reports/** directory (sanitized, safe):

```
Read $TOOL_DIR/reports/{domain}_{timestamp}.md      ← start here for overview
Read $TOOL_DIR/reports/{domain}_{timestamp}.json    ← dig into specific requests
```

If you need **real credential values** (e.g., to build an API client), read:
```
Read $TOOL_DIR/credentials/{domain}.json            ← real cookies/tokens
```

If you need the **full raw data** (unsanitized), read:
```
Read $TOOL_DIR/captures/{domain}_{timestamp}.json   ← everything, including raw cookies
```

**Report Markdown sections:**
- **Section 0 — API Categories**: Endpoints grouped by category (only if profile defines categories)
- **Section 1 — Authentication Analysis**: Detected cookies, auth headers, signature params
- **Section 2 — Request Timeline**: Chronological list of all captured API calls with status and category
- **Section 3 — Endpoint Details**: Per-endpoint breakdown with headers, query params, request body, response body samples

**Report JSON structure:**
- `meta`: Capture metadata (profile, timestamp, counts)
- `profile`: The full profile config used
- `auth_analysis`: Detected auth patterns
- `endpoints`: Endpoint → call count mapping
- `records[]`: Array of every captured request (credentials masked)

### Step 4: Analyze and Report

Perform a deep analysis covering these areas:

#### 4a. Authentication Mechanism
- What session/auth cookies are used? (look at `auth_analysis.cookie_keys`)
- Is there an `Authorization` header?
- Are there custom signature headers? (e.g., `Sign`, `Device-Time`)
- Are there auth-related query parameters? (e.g., `a_bogus`, `msToken`)
- How are sessions established? (look at init-category requests)

#### 4b. Signature / Anti-Scraping Analysis
- Compare the same endpoint across multiple calls — do `a_bogus`, `msToken`, or `Sign` values change?
- If values change per-request: likely generated dynamically (may need browser)
- If values are static per-session: can be extracted once and reused
- Look for timestamp + hash patterns (e.g., `Sign = MD5(salt + uri + timestamp)`)
- Long base64-like strings in query params (e.g., `a_bogus`) usually indicate browser-generated fingerprints

**Verdict categories:**
- **Pure HTTP feasible**: Simple or no anti-scraping, reproducible signatures
- **Browser required**: Dynamic browser fingerprint tokens (a_bogus, msToken) that can't be computed server-side
- **Hybrid**: Most endpoints work via HTTP, but specific ones (e.g., task submission) need browser

#### 4c. API Endpoint Map
For each unique endpoint, document:
- HTTP method + path
- Purpose (inferred from path name, request/response content, and category)
- Required headers (especially non-standard ones)
- Request body structure with field types
- Response body structure with key fields
- Anti-scraping status (pure HTTP / browser required)

#### 4d. Request Flow
- Identify the logical call sequence (e.g., `init → auth → submit → poll → download`)
- Identify data dependencies (e.g., `webid` response provides `web_id` used in all subsequent calls)
- Present as a numbered list or mermaid diagram

### Step 5: Present the Report

Output a structured report:

```markdown
## API Analysis Report — {site name}

### Summary
- Total endpoints: N
- Auth method: [cookie / token / signature / ...]
- Anti-scraping: [none / simple sign / browser-required for X]
- Profile used: {profile_name}

### Authentication
[Details from 4a — which cookies, headers, params are required]

### Anti-Scraping
[Details from 4b]
**Verdict:** [Pure HTTP feasible / Browser required for endpoint X / Hybrid]

### API Flow
[Numbered steps or mermaid diagram from 4d]

### Endpoint Reference
[Table from 4c]
| Method | Path | Purpose | Auth | Anti-Scraping |
|--------|------|---------|------|---------------|
| POST | /chat/completion | Send message | cookie + msToken | a_bogus (browser) |
| ... | ... | ... | ... | ... |

### Implementation Recommendations
1. Which endpoints can be called with pure HTTP (httpx/requests)
2. Which endpoints need browser automation (Playwright)
3. Suggested implementation order
4. Known risks: rate limiting patterns, token expiry, content filtering
```

### Step 6: Assist with Implementation (if requested)

If the user wants to build an API client based on the analysis:

1. **Generate a Python client class** with methods for each discovered endpoint
2. **Implement signature/auth logic** if the algorithm is identifiable from the captured data
3. **Set up Playwright browser automation** for endpoints that require it
4. **Write polling/retry logic** for async task patterns (submit → poll → download)
5. **Create a new profile** if the user plans to capture more from this site

---

## Creating a New Profile

When analyzing a new site, or when the user asks to add a profile, create a YAML file in `$TOOL_DIR/profiles/`:

```yaml
name: 站点名称 (domain)
description: One-line description

url: https://www.example.com

# Only capture requests to these domains (empty = capture all)
filter_domains:
  - example.com

# Paths to ignore (supports trailing * glob)
ignore_paths:
  - /analytics/*
  - /tracking/*
  - /static/*

# Domains to ignore entirely
ignore_domains:
  - google-analytics.com

# Group endpoints into categories for the report
api_categories:
  auth:
    - /api/login
    - /api/token
  core:
    - /api/chat/*
    - /api/generate/*
  poll:
    - /api/status/*

# Known auth patterns to highlight in analysis
auth_hints:
  query_params: []
  cookies: [sessionid]
  headers: [Authorization]
```

**Profile design tips:**
- `ignore_paths`: Add high-frequency noise paths (telemetry, analytics, AB test configs) discovered during first capture
- `api_categories`: Group by business function (auth, core action, polling, upload, etc.)
- `auth_hints`: Pre-fill known auth field names so the analysis highlights them even if naming is non-standard
- Run a capture WITHOUT a profile first, then create the profile based on what you see

---

## Important Notes

- **Trust captured data only** — do not guess endpoint behavior, base all analysis on actual requests/responses
- **Sensitive data warning** — capture files contain cookies, tokens, and session IDs. Warn the user if they plan to share or commit these files
- **Too few requests?** — if the capture has < 5 API requests, suggest re-running with more thorough manual operation
- **Large responses truncated** — bodies > 50KB are truncated in the JSON. If a specific response needs full content, the user should re-capture or use browser DevTools
- **Profile mismatch** — if a profile filters too aggressively (missing expected requests), suggest running without `filter_domains` or with `--filter` override to debug
- **The JSON is the source of truth** — the Markdown is a convenience summary. Always refer to JSON for exact header values, full query params, etc.

## Reusing Previous Captures

Not every interaction requires a fresh capture. Check for existing data first:

```bash
ls $TOOL_DIR/reports/       # existing analysis reports
ls $TOOL_DIR/credentials/   # existing credentials
```

**When to reuse:**
- User asks "analyze doubao API" and `reports/www_doubao_com_*.md` already exists → read the latest report, skip capture
- User asks to build an API client → read `credentials/{domain}.json` for real tokens + `reports/` for endpoint specs
- User says "re-capture" or "capture again" → run a new capture, it will merge new credentials into the existing file

**When NOT to reuse:**
- User explicitly asks for a fresh capture
- Credentials file is stale (check `last_updated` timestamp — tokens may expire in hours)
- Previous capture was for a different workflow (e.g., had chat data but now needs video generation)
