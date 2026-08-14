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

取消任务(已实现):先把任务/run/未完成节点标 `cancelled` 并 **提交后立刻返回**。`revoke(terminate=True)` 停 worker（Windows solo 经常杀不掉）；后台 `schedule_teardown_task_runtime` 只按 `crucible.task_id` 强拆该任务的 agent-runner（停 AI），不 `compose down` 可复用靶场。编排器每节点刷新库状态，已取消则停后续节点，且 execute 失败不得把 cancelled 改成 failed。靶场在静默满 TTL 1 小时且无 live 任务后由巡检销毁。

### POST `/api/v1/tasks/{id}/retry`  *(阶段 1 新增)*

重试任务(202 返 `{task_id, run_id, status:retrying}`)。**断点续跑**:复用上一 run 已完成的 NodeRun.output_json,从第一个非 completed 节点起重跑。

### DELETE `/api/v1/tasks/{id}?hard=true|false`  *(阶段 1 新增)*

删除任务。默认软删(`status=archived`);`hard=true` 物理删除需 `X-Confirm: true` header(级联清理 runs/nodes/events)。删除时 `teardown_task_runtime` 只拆该任务的 agent-runner，不拆可复用靶场。

### GET `/api/v1/tasks/{id}/runs/{run_id}/nodes`  *(阶段 1 新增)*

返回该 run 的 6 节点 NodeRun 列表(前端步骤条数据源):`[{node_index, node_key, status, attempt, error_message, started_at, finished_at}]`。

### GET `/api/v1/tasks/{id}/events`

历史事件（默认最近 1000 条，`limit` 1–1000）。含 `agent.thinking` / `agent.message` / `tool.call.*` / `agent.failed`（`title`+`hint`）/ `node.updated`。

### GET `/api/v1/tasks/{id}/events/stream`

**SSE** 实时事件流（P0-1 已实现）。`Content-Type: text/event-stream`。

- 响应头：`X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`（防 nginx/反代缓冲）
- 事件名 = `Event.event_type`（`phase.updated` / `agent.thinking` / `agent.message` / `tool.call.*` / `agent.completed` / `agent.failed` / `node.updated` / `ready` / `error`）
- 启动先回放 DB 最近 1000 条（帧含 `replayed: true`），再订阅 Redis `task.{id}.events` 频道转发实时事件
- `agent.failed` 的 `event` 含 `error`（原文）、`title`（人类可读）、`hint`（下一步）
- 15s 心跳：`: heartbeat\n\n`
- 客户端断开 → 立即 `unsubscribe` + 关闭 Redis 连接（防泄漏）
- 鉴权：`?token=<jwt>` query 注入（EventSource 不支持自定义 header）

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

## Lab Context

所有端点只访问当前用户的 lab。列表与详情中的 lab 对象包含 `live_task_count`；大于 0 时，所有靶场级和容器级写操作均拒绝。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/v1/labs` | 按 `project_id` 分组返回项目及其 labs；lab 含 status、commit_sha、target_url、ttl_remaining_seconds、live_task_count、容器摘要 |
| GET | `/api/v1/labs/{id}` | 返回 lab 详情及所属 compose 项目的容器列表（name、status、ports、image），并刷新 TTL |
| POST | `/api/v1/labs/{id}/actions/stop` | `compose stop`，状态变为 `stopped` |
| POST | `/api/v1/labs/{id}/actions/start` | `compose start`，状态变为 `ready` 并刷新 TTL |
| POST | `/api/v1/labs/{id}/actions/rebuild` | 状态变为 `creating`，执行 `compose up -d --build`，成功为 `ready`、失败为 `failed` |
| DELETE | `/api/v1/labs/{id}` | `compose down -v`，状态变为 `destroyed` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/stop` | `docker stop` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/start` | `docker start` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/restart` | `docker restart` |
| DELETE | `/api/v1/labs/{id}/containers/{name}` | `docker rm -f` |

`{name}` 不属于该 lab 的 compose 项目时返回 404。存在 live 任务时，上述所有写操作返回 **409**，错误码 `LAB_IN_USE`：

```json
{
  "detail": {
    "code": "LAB_IN_USE",
    "message": "靶场正被任务使用",
    "task_ids": ["uuid"]
  }
}
```

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
| 409 | `CONFLICT` / `STATE_INVALID` / `LAB_IN_USE` | 状态机非法转换 / username 重名 / 靶场正被 live 任务占用 |
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