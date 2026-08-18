# Crucible API 契约（v1）

> 本文件是**对外契约**——路由 / 请求 / 响应 / 错误码。完整 OpenAPI 见 FastAPI `/openapi.json`。

## 通用约定

- 前缀：`/api/v1`
- 鉴权：除 `/health`、`/metrics`、`/api/v1/auth/login`、`/api/v1/auth/register`、`/api/v1/auth/setup` 外全部需 Bearer JWT
- 时间：ISO-8601 UTC
- 错误响应统一格式见 `.claude/rules/error-handling.md` §3

---

## Identity Context

### GET `/api/v1/auth/setup`

是否还没有任何账号。`needs_setup=true` 时登录页展示创建账号。

**响应 200**

```json
{ "needs_setup": true }
```

### POST `/api/v1/auth/register`

仅在库中尚无用户时允许，创建第一个账号。请求体禁止传 `is_admin`。已有用户时返回 403。

**请求**

```json
{ "email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice" }
```

**响应 201**

```json
{ "id": "uuid", "email": "alice@example.com", "display_name": "Alice", "is_admin": true, "role": "admin", "is_active": true }
```

### POST `/api/v1/auth/login`

**请求**

```json
{ "email": "alice@example.com", "password": "Passw0rd!" }
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
- `status`：单状态，或逗号分隔多状态（如 `pending,queued`）。**未传时默认排除 `archived`**；查归档任务需显式 `status=archived`
- `priority` / `q`（项目地址关键词）/ `date_from` / `date_to`（`YYYY-MM-DD`）
- `limit`（默认 50，最大 200）/ `offset`

响应：`{ items, total, limit, offset }`

### GET `/api/v1/tasks/{id}`

任务只允许 owner 访问；非 owner 与不存在统一返回 404。该规则同样适用于事件、SSE、取消、重试、删除和节点记录。

### POST `/api/v1/tasks/{id}/cancel`

取消任务(已实现):先把任务/run/未完成节点标 `cancelled` 并 **提交后立刻返回**。`revoke(terminate=True)` 停 worker（Windows solo 经常杀不掉）；后台 `schedule_teardown_task_runtime` 只按 `crucible.task_id` 强拆该任务的 agent-runner（停 AI），不 `compose down` 可复用靶场。编排器每节点刷新库状态，已取消则停后续节点，且 execute 失败不得把 cancelled 改成 failed。靶场在静默满 TTL 1 小时且无 live 任务后由巡检销毁。

### POST `/api/v1/tasks/{id}/retry`  *(阶段 1 新增)*

重试任务(202 返 `{task_id, run_id, status:retrying}`)。新建 TaskRun，**从节点 0（源码获取）整条重跑**，不复用上一 run 的 NodeRun。上一 run 的节点/事件保留作历史。同一次 run 内 worker 崩溃仍可靠 NodeRun 断点续跑，与用户点「重试」不是同一条路径。

创建与重试均先提交 Task/TaskRun，再投递 Celery。Broker 投递失败时 Task/TaskRun 进入 `failed`，接口返回 503，不允许任务静默停在 queued/pending。

### DELETE `/api/v1/tasks/{id}?hard=true|false`  *(阶段 1 新增)*

删除任务。默认软删(`status=archived`)，已归档再软删返回 400；`hard=true` 物理删除需 `X-Confirm: true` header(级联清理 runs/nodes/events)。删除时 `teardown_task_runtime` 只拆该任务的 agent-runner，不拆可复用靶场。默认列表不返回已归档任务。

### GET `/api/v1/tasks/{id}/runs/{run_id}/nodes`  *(阶段 1 新增)*

返回该 run 的节点列表（前端进度条数据源）:

```json
[{
  "id": "uuid",
  "node_index": 0,
  "node_key": "source",
  "status": "pending|running|completed|failed|skipped|cancelled",
  "attempt": 1,
  "error_message": null,
  "started_at": "...",
  "finished_at": "...",
  "output": { "origin": "minio", "repo_dirname": "claudecodeui" }
}]
```

`output` 为解析后的节点产出（坏 JSON 为空对象）。源码节点含 origin/repo_dirname/commit_sha；画像含 language/framework/is_web/port/detected_services（不含 AI 长文）；靶场含 target_url（`http://{宿主机IP}:{compose 映射的 Web 端口}`，不是 localhost / 容器端口）与 initial_creds。AI 先根据 README、路由和鉴权配置判断是否存在登录功能：实际存在并可提供账密时为 `{username,password,login_url?}`（可由 `.vuln-env` 通过项目已有初始化机制创建靶场专用账号），确认无登录功能时为 `{auth_required:false,note?}`，存在登录但无法安全初始化时为 `{note}`，说明需自行注册、API Key 或其他前置条件。不要交空对象，也不要把“未找到凭据”冒充“无需登录”。

