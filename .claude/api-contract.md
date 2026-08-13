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

分页查询。Query：
- `status`：单状态，或逗号分隔多状态（如 `pending,queued`）
- `priority` / `q`（项目地址关键词）/ `date_from` / `date_to`（`YYYY-MM-DD`）
- `limit`（默认 50，最大 200）/ `offset`

响应：`{ items, total, limit, offset }`

### GET `/api/v1/tasks/{id}`

### POST `/api/v1/tasks/{id}/actions/cancel`

取消任务(已实现):`celery_app.control.revoke(terminate=True)` + agent-runner 容器销毁(SIGTERM 钩子)。

### POST `/api/v1/tasks/{id}/retry`  *(阶段 1 新增)*

重试任务(202 返 `{task_id, run_id, status:retrying}`)。**断点续跑**:复用上一 run 已完成的 NodeRun.output_json,从第一个非 completed 节点起重跑。

### DELETE `/api/v1/tasks/{id}?hard=true|false`  *(阶段 1 新增)*

删除任务。默认软删(`status=archived`);`hard=true` 物理删除需 `X-Confirm: true` header(级联清理 runs/nodes/events)。

### GET `/api/v1/tasks/{id}/runs/{run_id}/nodes`  *(阶段 1 新增)*

返回该 run 的 6 节点 NodeRun 列表(前端步骤条数据源):`[{node_index, node_key, status, attempt, error_message, started_at, finished_at}]`。

### GET `/api/v1/tasks/{id}/events`

历史事件分页（降级方案；SSE 不可用或补齐历史时用）。

### GET `/api/v1/tasks/{id}/events/stream`

**SSE** 实时事件流（P0-1 已实现）。`Content-Type: text/event-stream`。

- 响应头：`X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`（防 nginx/反代缓冲）
- 事件名 = `Event.event_type`（`phase.updated` / `agent.message` / `tool.call.*` / `agent.completed` / `agent.failed` / `ready` / `error`）
- 启动先回放 DB 历史（帧含 `replayed: true`），再订阅 Redis `task.{id}.events` 频道转发实时事件
- 15s 心跳：`: heartbeat\n\n`
- 客户端断开 → 立即 `unsubscribe` + 关闭 Redis 连接（防泄漏）
- 鉴权（待 P0-3）：`?token=<jwt>` query 注入（EventSource 不支持自定义 header）

---

## Agent Context

无对外 REST 端点。Agent 通过 Celery 工作流（`agent/tasks.py::run_task_analysis`）异步执行。

---

## Report Context

### GET `/api/v1/reports`

分页查询。

### GET `/api/v1/reports/{id}`

含正文、状态、结构化字段(verdict/cvss_score/severity/vulnerable_file/report_data/md_artifact_key/docx_artifact_key)。

### GET `/api/v1/reports/{id}/export?format=json|md`

**已实现**。format=json 返回结构化 report_data;format=md 返回 renderer.py 渲染的 markdown。**不生成 docx/pdf**。

### GET `/api/v1/reports/{id}/evidences`

证据列表（日志 / 截图 / PoC），每条含 MinIO 预签名 `download_url`。

### POST `/api/v1/reports/{id}/evidences`

上传证据（multipart/form-data）。字段：`file`（文件，≤50MB）+ `kind`（`artifact|log|screenshot|poc`）。
上传 → MinIO `crucible-evidence` bucket → 落 `evidences` 表 → 返回带预签名 URL 的 `EvidenceResponse`。
owner 校验：生产严格（`report.owner_id` 必须匹配 token），开发宽松。

---

## Settings Context（LLM Provider）

### GET `/api/v1/settings/providers`

**掩码** API Key（仅 `***last4`）。

### POST `/api/v1/settings/providers`

新建。`api_key` **明文落库**(存 `api_key_encrypted` 字段),响应层 `mask_secret` 掩码。

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

- ~~P0-0: Agent 编排~~ ✅ 平台 6 节点编排(见 docs/agent-workflow.md)
- ~~P0-1: `GET /tasks/{id}/events/stream` SSE 端点~~ ✅
- ~~P0-2: `POST /tasks/{id}/actions/cancel` 真正生效~~ ✅（revoke + 容器销毁）
- ~~P0-3: JWT 闭环 + SSE `?token=` 鉴权~~ ✅
- ~~P0-4: `POST /reports/{id}/evidences` 上传~~ ✅
- P1-6: Credential Proxy（补齐插件 `credential_store` 能力）
- P1-7: `Authorization: ApiKey xxx` 鉴权依赖
- P1-8: OIDC 回调 `/auth/oauth/{provider}/callback`
- P1-9: RBAC 权限矩阵
- 报告导出已实现(GET /reports/{id}/export?format=json|md),docx 不再支持
- P1-11: 审计日志端点 `/audit/logs`
- P2-12: OpenAPI 自动生成前端 types