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
├── captures/                ← 原始数据（含敏感信息，gitignored）
├── credentials/             ← 提取的 cookie/token（gitignored）
└── reports/                 ← 脱敏分析报告（可安全分享/提交）
```

## 安装

```bash
cd api-scout
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 使用

### 方式一：使用预置 Profile（推荐）

```bash
# 豆包 — 自动过滤埋点噪音，分类 chat/im/skill 等 API
python tools/api_capture.py --profile doubao

# 即梦 — 分类 generate/poll/upload/download API
python tools/api_capture.py --profile jimeng

# 小云雀
python tools/api_capture.py --profile xyq
```

Profile 预置了目标 URL、域名过滤、噪音路径过滤、API 分类和已知认证模式。

### 方式二：自定义参数

```bash
# 指定 URL，不用 profile
python tools/api_capture.py --url "https://www.example.com"

# 指定 URL + 域名过滤
python tools/api_capture.py --url "https://www.example.com" --filter "example.com"

# Profile + URL 覆盖
python tools/api_capture.py --profile doubao --url "https://www.doubao.com/chat/special"

# 空白页，自己导航
python tools/api_capture.py
```

### CLI 参数

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--profile` | `-p` | Profile 名称，加载 `profiles/<name>.yaml` |
| `--url` | `-u` | 起始 URL，覆盖 profile 中的 url |
| `--filter` | `-f` | 域名过滤，覆盖 profile 中的 filter_domains |

### 操作流程

1. 运行命令，浏览器弹出
2. 登录目标网站（如需要）
3. 执行你想抓取的完整操作流程
4. **关闭浏览器**结束抓包
5. 查看输出文件

## 输出文件

每次抓包输出到三个目录：

| 目录 | 文件 | 内容 | 安全性 |
|------|------|------|--------|
| `captures/` | `{domain}_{ts}.json` | 原始请求/响应（含真实 cookie/token） | **gitignored，勿分享** |
| `credentials/` | `{domain}.json` | 提取的 cookie 和 token，跨抓包自动合并 | **gitignored，勿分享** |
| `reports/` | `{domain}_{ts}.md` + `.json` | 脱敏分析报告（敏感值已遮蔽） | 可安全提交/分享 |

### Markdown 报告结构

- **Section 0 — API Categories**：按 profile 定义的分类归组端点
- **Section 1 — Authentication Analysis**：检测到的 Cookie、签名 Header、认证参数
- **Section 2 — Request Timeline**：按时间排列的请求列表，含状态码和分类标签
- **Section 3 — Endpoint Details**：每个端点的 headers、参数、请求体、响应体示例

## Profile 配置

Profile 是一个 YAML 文件，定义如何抓包和分析特定站点：

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
    - /api/token
  core:
    - /api/chat/*

auth_hints:               # 已知的认证字段
  query_params: [msToken, a_bogus]
  cookies: [sessionid, sid_tt]
  headers: [Sign, Device-Time]
```

### 新建 Profile

1. 先不用 profile 跑一次，观察有哪些噪音和 API
2. 基于观察结果创建 `profiles/<name>.yaml`
3. 用新 profile 重新跑，验证过滤和分类效果

## 作为 Skill 使用

将 `skill.md` 复制到 agent 的 skill/command 目录（如 `~/.claude/commands/api-scout.md`），即可通过 `/api-scout` 触发。

Skill 会引导 agent 完成：抓包 → 读取结果 → 分析认证/反爬/端点 → 输出实现方案 → 辅助代码开发。

详见 [skill.md](skill.md) 中的完整工作流定义。