### GET `/api/v1/tasks/{id}/events`

历史事件（默认当前/最新一次 run，最近 1000 条，`limit` 1–1000）。重试会新建 run，历史 run 的 `phase.updated` 不混进本接口。含 `agent.thinking` / `agent.message` / `tool.call.*` / `agent.failed`（`title`+`hint`）/ `node.updated`。

事件时间：`created_at` 一律带 UTC 偏移（开发 SQLite 读回 naive datetime 也按 UTC 输出）；每条事件的 `payload.timestamp` 必有 epoch 秒 —— SDK 事件用 SDK 自带值，平台事件（`phase.updated` 等）落库时补齐，SSE 回放才不会显示成浏览器收到的时刻。

### GET `/api/v1/tasks/{id}/events/stream`

**SSE** 实时事件流（P0-1 已实现）。`Content-Type: text/event-stream`。

- 响应头：`X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`（防 nginx/反代缓冲）
- 事件名 = `Event.event_type`（`phase.updated` / `agent.thinking` / `agent.message` / `tool.call.*` / `agent.completed` / `agent.failed` / `node.updated` / `ready` / `error`）
- 启动先回放 **当前 run** DB 最近 1000 条（帧含 `replayed: true`），再订阅 Redis `task.{id}.events` 频道转发实时事件
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

分页查询。`{ items, total, limit, offset }`。每条含 `id, task_id, title, verdict, severity, status, summary, created_at`。

### GET `/api/v1/reports/{id}`

含正文、状态、结构化字段(verdict/cvss_score/severity/vulnerable_file/report_data/poc_language/poc_filename/poc_code/poc_usage/md_artifact_key/docx_artifact_key)。

`report_data.document_kind` 区分两类文档（`completed` 只表示工作流结束，漏洞是否成立看 `verdict`）：

- `vulnerability_report`（仅 `confirmed|partial`）：8 节 `product_intro` / `vulnerability` / `impact` / `details` / `reproduction` / `poc_commands` / `fix_suggestions` / `reporting_decision`。PoC 正本在 `poc_language`/`poc_filename`/`poc_code`/`poc_usage` 四列；`poc_commands` 是平台从正本生成的 Markdown 围栏。索引列必有 CVSS。
- `verification_record`（`not_reproduced` / `false_positive` / `code_reachable` / `code_smell`）：8 节 `product_intro` / `claimed_issue` / `whitebox_analysis` / `test_record` / `blocker` / `observed_facts` / `remaining_conditions` / `reporting_decision`。无 PoC、无 CVSS（`cvss_score`/`severity` 为 null）。`test_record` 保留失败请求与响应。

缺 `document_kind` 的旧数据按漏洞报告 8 节渲染，不自动重写。

报告详情、按任务读取、发布、导出和证据列表均只允许 owner 访问；非 owner 与不存在统一返回 404。

### GET `/api/v1/reports/{id}/export?format=json|md`

**已实现**。format=json 返回 `document_kind` + 对应 8 键对象（值为 Markdown 字符串）+ 索引字段;format=md 按文档类型加 `## N.` 标题后拼接。验证记录标题为「漏洞验证记录」。**不生成 docx/pdf**。

### GET `/api/v1/reports/{id}/evidences`

证据列表（日志 / 截图 / PoC），每条含 MinIO 预签名 `download_url`。

### POST `/api/v1/reports/{id}/evidences`

