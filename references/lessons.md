# API Scout — 经验积累

> 每次成功完成完整流程（抓包 → 分析 → 开发脚本 → 验证通过）后，记录关键经验。
> 下次分析新站点时先读此文件，避免重复踩坑。

---

## e签宝 (2026-03-24)

**现象：** 脚本用真实 flowId/accountId 拼接 URL 路径，返回 403 或 1400319 "页面超过时效性"。

**根因：** e签宝的 openresty 网关使用 `sys_*` 服务端变量别名机制。URL 路径中的 `sys_flowId`、`sys_accountId`、`sys_transId` 不是参数占位符，而是字面量——网关根据当前会话上下文自动解析为真实 ID。用真实 ID 替换会被网关拒绝。

**解法：** 在 URL 路径和 query param 中原样使用 `sys_flowId`/`sys_accountId`/`sys_transId`，不替换为真实值。

**推广规律：** 当抓包数据中出现 `sys_*`、`$*`、`__*__` 等前缀的路径段时，优先假设它们是服务端变量别名，需要原样使用。先用字面量尝试，不行再换真实 ID。

---

## e签宝 — SERVERID (2026-03-24)

**现象：** 脚本调用 setCacheData 偶尔成功偶尔失败，返回的数据不一致。

**根因：** e签宝后端由 openresty 做负载均衡，`SERVERID` cookie 确保同一用户的请求路由到同一后端服务器。`openwebserver/login` 接口的 Set-Cookie 会设置此值。不携带 SERVERID 会导致请求被路由到不同服务器，session 不一致。

**解法：** 在 `openwebserver/login` 请求后捕获 Set-Cookie 中的 `SERVERID`，合并到后续所有请求的 cookie 中。

**推广规律：** 当某个端点的响应通过 Set-Cookie 设置了新的 cookie，尤其是名称含 `SERVER`、`ROUTE`、`STICKY`、`BACKEND` 等关键词的，很可能是负载均衡粘性会话标识，后续请求必须携带。
