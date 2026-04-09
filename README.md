# API Scout

通用的 Web API 逆向抓包工具。启动浏览器，手动操作任意网站，自动记录所有 API 请求，输出结构化分析报告。

可作为独立脚本使用，也可作为 Skill 接入 Claude 等 AI agent，让大模型直接分析 API 并辅助开发。

## 项目结构

```
api-scout/
├── SKILL.md                 ← 通用 Skill 定义（可迁移给任何 agent）
├── requirements.txt
├── .gitignore
├── profiles/                ← 站点配置
│   ├── _default.yaml        ← 通用兜底
│   └── <site>.yaml          ← 自定义站点（gitignored）
├── scripts/
│   └── api_capture.py       ← 核心抓包脚本
├── examples/                ← API 调用示例脚本（gitignored）
├── tests/
│   └── test_core.py         ← 核心逻辑测试 (53 个)
├── captures/                ← 原始数据（含敏感信息，gitignored）
├── credentials/             ← 提取的 cookie/token（gitignored）
└── reports/                 ← 脱敏分析报告 + API Spec（gitignored）
```

## 安装

```bash
cd api-scout
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 使用

### 抓包

```bash
# 使用预置 Profile
python scripts/api_capture.py --profile <site>

# 自定义 URL
python scripts/api_capture.py --url "https://www.example.com"

# URL + 域名过滤
python scripts/api_capture.py --url "https://www.example.com" --filter "example.com"

# Profile + URL 覆盖
python scripts/api_capture.py --profile <site> --url "https://example.com/special"

# CDP 模式（反检测严格的站点，使用用户真实 Chrome）
python scripts/api_capture.py --cdp --url "https://www.example.com"
```

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--profile` | `-p` | Profile 名称，加载 `profiles/<name>.yaml` |
| `--url` | `-u` | 起始 URL，覆盖 profile 中的 url |
| `--filter` | `-f` | 域名过滤，覆盖 profile 中的 filter_domains |
| `--cdp` | | CDP 模式，连接用户真实 Chrome（可选指定端口，默认 9222） |

操作：浏览器弹出 → 登录 → 执行操作 → **关闭浏览器**（Playwright）或**关闭标签页**（CDP）结束抓包。

**CDP 模式**：当站点有严格的反自动化检测（登录跳转异常、验证码拦截）时使用。自动启动 Chrome 调试模式，首次会复制用户的 Chrome profile 保留登录状态。

## 输出文件

每次抓包输出到三个目录：

| 目录 | 文件 | 内容 | 安全性 |
|------|------|------|--------|
| `captures/` | `{domain}_{ts}.json` | 原始请求/响应（含真实 cookie/token） | **gitignored，勿分享** |
| `credentials/` | `{domain}.json` | 提取的 cookie 和 token，跨抓包自动合并 | **gitignored，勿分享** |
| `reports/` | `{domain}_{ts}.md` + `.json` | 脱敏分析报告（敏感值已遮蔽） | gitignored（可自行选择分享） |

### 报告结构

- **Section 0 — API Categories**：按 profile 定义的分类归组端点
- **Section 1 — Authentication Analysis**：Cookie、签名 Header、认证参数
- **Section 2 — Request Timeline**：按时间排列的请求列表，含状态码和分类
- **Section 3 — Endpoint Details**：每个端点的 headers、参数、请求体、响应体
- **Section 4 — WebSocket Connections**：WS 连接和消息记录（如有）

### API Spec

多次抓包后可整理为完整的 API 文档，保存在 `reports/{domain}_api_spec.md`。

## 抓包能力

- 自动过滤静态资源（图片/字体/CSS/JS），只保留 API 请求
- JSON 请求体/响应体自动解析
- **SSE 流式响应**解析为结构化摘要（事件统计 + 前 5 条样本）
- **WebSocket** 连接和消息捕获（方向、payload、时序）
- 大 body（>50KB）自动截断，二进制内容标记跳过
- 相似端点自动归组（路径中的 ID/UUID/Hash 替换为占位符）
- 认证模式自动检测（Cookie、Authorization、签名 Header、Query 参数）
- 浏览器关闭前自动提取 cookie 到 credentials
- 敏感值脱敏（cookie、token、a_bogus 在 reports 中遮蔽）

## Profile 配置

```yaml
name: 站点名称
description: 一句话描述
url: https://www.example.com

filter_domains:           # 只抓这些域名（空 = 全抓）
  - example.com

ignore_paths:             # 过滤噪音路径（支持尾部 * 通配）
  - /analytics/*
  - /tracking/*

ignore_domains: []        # 完全忽略的域名

api_categories:           # API 分类
  auth:
    - /api/login
  core:
    - /api/chat/*

auth_hints:               # 已知的认证字段
  query_params: [msToken, a_bogus]
  cookies: [sessionid, sid_tt]
  headers: [Sign, Device-Time]
```

新建 Profile：先不用 profile 跑一次 → 观察噪音和 API → 创建 yaml → 重新跑验证。

## 测试

```bash
python -m pytest tests/ -v
```

53 个测试覆盖：Profile 加载、路径匹配、请求过滤、Body/SSE 处理、路径归一化、认证检测、端点分组、凭据提取、脱敏、Markdown 生成。

## 作为 Skill 使用

将 `SKILL.md` 复制到 agent 的 skill/command 目录：

```bash
cp SKILL.md ~/.claude/commands/api-scout.md
```

Skill 工作流（7 步）：环境准备 → 抓包 → 读取输出 → 分析 → 报告 → 整理 API Spec → 辅助开发。

详见 [SKILL.md](SKILL.md)。