上传证据（multipart/form-data）。字段：`file`（文件，≤50MB）+ `kind`（`artifact|log|screenshot|poc`）。
上传 → MinIO `crucible-evidence` bucket → 落 `evidences` 表 → 返回带预签名 URL 的 `EvidenceResponse`。
owner 校验：所有环境均要求 `report.owner_id` 匹配当前用户。

---

## Project Context

### GET `/api/v1/projects/`

当前用户的项目列表：`{ items, total }`。任务创建时按 Git 地址自动登记。

### GET `/api/v1/projects/{id}`

项目元数据 + 画像快照（`detected_language` / `detected_framework` / `is_web` / `last_cloned_at`）。权威画像按 commit SHA 存在对应 `source_artifacts.profile_json`。`is_web=false` 时前端禁止从该项目开验证任务。非所有者或不存在 → 404。

### GET `/api/v1/projects/{id}/artifacts`

当前用户在该仓库已缓存的 MinIO 源码包（按 owner + host + `project_key` 匹配，`.git` 后缀不区分）。

```json
{ "items": [{ "ref_type": "branch", "ref_name": "main", "commit_sha": "...", "object_url": "...", "repo_dirname": "claudecodeui" }], "total": 1 }
```

非所有者或不存在 → 404。

### POST `/api/v1/projects/` / PUT `/api/v1/projects/{id}` / DELETE `/api/v1/projects/{id}`

手工登记/改备注/删除。日常用任务创建自动 upsert 即可。GET/PUT/DELETE 均校验 owner；非所有者或不存在 → 404。

---

## Lab Context

所有端点只访问当前用户的 lab。列表与详情中的 lab 对象包含 `live_task_count`；大于 0 时，所有靶场级和容器级写操作均拒绝。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/v1/labs` | 按 `project_id` 分组返回项目及其 labs；lab 含 status、commit_sha、target_url、ttl_remaining_seconds、live_task_count、容器摘要 |
| GET | `/api/v1/labs/{id}` | 返回 lab 详情及所属 compose 项目的容器列表（name、status、ports、image），并刷新 TTL |
| POST | `/api/v1/labs/{id}/actions/stop` | `compose stop`，状态变为 `stopped` |
| POST | `/api/v1/labs/{id}/actions/start` | `compose start`，状态变为 `ready` 并刷新 TTL |
| POST | `/api/v1/labs/{id}/actions/rebuild` | 状态变为 `creating`，执行 `compose up -d --build`，成功为 `ready`、失败为 `failed`。本地缺 compose 时先从 MinIO 拉配方，仍无则 400。 |
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

Provider **没有独立启用/停用字段**。Agent 运行时只读取唯一的 `is_default=true` 项；列表「状态」由该字段派生（默认 / 备用）。真正启用某个 Provider = `POST .../activate`。

响应字段含 `is_default`，**不含** `enabled`。

### GET `/api/v1/settings/llm/providers`

要求 Bearer JWT。Provider 当前是平台全局配置，RBAC 落地前任意已登录用户均可管理。API Key 只返回掩码（`***last4`）。

### POST `/api/v1/settings/llm/providers`

新建。`api_key` **明文落库**(存 `api_key_encrypted` 字段),响应层 `mask_secret` 掩码。当前尚无默认项时，新建的第一条自动 `is_default=true`。请求体可带 `is_default` 显式抢默认。

### PUT `/api/v1/settings/llm/providers/{id}`

只改名称 / 端点 / 模型 / Key / 超时。不通过 PUT 切换默认。

### DELETE `/api/v1/settings/llm/providers/{id}`

### POST `/api/v1/settings/llm/providers/{id}/activate`

唯一默认 Provider：事务内把其他 Provider 的 `is_default` 置 false。

### POST `/api/v1/settings/llm/providers/{id}/test`

### POST `/api/v1/settings/llm/test`

真实请求 `base_url` 验证。Base URL 必须是 HTTPS 域名，禁止 IP 字面量、userinfo、fragment；DNS 须为公网或 TUN fake-ip（`198.18.0.0/15`），仍拒绝真实私网/回环/元数据；不跟随重定向。不使用域名白名单。

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
| 503 | `SERVICE_UNAVAILABLE` | Docker / Redis / MinIO 不可用；Celery 投递失败 |

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