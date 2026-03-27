# API Scout — 经验积累

> 每次成功完成完整流程（抓包 → 分析 → 开发脚本 → 验证通过）后，记录关键经验。
> 下次分析新站点时先读此文件，避免重复踩坑。

---

## 某*签 (2026-03-24)

**现象：** 脚本用真实 flowId/accountId 拼接 URL 路径，返回 403 或 1400319 "页面超过时效性"。

**根因：** 某*签的 openresty 网关使用 `sys_*` 服务端变量别名机制。URL 路径中的 `sys_flowId`、`sys_accountId`、`sys_transId` 不是参数占位符，而是字面量——网关根据当前会话上下文自动解析为真实 ID。用真实 ID 替换会被网关拒绝。

**解法：** 在 URL 路径和 query param 中原样使用 `sys_flowId`/`sys_accountId`/`sys_transId`，不替换为真实值。

**推广规律：** 当抓包数据中出现 `sys_*`、`$*`、`__*__` 等前缀的路径段时，优先假设它们是服务端变量别名，需要原样使用。先用字面量尝试，不行再换真实 ID。

---

## 某*签 — SERVERID (2026-03-24)

**现象：** 脚本调用 setCacheData 偶尔成功偶尔失败，返回的数据不一致。

**根因：** 某*签后端由 openresty 做负载均衡，`SERVERID` cookie 确保同一用户的请求路由到同一后端服务器。`openwebserver/login` 接口的 Set-Cookie 会设置此值。不携带 SERVERID 会导致请求被路由到不同服务器，session 不一致。

**解法：** 在 `openwebserver/login` 请求后捕获 Set-Cookie 中的 `SERVERID`，合并到后续所有请求的 cookie 中。

**推广规律：** 当某个端点的响应通过 Set-Cookie 设置了新的 cookie，尤其是名称含 `SERVER`、`ROUTE`、`STICKY`、`BACKEND` 等关键词的，很可能是负载均衡粘性会话标识，后续请求必须携带。

---

## 某*包 — IM 接口 header 与 chat 接口不同 (2026-03-26)

**现象：** 调用 `/im/conversation/batch_del_user_conv` 删除会话，返回 HTTP 200 但 `status_code: 712012002, "不支持编码类型"`，会话实际未被删除。

**根因：** 同一站点的 IM 系列接口和 chat 接口使用不同的 header 约定：
- chat 接口：`Content-Type: application/json`，`agw-js-conv: str, str`
- IM 接口：`Content-Type: application/json; encoding=utf-8`，`agw-js-conv: str`

服务端根据 `encoding=utf-8` 和 `agw-js-conv` 值选择解码方式，用错了不会报 400，而是返回 200 + 业务错误码。

**解法：** IM 类请求（路径含 `/im/`）单独设置 header，不复用 chat 的 header。

**推广规律：** 同一站点不同子系统（chat vs IM vs upload）的 header 约定可能不同。当请求返回 200 但业务码异常（尤其是"编码"/"格式"相关错误），优先排查 Content-Type 和自定义 header 的差异。对比抓包数据中成功请求的 header 是最快的定位方式。

---

## 某*包 — 删除会话的正确接口 (2026-03-26)

**现象：** 开源项目推荐用 `/samantha/thread/delete` 删除 API 创建的会话，调用返回 HTTP 200 空响应，但会话仍然存在于用户列表中。

**根因：** `/samantha/thread/delete` 是旧接口或仅标记删除，实际不从用户会话列表中移除。真正的删除走 IM 系统的 `/im/conversation/batch_del_user_conv`，需要 `cmd: 4171` 的 IM 消息格式。

**解法：** 用 `/im/conversation/batch_del_user_conv` + `cmd=4171` + `conversation_type=3`（AI 对话）删除。`conversation_id` 从 SSE 响应的 `SSE_ACK` 事件中 `ack_client_meta.conversation_id` 获取。

**推广规律：** 开源项目的实现不一定完全正确，尤其是清理/删除逻辑。当开源方案的某个操作"看起来成功但没效果"时，回到抓包数据找官方前端的实际调用方式。

---

## 某*包 — 图片理解不需要上传 (2026-03-26)

**现象：** 抓包发现图片理解需要复杂的上传流程（AWS4 签名 → CDN 上传 → 预处理 → 带 block_type=10052 的聊天），准备实现时发现直接把图片 URL 放在文本消息里发送，AI 也能识别图片内容。

**根因：** 该站点的 AI 模型本身具备通过 URL 访问图片的能力。前端上传流程是为了用户体验（拖拽上传本地图片），但对于已有公开 URL 的图片，模型可以直接通过 URL 获取并分析。

**解法：** 直接在 `text_block.text` 中包含图片 URL + 提问即可，无需实现上传流程。

**推广规律：** 对于 AI 对话类站点，在实现复杂的文件上传流程之前，先测试模型是否能直接通过 URL 理解内容。很多多模态模型支持直接访问公开 URL，可以绕过上传。

---

## 某*音 — AI 助手 SSE 文本在深层嵌套结构中 (2026-03-26)

**现象：** AI 助手返回 SSE 流式响应，但 JSON 结构非常复杂（多层嵌套），用常规方法（查找 `text`/`content`/`markdown` 字段）提取不到文字。

**根因：** 文字内容在 `generation_spans` 数组里，`type=2` 的 span 的 `text.content` 字段中，可能嵌套在 `data[].display.generation_spans` 或更深的位置。而且整个 SSE 响应使用卡片系统（`cmd: NewCard` / `Append`），文字是逐 token 追加的。

**解法：** 用递归函数查找所有 `generation_spans` 键，筛选 `type==2` 的 span，拼接 `span.text.content` 得到完整文本。

**推广规律：** 字节系产品的 SSE 响应倾向于用复杂的卡片/组件系统包裹内容，而不是简单的 `{text: "..."}` 格式。拿到 SSE 响应后，先 dump 完整 JSON 结构找到文本所在位置，再写针对性的提取逻辑。递归查找比写固定路径更稳健。

---

## 某*音 — 深度思考模式影响完整度 (2026-03-26)

**现象：** 请求 AI 助手"给我视频完整文字版"，返回的内容经常不完整或被截断。

**根因：** 默认模式下 `enable_ai_search_deep_think=0`，AI 倾向于给摘要而不是完整内容。开启深度思考（`=1`）后，AI 更倾向于给出完整的长文本。但在首轮对话直接开启深度思考效果不稳定，在多轮追问后开启效果更好。

**解法：** 采用两步策略：先用普通模式获取视频总结，再用深度思考模式要求完整文字版。

**推广规律：** AI 对话类接口的"质量参数"（deep_think、detail_level 等）在多轮上下文中效果更好。如果一步到位效果不好，拆成多步渐进式请求。
