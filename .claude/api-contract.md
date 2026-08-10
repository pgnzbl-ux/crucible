# Crucible API 契约（v1）

> 本文件是**对外契约**——路由 / 请求 / 响应 / 错误码。完整 OpenAPI 见 FastAPI `/openapi.json`。

## 通用约定

- 前缀：`/api/v1`
- 鉴权：除 `/health`、`/metrics`、`/api/v1/auth/login`、`/api/v1/auth/register` 外全部需 Bearer JWT
- 时间：ISO-8601 UTC
- 错误响应统一格式见 `.claude/rules/error-handling.md` §3

---

## Identity Context

### POST `/api/v1/auth/register`

注册新用户。

**请求**

```json
{ "username": "alice", "password": "Passw0rd!" }
```

**响应 201**

```json
{ "id": "uuid", "username": "alice", "created_at": "2026-08-10T..." }
```

### POST `/api/v1/auth/login`

**请求**

```json
{ "username": "alice", "password": "Passw0rd!" }
```

**响应 200**

```json
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 3600 }
```

### GET `/api/v1/users/me`

当前用户信息。

---

## Task Context

### POST `/api/v1/tasks`

创建任务。

**请求**

```json
{
  "repo_url": "https://github.com/owner/repo",
  "description": "漏洞描述（10-8000 字符）",
  "priority": "P0" | "P1" | "P2"
}
```

**响应 201**：`Task` 对象

### GET `/api/v1/tasks`

分页查询。`?page=1&page_size=20&status=running`

### GET `/api/v1/tasks/{id}`

### POST `/api/v1/tasks/{id}/actions/cancel`

取消任务。**待补（P0-2）**：`celery_app.control.revoke` + 沙箱销毁。

### GET `/api/v1/tasks/{id}/events`

历史事件分页（占位方案；P0-1 前轮询用）。

### GET `/api/v1/tasks/{id}/events/stream`

**SSE**（P0-1 待补）。`text/event-stream`，事件名 = `Event.event_type`。

---

## Agent Context

无对外 REST 端点。Agent 通过 Celery 工作流（`agent/tasks.py::run_task_analysis`）异步执行。

---

## Report Context

### GET `/api/v1/reports`

分页查询。

### GET `/api/v1/reports/{id}`

含正文、状态、MinIO 签名 URL。

### POST `/api/v1/reports/{id}/actions/export?format=pdf|docx`

**待补（P1-10）**。

### GET `/api/v1/reports/{id}/evidences`

证据列表（日志 / 截图 MinIO 路径）。

**待补（P0-4）** 上传链路。

---

## Settings Context（LLM Provider）

### GET `/api/v1/settings/providers`

**掩码** API Key（仅 `***last4`）。

### POST `/api/v1/settings/providers`

新建。`api_key` 落库前 Fernet 加密。

### PATCH `/api/v1/settings/providers/{id}`

### POST `/api/v1/settings/providers/{id}/actions/activate`

唯一 active：事务内把别人置 false。

### POST `/api/v1/settings/providers/{id}/actions/test`

真实打 `base_url` 验证（不 Mock）。

---

## 健康检查 / 监控

- `GET /health` → `{ "status": "ok" }`
- `GET /metrics` → Prometheus（无需鉴权）

---

## 错误码汇总

| HTTP | code | 场景 |
|---|---|---|
| 400 | `BAD_REQUEST` | JSON 解析失败等 |
| 401 | `UNAUTHENTICATED` | 缺 / 错 token |
| 403 | `FORBIDDEN` | 权限不足（待 P1-9 RBAC） |
| 404 | `NOT_FOUND` / `TASK_NOT_FOUND` / `REPORT_NOT_FOUND` | 资源不存在 |
| 409 | `CONFLICT` / `STATE_INVALID` | 状态机非法转换 / username 重名 |
| 422 | `VALIDATION_FAILED` | Pydantic 字段错误 |
| 429 | `RATE_LIMITED` | （待补） |
| 503 | `SERVICE_UNAVAILABLE` | Docker / Redis / MinIO 不可用 |

---

## 待补（P0/P1/P2 路线）

- P0-1: `GET /tasks/{id}/events/stream` SSE 端点
- P0-2: `POST /tasks/{id}/actions/cancel` 真正生效
- P0-3: `users/me` JWT 解析 + 路由守卫
- P0-4: `POST /reports/{id}/evidences` 上传
- P1-7: `Authorization: ApiKey xxx` 鉴权依赖
- P1-8: OIDC 回调 `/auth/oauth/{provider}/callback`
- P1-9: RBAC 权限矩阵
- P1-10: 报告导出 `?format=pdf|docx`
- P1-11: 审计日志端点 `/audit/logs`
- P2-12: OpenAPI 自动生成前端 types