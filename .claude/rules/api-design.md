---
paths: ["backend/app/**/*.py"]
---

# Crucible API 设计规范

> 完整端点契约见 `.claude/api-contract.md`（路由 / 请求 / 响应 / 错误码）。本文件是**风格与原则**。

## 1. 路由风格

- 前缀：`/api/v1`（v1 当前唯一版本）
- 资源用复数名词：`/tasks`、`/reports`、`/users`、`/settings/providers`
- 动作尽量用 HTTP 动词（GET/POST/PUT/PATCH/DELETE），避免 `/tasks/{id}/cancel` 这种 RPC 风
  - 例外：取消 / 重跑 / 触发确实是动作语义时允许 POST `/tasks/{id}/actions/cancel`
- 嵌套最多一层：`/tasks/{id}/runs` OK，`/tasks/{id}/runs/{run_id}/events` OK，更深请拆资源

## 2. 版本化

- 路径版本（不是 header 版本），便于缓存与日志
- 破坏性变更**必须**进 `/api/v2`，不兼容老路径
- 非破坏性（新增字段、新增端点）直接发 v1

## 3. 请求 / 响应

- 入参用 Pydantic schema（参考 `validation.md`）
- 响应 JSON：单一资源直接返对象；列表返 `{items: [...], total: N, page: 1, page_size: 20}`
- 时间统一 ISO-8601 UTC；状态码 UPPER_SNAKE_CASE

## 4. 错误码

参见 `.claude/api-contract.md` 与 `error-handling.md`。最小集：

| HTTP | 业务 code |
|---|---|
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHENTICATED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 409 | `CONFLICT` / `STATE_INVALID` |
| 422 | `VALIDATION_FAILED` |
| 429 | `RATE_LIMITED` |
| 503 | `SERVICE_UNAVAILABLE` |

## 5. SSE 端点（P0-1 已实现）

- 路径：`GET /api/v1/tasks/{id}/events/stream`
- Content-Type：`text/event-stream`
- 响应头：`X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`（防反代缓冲）
- 事件名 = `Event.event_type`（`phase.updated` / `agent.message` / `tool.call.*` / `agent.completed` / `agent.failed` / `ready` / `error`）
- 心跳：每 15s 一条 `: heartbeat\n\n`，防代理超时
- 启动先回放 DB 历史（帧含 `replayed: true`），再订阅 Redis `task.{id}.events`
- 客户端断开立即关闭 Redis 订阅（finally 块 unsubscribe + close），避免泄漏

## 6. 鉴权

- 除 `/health`、`/metrics`、`/api/v1/auth/login`、`/api/v1/auth/register` 外全部需鉴权
- 鉴权依赖统一在 `shared/deps.py::get_current_user`
- API Key（待 P1-7）走独立依赖 `get_api_key_principal`

## 7. OpenAPI 契约（P2-12 待补）

- FastAPI 自动生成 `/openapi.json`
- 前端通过 `openapi-typescript` 生成 `shared/types/api.ts`，**禁止**手抄类型
- 契约变更必须同步更新本文件 + `.claude/api-contract.md`