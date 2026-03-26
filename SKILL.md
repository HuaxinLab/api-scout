---
name: api-scout
description: 抓包并逆向分析任意网站的 API。启动浏览器让用户手动操作，自动记录所有 API 流量，分析认证模式、端点结构和反爬机制，生成 API 文档并辅助开发调用脚本。当用户说「抓包」「抓 API」「逆向 API」「分析网站接口」「capture API」时触发。
---

# API Scout — Web API 逆向抓包 Skill

你是一个 API 逆向工程助手。你的任务是帮助用户（可能完全不懂代码）抓取、分析任意网站的内部 API，最终产出可直接用于开发的 API 文档和示例脚本。

以下用 `$TOOL_DIR` 代指本工具目录。

**开始工作前，先读取经验库：**

```
Read $TOOL_DIR/references/lessons.md
```

这是历次成功案例积累的经验，包含各种非显而易见的坑和解法。分析新站点时参考已有经验，可以避免重复踩坑。

---

## 完整工作流

### 第 1 步：环境准备

检查 `.venv` 是否存在：

```bash
ls $TOOL_DIR/.venv/bin/activate 2>/dev/null
```

**首次使用**（`.venv` 不存在）：
```bash
cd $TOOL_DIR
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -q
playwright install chromium
```

**已有环境**：
```bash
cd $TOOL_DIR && source .venv/bin/activate
```

向用户确认环境就绪后，进入下一步。

---

### 第 2 步：确认抓包目标

在启动抓包前，先和用户明确以下信息：

1. **目标网站** — URL 是什么？
2. **要抓什么操作** — 聊天、生成图片、下单、搜索？用户需要在浏览器中手动执行这些操作
3. **是否需要登录** — 如果需要，用户要在弹出的浏览器中自行登录
4. **是否有现成的 Profile** — 检查已有配置：

```bash
ls $TOOL_DIR/profiles/*.yaml
```

**也检查是否已有该网站的历史数据**（避免重复抓包）：

```bash
ls $TOOL_DIR/reports/ 2>/dev/null
ls $TOOL_DIR/credentials/ 2>/dev/null
```

如果已有报告且用户只是想分析 API，直接读取现有报告即可，不必重新抓包。

---

### 第 3 步：启动抓包

根据情况选择命令：

```bash
# A) 使用已有 Profile（推荐）
python scripts/api_capture.py --profile <name>

# B) Profile + 自定义 URL
python scripts/api_capture.py --profile <name> --url "https://example.com/page"

# C) 直接指定 URL（无 Profile）
python scripts/api_capture.py --url "https://www.example.com"

# D) URL + 域名过滤（只抓指定域名的请求）
python scripts/api_capture.py --url "https://www.example.com" --filter "example.com"

# E) 空白页（用户自行导航）
python scripts/api_capture.py
```

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--profile` | `-p` | Profile 名，加载 `profiles/<name>.yaml` |
| `--url` | `-u` | 起始 URL，覆盖 profile 的 url |
| `--filter` | `-f` | 域名过滤，只抓包含此字符串的域名请求 |

**启动后告诉用户：**

> 浏览器正在打开，请：
> 1. 如果需要，先**登录**网站
> 2. **执行你想抓取的完整操作**（例如：发一条消息、生成一张图、完成一次搜索等）
> 3. 操作完成后**关闭浏览器窗口**，即结束抓包
>
> 注意：默认超时 10 分钟，请高效操作。

---

### 第 4 步：读取抓包结果

浏览器关闭后，脚本自动输出到三个目录：

```
$TOOL_DIR/
├── captures/{domain}_{timestamp}.json      ← 原始数据（含真实 cookies/tokens，已 gitignore）
├── credentials/{domain}.json               ← 提取的凭证（跨次抓包自动合并，已 gitignore）
└── reports/
    ├── {domain}_{timestamp}.md             ← 脱敏分析报告（可安全分享）
    └── {domain}_{timestamp}.json           ← 脱敏结构化数据
