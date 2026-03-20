# API Scout

通用的 Web API 逆向抓包工具。启动浏览器，手动操作任意网站，自动记录所有 API 请求，输出结构化分析报告。

可作为独立脚本使用，也可作为 Skill 接入 Claude 等 AI agent，让大模型直接分析 API 并辅助开发。

## 项目结构

```
api-scout/
├── skill.md                 ← 通用 Skill 定义（可迁移给任何 agent）
├── requirements.txt
├── .gitignore
├── profiles/                ← 站点配置
│   ├── _default.yaml        ← 通用兜底
│   ├── doubao.yaml          ← 豆包 AI 对话
│   ├── jimeng.yaml          ← 即梦 AI 视频生成
│   └── xyq.yaml             ← 小云雀 AI 视频
├── tools/
│   └── api_capture.py       ← 核心抓包脚本
├── examples/                ← API 调用示例脚本
│   └── doubao_chat_test.py  ← 豆包聊天 API (纯 HTTP，无需浏览器)
├── tests/
│   └── test_core.py         ← 核心逻辑测试 (53 个)
├── captures/                ← 原始数据（含敏感信息，gitignored）
├── credentials/             ← 提取的 cookie/token（gitignored）
└── reports/                 ← 脱敏分析报告 + API Spec
    ├── www_doubao_com_api_spec.md
    ├── jimeng_jianying_com_api_spec.md
    └── xyq_jianying_com_api_spec.md
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
# 使用预置 Profile（推荐）
python tools/api_capture.py --profile doubao
python tools/api_capture.py --profile jimeng
python tools/api_capture.py --profile xyq

# 自定义 URL
python tools/api_capture.py --url "https://www.example.com"

# URL + 域名过滤
python tools/api_capture.py --url "https://www.example.com" --filter "example.com"

# Profile + URL 覆盖
python tools/api_capture.py --profile doubao --url "https://www.doubao.com/chat/special"
```

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--profile` | `-p` | Profile 名称，加载 `profiles/<name>.yaml` |
| `--url` | `-u` | 起始 URL，覆盖 profile 中的 url |
| `--filter` | `-f` | 域名过滤，覆盖 profile 中的 filter_domains |

操作：浏览器弹出 → 登录 → 执行操作 → **关闭浏览器**结束抓包。

### API 调用示例

```bash
# 豆包聊天（纯 HTTP，无需浏览器）
python examples/doubao_chat_test.py "你好"

# 指定 cookie 文件（支持 4 种格式）
python examples/doubao_chat_test.py --cookie /path/to/cookies.json "你好"
python examples/doubao_chat_test.py --cookie /path/to/cookies.txt "你好"

# 管道输入
echo "翻译成英文：你好" | python examples/doubao_chat_test.py
```

支持的 cookie 格式：
- **JSON 数组** — 浏览器插件导出: `[{"name":"sessionid","value":"xxx"}, ...]`
- **Netscape/curl txt** — `domain  TRUE  /  FALSE  0  name  value`
- **Key=Value 字符串** — DevTools 手动复制: `sessionid=xxx; sid_tt=yyy`
- **api-scout credentials** — 本工具抓包生成的 `credentials/*.json`

## 输出文件

每次抓包输出到三个目录：

| 目录 | 文件 | 内容 | 安全性 |
|------|------|------|--------|
| `captures/` | `{domain}_{ts}.json` | 原始请求/响应（含真实 cookie/token） | **gitignored，勿分享** |
| `credentials/` | `{domain}.json` | 提取的 cookie 和 token，跨抓包自动合并 | **gitignored，勿分享** |
| `reports/` | `{domain}_{ts}.md` + `.json` | 脱敏分析报告（敏感值已遮蔽） | 可安全提交/分享 |

### 报告结构

- **Section 0 — API Categories**：按 profile 定义的分类归组端点
- **Section 1 — Authentication Analysis**：Cookie、签名 Header、认证参数
- **Section 2 — Request Timeline**：按时间排列的请求列表，含状态码和分类
- **Section 3 — Endpoint Details**：每个端点的 headers、参数、请求体、响应体
- **Section 4 — WebSocket Connections**：WS 连接和消息记录（如有）

### API Spec

多次抓包后可整理为完整的 API 文档，保存在 `reports/` 下：

| 文件 | 站点 | 来源 |
|------|------|------|
| `www_doubao_com_api_spec.md` | 豆包 | 6 次抓包 + doubao-free-api 项目参考 |
| `jimeng_jianying_com_api_spec.md` | 即梦 | 代码逆向 + 3 个开源项目对比 |
| `xyq_jianying_com_api_spec.md` | 小云雀 | 代码逆向 |

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

将 `skill.md` 复制到 agent 的 skill/command 目录：

```bash
cp skill.md ~/.claude/commands/api-scout.md
```

Skill 工作流（7 步）：环境准备 → 抓包 → 读取输出 → 分析 → 报告 → 整理 API Spec → 辅助开发。

详见 [skill.md](skill.md)。
