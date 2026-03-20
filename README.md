# API Scout

通用的 Web API 逆向抓包工具。启动浏览器，手动操作任意网站，自动记录所有 API 请求，输出结构化分析报告。

可作为独立脚本使用，也可作为 Skill 接入 Claude 等 AI agent，让大模型直接分析 API 并辅助开发。

## 项目结构

```
api-scout/
├── skill.md                 ← 通用 Skill 定义（可迁移给任何 agent）
├── requirements.txt         ← 依赖（仅 playwright）
├── .gitignore
├── tools/
│   └── api_capture.py       ← 核心抓包脚本
└── captures/                ← 输出目录（自动创建，已 gitignore）
```

## 安装

```bash
cd api-scout
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 使用

### 方式一：独立脚本

```bash
# 指定起始 URL
python tools/api_capture.py --url "https://www.doubao.com"

# 只抓特定域名的请求
python tools/api_capture.py --url "https://www.doubao.com" --filter "doubao.com"

# 不指定 URL，打开空白页自己导航
python tools/api_capture.py
```

浏览器弹出后：

1. 登录目标网站（如需要）
2. 执行你想抓取的完整操作流程（如：提交任务 → 等待结果 → 下载）
3. **关闭浏览器**结束抓包

脚本会在 `captures/` 下生成两个文件：

- `{domain}_{timestamp}.json` — 完整结构化数据（所有请求/响应详情）
- `{domain}_{timestamp}.md` — 可读的分析报告（认证分析、端点列表、请求示例）

### 方式二：通过 Skill 接入 AI Agent

将 `skill.md` 配置到 Claude Code 或其他兼容 agent 中，使用 `/api-scout <url>` 触发。

Skill 工作流：

1. **抓包** — 运行 `api_capture.py`，用户手动操作浏览器
2. **分析** — AI 读取输出文件，自动识别认证机制、API 端点、签名算法、反爬特征
3. **输出** — 生成结构化的 API 分析报告 + 实现建议
4. **开发**（可选）— 根据分析结果辅助生成 API 客户端代码

## 抓包脚本功能

- 自动过滤静态资源（图片/字体/CSS/JS），只保留 API 请求
- JSON 请求体/响应体自动解析
- 大 body（>50KB）自动截断，二进制内容标记跳过
- 相似端点自动归组（路径中的 ID/UUID/Hash 替换为占位符）
- 认证模式自动检测（Cookie、Authorization、自定义签名 Header、Query 参数）
- 实时输出捕获的请求列表

## 输出报告内容

Markdown 报告包含：

1. **认证分析** — Session Cookie、Auth Header、签名 Header、认证相关 Query 参数
2. **请求时间线** — 按时间顺序的完整 API 调用列表
3. **端点详情** — 每个端点的请求头、请求体、响应体示例