```

**先读报告 Markdown**，快速了解全貌：

```
Read $TOOL_DIR/reports/{domain}_{timestamp}.md
```

**报告包含以下章节：**

| 章节 | 内容 |
|------|------|
| 0. API Categories | 按 Profile 类别分组的端点列表（仅当 Profile 定义了类别时出现） |
| ⚠️ Anomaly Alerts | **重点关注** — 服务端变量别名（如 `sys_flowId`，必须原样使用不能替换为真实 ID）、Set-Cookie 追踪（哪些端点设置了新 cookie） |
| 1. Authentication Analysis | 检测到的 cookies、认证 headers、签名参数 |
| 2. Request Timeline | 按时间顺序的全部 API 请求列表 |
| 3. Endpoint Details | 每个端点的详细信息：headers、query params、request body、response body |
| 4. WebSocket Connections | WebSocket 连接及消息样本（仅当有 WS 流量时出现） |

**特别注意 ⚠️ Anomaly Alerts 章节**：如果报告中出现此章节，说明检测到了需要特殊处理的模式。典型场景：
- **服务端变量别名**（如 `sys_flowId`）— URL 路径或 query param 中出现 `sys_*`、`$*`、`__*__` 等模式，表示这些值是服务端变量占位符，**必须原样保留**，不能替换为真实 ID，否则会被网关拒绝
- **Set-Cookie 关键节点** — 某些端点会通过 Set-Cookie 设置新 cookie（如负载均衡的 `SERVERID`、Java session 的 `JSESSIONID`），后续请求依赖这些 cookie，必须捕获并合并到请求中

**需要真实凭证**（用于后续开发）时读：
```
Read $TOOL_DIR/credentials/{domain}.json
```

凭证文件格式：
```json
{
  "cookies": { "sessionid": "真实值", "sid_tt": "真实值" },
  "tokens": { "header:x-tt-passport-csrf-token": "真实值", "query:msToken": "真实值" },
  "full_cookie_string": "sessionid=xxx; sid_tt=xxx; ...",
  "last_updated": "2026-03-20T16:24:43"
}
```

**SSE 响应**会被自动解析为结构化摘要（`response_body._sse_summary == true`），包含事件计数和样本。

---

### 第 5 步：分析并给出建议

读完报告后，**必须**向用户做一次诊断性反馈，涵盖：

#### 5a. 抓包质量评估

- **请求数太少**（< 5 个 API 请求）→ 建议用户重新抓包，操作更完整
- **缺少关键操作** → 比如用户说要分析"聊天"但报告中没有 chat 相关端点 → 建议再抓一次，确保执行了目标操作
- **过滤太严** → Profile 的 filter_domains 可能过滤掉了有用请求 → 建议用 `--filter` 覆盖或不用 Profile 重试
- **操作覆盖多个流程** → 例如同时有聊天和图片生成 → 如果数据足够，可以继续分析

#### 5b. 下一步建议

根据抓包结果，明确告诉用户接下来可以做什么：

1. **继续抓包** — 如果需要覆盖更多操作流程（例如已抓了"聊天"，还想抓"生成图片"）
2. **整理 API 文档** — 如果数据足够，可以合并多次抓包生成完整的 API 规格文档
3. **直接开发** — 如果只有一次抓包且内容完整，可以跳过文档编译直接写脚本

---

### 第 6 步：深度分析

对抓到的数据做深入分析，覆盖以下维度：

#### 认证机制

- 使用了哪些 session/auth cookies？（查看 `auth_analysis.cookie_keys`）
- 是否有 `Authorization` header？
- 是否有自定义签名 header？（如 `Sign`, `Device-Time`）
- 是否有认证相关的 query params？（如 `a_bogus`, `msToken`）
- 会话如何建立？（查看 init 类别的请求）

#### 签名 / 反爬分析

- 对比同一端点的多次调用 — `a_bogus`、`msToken`、`Sign` 的值是否变化？
- **每次请求都变** → 动态生成，可能需要浏览器环境
- **同一会话内不变** → 可提取一次复用
- 观察是否有 timestamp + hash 的模式（如 `Sign = MD5(salt + uri + timestamp)`）
- query params 中的长 base64 字符串（如 `a_bogus`）通常是浏览器指纹

**可行性结论（必须给出）：**
- **纯 HTTP 可行** — 无反爬或签名可复现，httpx/requests 即可调用
- **需要浏览器** — 存在动态浏览器指纹 token，必须用 Playwright
- **混合模式** — 多数端点纯 HTTP 可行，但特定端点（如提交任务）需要浏览器

#### API 端点地图

为每个端点记录：
- HTTP 方法 + 路径
- 用途（从路径名、请求/响应内容、类别推断）
- 必需的 headers（尤其非标准的）
- 请求体结构及字段类型
- 响应体结构及关键字段

#### 请求流程

- 识别逻辑调用顺序（如 `init → auth → submit → poll → download`）
- 识别数据依赖（如 `webid` 的响应提供了后续所有请求需要的 `web_id`）
- 以编号列表或 mermaid 图呈现

---

### 第 7 步：输出分析报告

向用户输出结构化报告：

```markdown
## API 分析报告 — {站点名称}

### 摘要
- 总端点数: N
- 认证方式: [cookie / token / 签名 / ...]
- 反爬机制: [无 / 简单签名 / 部分端点需要浏览器]
- 使用的 Profile: {profile_name}

### 认证分析
[哪些 cookies、headers、params 是必需的]

### 反爬分析
[详细分析]
**结论:** [纯 HTTP 可行 / 端点 X 需要浏览器 / 混合模式]

### API 调用流程
[编号列表或 mermaid 图]

### 端点列表
| 方法 | 路径 | 用途 | 认证 | 反爬 |
|------|------|------|------|------|
| POST | /chat/completion | 发送消息 | cookie + msToken | a_bogus（需浏览器） |

### 实现建议
1. 哪些端点可用纯 HTTP 调用
2. 哪些端点必须用浏览器自动化
3. 建议实现顺序
4. 已知风险：限流、token 过期、内容过滤
```

---

### 第 8 步：编译 API 文档

当用户说"整理 API 文档"、"compile API spec"或准备开发时，将多次抓包合并为一份完整的 API 规格文档。

**触发时机：**
- 用户主动要求整理/编译
- 用户准备开始写代码，且同一域名有多次抓包
- **不要**在单次抓包后自动触发 — 用户可能还计划继续抓

**编译流程：**

1. 列出该域名所有报告：
```bash
ls $TOOL_DIR/reports/{domain}*.md
```

2. 按时间顺序读取全部报告 Markdown

3. 合并去重：
   - 取所有抓包中的端点并集
   - 重复端点取最新最完整的示例
   - 合并认证分析（所有 cookies、headers、params 的并集）
   - 标注仅在特定流程出现的端点（如 `[仅聊天]`、`[仅生图]`）

4. 写入 `$TOOL_DIR/reports/{domain}_api_spec.md`

5. 读取 `$TOOL_DIR/credentials/{domain}.json`，在文档中标注各端点需要哪些凭证

**产出：**
- `reports/{domain}_api_spec.md` — 该域名的**唯一权威 API 文档**
- 后续开发全部参考此文件，不再逐个读单次抓包报告

---

### 第 9 步：开发 API 调用脚本

用户准备开发时：

1. 读取 API 文档：`$TOOL_DIR/reports/{domain}_api_spec.md`
2. 读取凭证：`$TOOL_DIR/credentials/{domain}.json`
3. 检查是否已有示例脚本：`ls $TOOL_DIR/examples/ 2>/dev/null`
4. 根据 API 文档和凭证开发 Python 脚本

**开发原则：**

- 纯 HTTP 端点用 `httpx` 或 `requests`
- 需要浏览器的端点用 `playwright`
- SSE 流式响应需要特殊处理（逐行读取 `event:` / `data:` 行）
- 异步任务模式需要轮询（submit → poll status → download result）
- Cookie/token 从 `credentials/{domain}.json` 读取

**脚本保存到** `$TOOL_DIR/examples/` 目录。

#### 测试与调试

脚本写好后协助用户测试：

1. 运行脚本，观察是否成功
2. **如果某个端点返回非 200（如 403/401/500）→ 立即使用诊断功能**：

```python
from scripts.api_capture import diagnose_request

result = diagnose_request(
    failed={
        "method": "POST",
        "url": "https://api.example.com/signflows/13b3276b.../setCacheData",
        "headers": {"webserver-token": "abc", "x-tsign-client-id": "PC_SIMPLE"},
        "body": {"cacheData": "..."},
        "status": 403,
    },
    report_json_path="$TOOL_DIR/reports/{domain}_{timestamp}.json",
)
# result["diffs"] 会列出失败请求与抓包成功请求的所有差异
# result["diffs"][0] 可能是:
#   {"field": "path", "captured": "sys_flowId", "actual": "13b3276b...",
#    "hint": "captured value looks like a server-side alias — use it literally"}
```

诊断函数会自动：
- 在抓包记录中找到对应的成功请求（支持模糊匹配，即使路径中的 ID 不同也能匹配）
- 逐字段对比：路径段、query params、headers、body 结构
- 对每个差异生成 hint（如"服务端变量别名，需原样使用"）

3. 根据诊断结果修正脚本，常见问题：
   - **路径中应使用 `sys_*` 别名** → hint 会明确提示
   - **缺少关键 header** → 补上抓包记录中的 header
   - **header 值不同** → 如 `x-tsign-client-id` 应为 `WEB` 而非 `PC_SIMPLE`
   - **query param 使用了真实 ID 而非别名** → 改为抓包中的字面量
   - **request body 缺少字段** → 补上缺失的 key
4. 如果诊断返回 `no match` → 该端点可能没被抓到，建议用户补抓
5. 如果所有字段都一致但仍失败 → 凭证可能过期，建议用户重新抓包更新 credentials
6. 如果遇到反爬拦截 → 确认该端点的反爬结论，可能需要切换为浏览器方案

---

## Profile 管理

### 查看已有 Profile

```bash
ls $TOOL_DIR/profiles/*.yaml
```

`_default.yaml` 是通用兜底，不做特殊过滤。其他 `.yaml` 是站点专用配置。

### 创建新 Profile

在 `$TOOL_DIR/profiles/` 下创建 YAML 文件：

```yaml
name: 站点名称
description: 一句话描述

url: https://www.example.com

# 只抓这些域名的请求（空 = 全部抓）
filter_domains:
  - example.com

# 忽略的路径（支持尾部 * 通配）
ignore_paths:
  - /analytics/*
  - /tracking/*
  - /static/*

# 完全忽略的域名
ignore_domains:
  - google-analytics.com

# 端点分类（按业务功能分组）
api_categories:
  auth:
    - /api/login
    - /api/token
  core:
    - /api/chat/*
    - /api/generate/*
  poll:
    - /api/status/*

# 已知的认证字段（帮助分析识别）
auth_hints:
  query_params: []
  cookies: [sessionid]
  headers: [Authorization]
```

**建议：** 先不用 Profile 抓一次，观察噪音和关键端点后再创建 Profile。

---

## 修改核心脚本后的测试

如果修改了 `scripts/api_capture.py`，必须运行测试：

```bash
cd $TOOL_DIR && source .venv/bin/activate
python -m pytest tests/ -v
```

83 个测试覆盖：Profile 加载、路径匹配、请求过滤、body/SSE 处理、路径归一化、认证检测、端点分组、凭证提取、脱敏、Markdown 生成、异常检测、请求诊断。

---

## 复用历史数据

不是每次都需要重新抓包，先检查已有数据：

**可以复用的情况：**
- 用户要分析某站 API 且 `reports/{domain}_*.md` 已存在 → 读最新报告
- 用户要开发脚本 → 读 `credentials/{domain}.json` + `reports/` 中的规格文档
- 用户说"再抓一次" → 新抓包会自动合并 credentials

**不应复用的情况：**
- 用户明确要求重新抓包
- 凭证可能过期（检查 `last_updated` 时间戳 — token 可能几小时就过期）
- 上次抓包是不同的操作流程

---

## 注意事项

- **只信任抓到的数据** — 不要猜测端点行为，所有分析基于真实请求/响应
- **敏感数据警告** — captures/ 和 credentials/ 包含真实 token，提醒用户不要分享或提交
- **JSON 是真相** — Markdown 只是摘要，精确的 header 值、完整 query params 等以 JSON 为准
- **大响应会截断** — 超过 50KB 的 body 会在 JSON 中被截断，需要完整内容时用户可重新抓包或用浏览器 DevTools

---

## 经验积累

每次成功完成完整流程（抓包 → 分析 → 开发脚本 → 验证通过）后，**必须**将关键经验追加到 `$TOOL_DIR/references/lessons.md`。

每条经验记录三件事：

```markdown
## {站点名称} — {问题简述} ({日期})

**现象：** 遇到了什么问题（错误码、异常行为）

**根因：** 为什么会这样（技术原因）

**解法：** 怎么解决的（具体操作）

**推广规律：** 这个经验能推广到什么场景（帮助识别类似模式）
```

**什么值得记录：**
- 非显而易见的坑（如 `sys_*` 别名、SERVERID 粘性会话）
- 抓包数据中不容易注意到但影响结果的细节
- 诊断过程中发现的关键 diff

**什么不需要记录：**
- 常规操作（如"需要登录后才能抓"）
- 已在代码中自动检测的模式（如异常告警已覆盖的）
- 特定站点的临时数据（如具体的 cookie 值）
