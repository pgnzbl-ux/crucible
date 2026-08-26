# Crucible API 契约（v1）

> 本文件是**对外契约**——路由 / 请求 / 响应 / 错误码。完整 OpenAPI 见 FastAPI `/openapi.json`。

## 通用约定

- 前缀：`/api/v1`
- 鉴权：除 `/health`、`/health/ready`、`/api/v1/auth/login`、`/api/v1/auth/register`、`/api/v1/auth/setup` 外全部需 Bearer JWT。`/metrics` 在配置了 `METRICS_TOKEN` 时需 `Authorization: Bearer <METRICS_TOKEN>`（生产强制配置）。
- 时间：ISO-8601 UTC
- 错误响应同时给契约信封与兼容字段（前端优先读 `error.message`，再回退 `detail`）：

```json
{
  "error": { "code": "TASK_NOT_FOUND", "message": "任务不存在", "details": { "task_id": "..." } },
  "detail": "任务不存在"
}
```

`detail` 在历史接口（如靶场 `LAB_IN_USE`）可以是对象 `{code,message,task_ids}`；信封里 `error.details` 装除 code/message 外的附加字段。未捕获异常仍由 FastAPI 兜底 500。完整语义见 `.claude/rules/error-handling.md` §3。

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
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "id": "uuid", "email": "alice@example.com", "display_name": "Alice", "is_active": true, "is_admin": true, "role": "admin" }
}
```

### GET `/api/v1/auth/me`

当前用户信息。响应同 `UserResponse`：`id / email / display_name / is_active / is_admin / role`。身份字段是 **email + display_name**，没有 `username`。

---

## Task Context

### POST `/api/v1/tasks`

创建任务。Git 地址非法（非 http/https/ssh、缺 space/project）→ **400** `BAD_REQUEST`，不得用原文落库。创建时按规范化 Git 地址 upsert Project；Project 写入失败不得吞掉后继续建任务。

**请求**

```json
{
  "project_address": "https://github.com/owner/repo",
  "project_ref": "main",
  "project_ref_type": "branch | tag | commit（可选；省略则自动推断）",
  "clone_depth": 1,
  "source_type": "git",
  "task_type": "verify | discovery",
  "vulnerability_description": "漏洞描述（task_type=verify 必填，≥10 字符；discovery 必空）",
  "priority": "low | medium | high | critical"
}
```

`task_type`（默认 `verify`）：`verify`=漏洞验证（人工给描述，扫描/聚类/二审/调度节点 `VERIFY_MODE` skip，走 6 个终认节点）；`discovery`=仓库审计（禁止填描述，跑全 13 节点，dispatch 把**全部合格可疑真洞**入 Redis db3 终认队，LeadWorker 复用同一套 audit/reproduce）。

`clone_depth`：浅克隆层数，默认 `1`；`0` 表示全量 clone（流量更大）。`project_ref_type` 显式指定可避免 tag 名被误判为 branch 导致缓存 SHA 对不上。`source_type` 默认 `git`；`local_upload` 时 `project_address` 必须是已登记的 `upload://local/{project_id}`，节点 0 从 MinIO 解开原始包，不再 clone。

**响应 202**：`TaskDetail`（含首次 `runs[]`）。先提交 Task/TaskRun 再投 Celery；Broker 失败 → 任务/run 标 `failed`，接口 **503**。未配置默认 LLM Provider、默认项无 API Key、或 agent-runner 镜像不存在/Docker 不可用 → **400**，不落任务、不投递 worker。运行槽满不在创建时拒绝，任务入队后由 worker 按间隔重试。

### POST `/api/v1/tasks/upload`

上传本地源码包并创建任务（multipart）。字段：

- `file`：zip / tar / tar.gz，≤200MB（解压后 ≤1GB、≤50000 文件）
- `vulnerability_description`：至少 10 字符
- `name` / `priority` / `vulnerability_reasoning` / `credential_refs`（JSON 数组字符串）可选

非法压缩路径（zip-slip）、空包、不支持的格式 → **400**。超限 → **413**。MinIO 入库失败 → **503**。同 owner 下**项目名称已存在** → **409** `CONFLICT`（不按内容 sha256 复用）。任务的 `project_address` 为 `upload://local/{project_id}`，`source_type=local_upload`。原始包写入 MinIO `source/{owner}/upload/{project_id}/original.tar.gz`，`.vuln-env` 配方不回写该对象。

**响应 202**：同 `POST /tasks` 的 `TaskDetail`。

日常请先在源码管理 `POST /projects/upload` 登记，再用 `POST /tasks` + `source_type=local_upload` 开任务。本接口是快捷路径，登记规则相同。

### GET `/api/v1/tasks`

分页查询。Query：
- `status`：单状态，或逗号分隔多状态（如 `pending,queued`）。**未传时默认排除 `archived`**；查归档任务需显式 `status=archived`
- `priority` / `q`（项目地址关键词）/ `date_from` / `date_to`（`YYYY-MM-DD`）
- `limit`（默认 50，最大 200）/ `offset`

响应：`{ items, total, limit, offset }`

### GET `/api/v1/tasks/stats`

当前用户非归档任务的状态计数。工作台卡片用这一次查询，不要对 `/tasks` 连打 `limit=1`。

响应：`{ total, by_status }`，`by_status` 为 `status → count`，缺省状态视为 0。必须注册在 `/{id}` 之前。

### GET `/api/v1/tasks/{id}`

任务只允许 owner 访问；非 owner 与不存在统一返回 404。该规则同样适用于事件、SSE、取消、重试、删除和节点记录。

`TaskDetail.usage`（有台账时）：`prompt_tokens` / `completion_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens` / `total_tokens` / `sessions`。`total_tokens` 为前四项之和（SDK/API 回传聚合，见 `docs/token-usage-accounting.md`）。

### POST `/api/v1/tasks/{id}/cancel`

取消任务(已实现):先把任务/run/未完成节点标 `cancelled` 并 **提交后立刻返回**。`revoke(terminate=True)` 停 worker；后台 `schedule_teardown_task_runtime` 只按 `crucible.task_id` 强拆该任务的 agent-runner（停 AI），不 `compose down` 可复用靶场。编排器每节点刷新库状态，已取消则停后续节点，且 execute 失败不得把 cancelled 改成 failed。靶场 TTL 从任务终态起算，静默满 1 小时且无 live 任务后由巡检销毁。

### POST `/api/v1/tasks/{id}/retry`  *(已实现，含 from_node 断点续跑)*

重试任务(202 返 `{task_id, run_id, status:retrying, from_node}`)。任务不存在或非 owner → **404** `TASK_NOT_FOUND`（不是 400）。非法状态 / 非法 `from_node` / 前置节点未完成 → **400**。未配置默认 LLM Provider、默认项无 API Key、或 agent-runner 镜像不存在/Docker 不可用 → **400**，不改任务状态、不新建 run。

- 默认：新建 TaskRun，**从节点 0（源码获取）整条重跑**，不复用上一 run 的 NodeRun。
- 可选 query `from_node=`（除 `source`/`profile` 外的管线节点：`scan_*`/`env_ready`/`cluster`/`screen`/`triage`/`dispatch`/`audit`/`reproduce`/`report`）：拷贝上一 run 里该节点**之前**已 `completed`/`skipped` 的 NodeRun 进新 run，编排器断点续跑，只重跑该节点及之后。前置未完成 → 400。`source`/`profile` 不是合法起点（请整条重试）。
- 上一 run 的节点/事件保留作历史。工作区按 `task_id` 固定路径；新 run 已有 completed 节点则保留工作区（失败任务本就会留目录），否则清空重拉。不拆可复用靶场。
- 同一次 run 内 worker 崩溃仍可靠 NodeRun 断点续跑，与用户点「重试」不是同一条路径。

创建与重试均先提交 Task/TaskRun，再投递 Celery。Broker 投递失败时 Task/TaskRun 进入 `failed`，接口返回 503，不允许任务静默停在 queued/pending。

### DELETE `/api/v1/tasks/{id}?hard=true|false`  *(阶段 1 新增)*

删除任务。默认软删(`status=archived`)，已归档再软删返回 400；`hard=true` 物理删除需 `X-Confirm: true` header(级联清理 runs/nodes/events)。`pending`/`queued` 删除会先 revoke 对应 Celery 任务再归档，避免 worker 把已归档任务改回 running。删除时 `teardown_task_runtime` 只拆该任务的 agent-runner，不拆可复用靶场。默认列表不返回已归档任务。

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
  "output": { "origin": "minio", "repo_dirname": "claudecodeui" },
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "total_tokens": 0
  }
}]
```

`usage` 可选：本 run 该 `node_key` 在 `agent_usage` 台账的聚合（无消耗则省略）。数值全部来自 Claude Agent SDK / 兼容网关回传（优先 `ResultMessage.model_usage`，否则 `usage`）；`cache_*` 禁止自算。`total_tokens = prompt + completion + cache_read + cache_creation`。规则见 `docs/token-usage-accounting.md`。

`output` 为解析后的节点产出（坏 JSON 为空对象）。源码节点含 origin/repo_dirname/commit_sha；画像含 language/framework/is_web/port/detected_services（不含 AI 长文）；靶场含 target_url（`http://{宿主机IP}:{compose 映射的 Web 端口}`，不是 localhost / 容器端口）与 initial_creds。AI 先根据 README、路由和鉴权配置判断是否存在登录功能：实际存在并可提供账密时为 `{username,password,login_url?}`（可由 `.vuln-env` 通过项目已有初始化机制创建靶场专用账号），确认无登录功能时为 `{auth_required:false,note?}`，存在登录但无法安全初始化时为 `{note}`，说明需自行注册、API Key 或其他前置条件。不要交空对象，也不要把“未找到凭据”冒充“无需登录”。

### GET `/api/v1/tasks/{id}/events`

历史事件（默认当前/最新一次 run，最近 1000 条，`limit` 1–1000）。重试会新建 run，历史 run 的 `phase.updated` 不混进本接口。含 `agent.thinking` / `agent.message` / `tool.call.*` / `agent.failed`（`title`+`hint`）/ `node.updated` / `usage.updated`。

`usage.updated`：某次会话入台账后推送。payload 含 `node_key`、`usage`（本会话四字段+`total_tokens`）、`cumulative`（本节点本 run 累计，同形状）。不转发 SDK `thinking_tokens` 心跳。

事件时间：`created_at` 一律带 UTC 偏移（开发 SQLite 读回 naive datetime 也按 UTC 输出）；节点 `started_at` / `finished_at` 同样带 UTC 偏移。每条事件的 `payload.timestamp` 必有 epoch 秒 —— SDK 事件用 SDK 自带值，平台事件（`phase.updated` 等）落库时补齐，SSE 回放才不会显示成浏览器收到的时刻。AI 事件 payload 带 `node_key` / `node_run_id`，`AgentEvent.node_run_id` 列同步写入；思考/工具事件可按节点过滤而不靠时间边界猜测。

### POST `/api/v1/tasks/{id}/events/ticket`

领取短命 SSE ticket。需 Bearer。响应 `{ ticket, expires_in }`（默认约 120s）。票面绑定 `user_id` + `task_id`。

### GET `/api/v1/tasks/{id}/events/stream`

**SSE** 实时事件流。`Content-Type: text/event-stream`。

- 响应头：`X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`
- 鉴权：**推荐** `?ticket=<sse_ticket>`（先调 ticket 接口）；开发环境兼容 `?token=<access jwt>`；**生产拒绝** `?token=`
- 启动先回放当前 run DB 最近 1000 条（`replayed: true`），再订阅 Redis
- 续播：`Last-Event-ID` 或 `?last_event_id=`
- 15s 心跳；客户端断开立即清理 Redis 订阅
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

报告详情、按任务读取、发布、导出和证据列表均只允许 owner 访问；非 owner 与不存在统一返回 404。`GET /api/v1/reports/task/{task_id}` 在同一任务有多份报告时返回 **最新 run** 的那一份。

### GET `/api/v1/reports/audits`

审计报告 Tab：当前用户的 `task_type=discovery` 任务聚合（有 Report 或有终局漏洞报告即可出现）。Query：`q` / `limit`（默认 50，最大 200）/ `offset`。同一任务多份 Report 只保留最新一份。

响应 `{ items, total, limit, offset }`。每条 `AuditTaskSummary`：`task_id / project_id / project_address / project_ref / task_status / report_id / report_status / confirmed_count / code_reachable_count / vuln_report_count / created_at / updated_at / published_at`。

### GET `/api/v1/reports/audits/{task_id}`

单条审计任务摘要。非 owner / 非 discovery / 不存在 → **404**。

### GET `/api/v1/reports/audits/{task_id}/vulns`

该任务下单漏洞报告卡片列表。响应 `{ task_id, items, total }`。

### GET `/api/v1/reports/audits/{task_id}/vulns/{group_id}`

单份漏洞报告。Query `format=json|md`（默认 json）。`md` 为附件下载。不存在 / 非 owner → **404**。

### GET `/api/v1/reports/{id}/export?format=json|md`

**已实现**。format=json 返回 `document_kind` + 对应 8 键对象（值为 Markdown 字符串）+ 索引字段;format=md 按文档类型加 `## N.` 标题后拼接。验证记录标题为「漏洞验证记录」。**不生成 docx/pdf**。

### GET `/api/v1/reports/{id}/evidences`

证据列表（日志 / 截图 / PoC），每条含 MinIO 预签名 `download_url`。

### POST `/api/v1/reports/{id}/evidences`

上传证据（multipart/form-data）。字段：`file`（文件，≤50MB）+ `kind`（`artifact|log|screenshot|poc`）。
上传 → MinIO `crucible-task`（kind=`evidence`，key=`evidence/{owner_id}/{task_id}/{evidence_id}/{file_name}`）→ 落 `evidences` 表 → 返回带预签名 URL 的 `EvidenceResponse`。
owner 校验：所有环境均要求 `report.owner_id` 匹配当前用户。报告不存在/非 owner → **404**；非法 `kind` → **400**（不得静默改成 `artifact`）；MinIO 失败 → **503**。

---

## Finding Context（漏洞线索台，discovery-spec §9.1）

仓库审计（`task_type=discovery`）产出的合格线索与终认结果。仅 owner 可访问，非 owner 与不存在统一 404。

对用户文案必须用「可疑真洞 / 误报 / 二审未决」，禁止把内部码 `tp`/`fp` 当标签原文。`ai_verdict` 字段仍返回内部码，由 UI 翻译。

### GET `/api/v1/findings/stats`

当前用户任务下告警组计数。工作台卡片一次查询，不要对 `/groups` 连打。

响应 `{ total, by_status, by_resolution, by_queue }`。`by_queue` 键为 `workbench` / `verifying` / `confirmed` / `reachable`（语义同列表 `scope`）。未登录 → **401**。

### GET `/api/v1/findings/groups`

分页查询告警组。Query：`task_id` / `status` / `resolution`（`confirmed|partial|code_reachable|false_positive|ignored`）/ `cwe` / `ai_verdict` / `engine` / `clue_grade` / `scope` / `q` / `limit`（默认 50，最大 200）/ `offset`。未传 `task_id` 时只返回当前用户任务集合内的组。

`scope`：`workbench`（默认，验证中 + 已确认 + 代码可达）/ `verifying`（`dispatched`）/ `confirmed`（`confirmed|partial`）/ `reachable`（`code_reachable`）/ `all`。未传 `scope` 且未带 `status`/`resolution` 时按 `workbench`。

响应 `{ total, items }`。每条 `AlertGroupSummary`：`id / task_id / cwe / file_path / function_symbol / line_span / member_count / engine_set / status / clue_grade / ai_verdict / ai_confidence / priority / resolution / created_at / updated_at`。

`status` 状态机：`clustered → adjudicated → dispatched → resolved(*)`。长期可见终态为 `confirmed | partial | code_reachable`。明确误报不占组。`ai_verdict` 内部码为 `tp / fp / need_more_context / bypass`（osv 依赖情报默认不进终认，除非 `called` 且 T3 可达）。

### GET `/api/v1/findings/groups/ids`

跨页全选：筛选条件与列表相同（无分页），响应 `{ total, ids }`。结果超过 2000 条返回 400，需先收窄筛选。

### POST `/api/v1/findings/groups/batch-delete`

批量物理删除。请求 `{ ids: string[] }`（1–100）。响应 `{ deleted, skipped }`；`skipped[].reason` 为 `not_found`（含非 owner）或 `in_progress`（LeadRun/验证 Task 仍进行中）。级联删 Adjudication / ReviewAction / LeadRun，清空指向该组的 `source_alert_group_id`，**不**删 RawFinding / 验证 Task。

### DELETE `/api/v1/findings/groups/{group_id}`

单条物理删除，语义同批量。成功 204；不存在/非 owner → 404；终认进行中 → 409。

### GET `/api/v1/findings/groups/{group_id}`

告警组详情：摘要字段 + `members`（成员 findings）、`representative`（代表成员）、`adjudications`（AI 二审记录，含 prompt/response 全文）、`reviews`（人工复核动作）、`verification_task_id` / `verification_verdict`（关联验证任务及终认判定）。

读取时做**惰性对账**（§4.4 丢事件兜底）：组仍 `dispatched` 且关联 Task 已有终态 verdict → 当场回写。

### POST `/api/v1/findings/groups/{group_id}/review`

人工复核，动作即标注数据。请求体：

```json
{ "action": "confirm | reject | revise_cwe | adjust_confidence",
  "reason_tags": ["..."], "reason_text": "...",
  "cwe": "CWE-89", "confidence": 0.9 }
```

- `confirm` → 组 `resolved(confirmed)`；`reject` → `resolved(false_positive)`（**必须带 `reason_tags`**）
- `revise_cwe` 必填 `cwe`；`adjust_confidence` 必填 `confidence`（0–1）

响应 200：更新后的 `AlertGroupSummary`。

### POST `/api/v1/findings/groups/{group_id}/revive`

遗留接口：新产生的误报不入库，故无复活对象。若组仍存在（历史行），行为保持「退回 `needs_review`」。新 UI 不提供入口。

### POST `/api/v1/findings/groups/{group_id}/dispatch`

人工次级入口：另开 `task_type=verify`（同 project/ref，Lab 复用；同一套终认节点，不是第二套产品）。请求体 `{ "include_engine_conclusion": false }`——勾选才在描述追加【引擎线索】原文（默认不锚定）。响应 `{ group_id, verification_task_id }`。组随后 `dispatched`，验证任务终态经事件回流写回该组（`confirmed` / `code_reachable` 可见；终认证伪不展示）。

已 `dispatched` 重复投递 → **409**。组缺少代表成员 → **409**。Celery 投递失败 → **503**（验证任务已落库为 `failed`；组状态滚回抢占前，可重试本接口）。与 `POST /tasks` Broker 失败同为 503，不是 502。

---

## Project Context

### GET `/api/v1/projects/`

当前用户的项目列表：`{ items, total }`。Git 任务创建时按地址自动登记；本地包请在源码管理上传登记（`source_type=local_upload`），也可从任务页快捷上传（同名 409）。

Git 项目的 `source_refs` 为已缓存制品的 `{ ref_type, ref_name }`（`branch` / `tag` / `commit`）；尚无制品时回退为 `default_ref` 的推断结果（空则 `branch/HEAD`）。上传项目 `source_refs` 为空。任务新建下拉用 `项目名称：{ref_type}/{ref_name}  <git_url>` 展示。

### GET `/api/v1/projects/{id}`

项目元数据 + 画像快照（`detected_language` / `detected_framework` / `is_web` / `last_cloned_at` / `source_type`）。Git 项目同样返回 `source_refs`（见列表接口）。权威画像按 commit SHA 或上传包 sha256 存在对应 `source_artifacts.profile_json`。`is_web=false` 时前端禁止从该项目开验证任务。非所有者或不存在 → 404。

### GET `/api/v1/projects/{id}/artifacts`

Git 项目：该仓库已缓存的 MinIO 源码包（按 owner + host + `project_key`，`.git` 后缀不区分）。上传项目：入库时的**原始包**（一项目一份，不按内容指纹复用）。

```json
{ "items": [{ "ref_type": "branch", "ref_name": "main", "commit_sha": "...", "object_url": "...", "repo_dirname": "claudecodeui" }], "total": 1 }
```

非所有者或不存在 → 404。

### DELETE `/api/v1/projects/{id}/artifacts/{artifact_id}`

删除一条源码制品索引（Git 缓存包或上传原始包）。204。

- 制品必须属于该项目且当前用户是 owner；否则与项目详情一致 → 404。
- **独享** `object_key`：同时删 MinIO 对象。
- **多行共用**同一 `object_key`（历史脏数据：同一 SHA 被记成 branch 又记成 tag）：只删本行，对象保留。
- 删的是缓存索引，进行中任务的 `host_workdir` 不受影响；下次源码节点未命中则重新 clone / 需重新上传。

### POST `/api/v1/projects/`

手工登记 **Git** 仓库。`git_url` 必须是合法 Git 地址（非法 → 400）。同 owner **名称已存在** → 409 `CONFLICT`。`source_type=git`。

### POST `/api/v1/projects/upload`

登记本地源码包，**不创建任务**（multipart）：

- `file`：zip / tar / tar.gz，≤200MB
- `name`：必填，同 owner 重名 → 409 `CONFLICT`
- `description`：可选

对象键 `source/{owner}/upload/{project_id}/original.tar.gz`。`git_url` 为 `upload://local/{project_id}`。MinIO 失败 → 503。

### PUT `/api/v1/projects/{id}` / DELETE `/api/v1/projects/{id}`

改名称/备注/默认引用/删除。改名若与同 owner 其他项目冲突 → 409。GET/PUT/DELETE 均校验 owner；非所有者或不存在 → 404。

删除项目时同步删除该仓库（`project_key`）下当前用户的全部 `source_artifacts`；独享 `object_key` 的 MinIO 对象一并删除，共用对象保留。进行中任务的工作目录不受影响。仍被任务或靶场引用 → 409 `PROJECT_IN_USE`（不静默拆外键）。

---

## Lab Context

所有端点只访问当前用户的 lab。列表与详情中的 lab 对象包含 `live_task_count`；大于 0 时，停止/启动/重建及容器级写操作均拒绝。**例外：`creating` 的销毁先取消占用任务再 `down`，不返回 409。** `ttl_remaining_seconds` 仅 `ready`/`stopped` 为非负整数，其余状态为 `null`（创建中不计 TTL）。**TTL 从任务终态（completed / failed / cancelled / needs_review）且无其它 live 占用时起算**；有 live 任务时对外显示满额 TTL（倒计时未开始）。静默满 TTL 且无 live 任务后由巡检销毁。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/v1/labs` | 按 `project_id` 分组返回项目及其 labs；lab 含 status、commit_sha、target_url、ttl_remaining_seconds、live_task_count、容器摘要。ttl_remaining_seconds 仅 ready/stopped 有值；有 live 任务时为满额 ttl（未起算）。status 按 compose 实际容器校正并回写（expired/stopped 且容器在跑 → ready；无 live 任务时 ready 全停 → stopped、无容器 → expired） |
| GET | `/api/v1/labs/{id}` | 返回 lab 详情及所属 compose 项目的容器列表（name、status、ports、image），按容器实际状态校正 status；仅空闲 ready/stopped 刷新 TTL 锚点 |
| POST | `/api/v1/labs/{id}/actions/stop` | `compose stop`，状态变为 `stopped` |
| POST | `/api/v1/labs/{id}/actions/start` | `compose start`，状态变为 `ready` 并刷新 TTL |
| POST | `/api/v1/labs/{id}/actions/rebuild` | 状态变为 `creating`，执行 `compose up -d --build`，成功为 `ready`、失败为 `failed`。本地缺 compose 时先从 MinIO 拉配方，仍无则 400。 |
| DELETE | `/api/v1/labs/{id}` | `compose down -v`，状态变为 `destroyed` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/stop` | `docker stop` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/start` | `docker start` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/restart` | `docker restart` |
| DELETE | `/api/v1/labs/{id}/containers/{name}` | `docker rm -f` |

`{name}` 不属于该 lab 的 compose 项目时返回 404。存在 live 任务时，停止/启动/重建及容器写操作返回 **409**，错误码 `LAB_IN_USE`（`creating` 的 DELETE 除外）：

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

高级设置（对本 Provider **全局**约束：全部 Agent 节点沙箱 + 轻量二审 Messages + 测连接）：

| 字段 | 默认 | 语义 |
|---|---|---|
| `temperature` | `0.2` | Anthropic Messages `temperature`；Agent CLI 暂不透传（SDK 无官方能力） |
| `max_context_tokens` | `200000` | 注入 `CLAUDE_CODE_MAX_CONTEXT_TOKENS`，CLI 按此窗口做 autocompact |
| `effort` | `high` | `low\|medium\|high\|xhigh\|max\|auto`；注入 `CLAUDE_CODE_EFFORT_LEVEL` + Messages `output_config.effort` |

缺省只来自列 / Schema / 新建表单，禁止业务路径再写死温度等魔法数。

### GET `/api/v1/settings/llm/providers`

要求 Bearer JWT。Provider 当前是平台全局配置，RBAC 落地前任意已登录用户均可管理。API Key 只返回掩码（`***last4`）。响应含上述高级字段。

### POST `/api/v1/settings/llm/providers`

新建。`api_key` **Fernet 加密落库**(存 `api_key_encrypted` 字段；存量明文可读),响应层 `mask_secret` 掩码。当前尚无默认项时，新建的第一条自动 `is_default=true`。请求体可带 `is_default` 显式抢默认。未传高级字段时用全局默认（0.2 / 200000 / high）。

### PUT `/api/v1/settings/llm/providers/{id}`

可改名称 / 端点 / 模型 / Key / 超时 / `temperature` / `max_context_tokens` / `effort`。不通过 PUT 切换默认。

### DELETE `/api/v1/settings/llm/providers/{id}`

### POST `/api/v1/settings/llm/providers/{id}/activate`

唯一默认 Provider：事务内把其他 Provider 的 `is_default` 置 false。

### POST `/api/v1/settings/llm/providers/{id}/test`

使用该 Provider 已存的 temperature / effort 发测连接请求。

### POST `/api/v1/settings/llm/providers/{id}/agent-test`

管理员：用该 Provider 起一次真实 Docker Agent canary（校验 Bash / MCP `submit_result` / 多轮 / 凭据隔离）。不存在 → **404**。响应 `{ ok, message, checks, provider_id, model, duration_ms, num_turns, usage }`。

### POST `/api/v1/settings/llm/test`

真实请求 `base_url` 验证。可选带 `temperature` / `effort`（缺省用全局默认）。由 `.env` 的 `LLM_BASE_URL_RELAXED` 控制：`true` 暂不限制（http/https、域名或 IP，含本机/私网）；`false` 恢复最早安全限制——仅 HTTPS 域名、禁止 IP 字面量、DNS 须公网或 TUN fake-ip。两种模式均禁止 userinfo、fragment。

### GET `/api/v1/settings/runtime`

平台全局「同时 running 的验证任务数」。无行则插入默认 `max_concurrent_tasks=1`。RBAC 落地前任意已登录用户可读写。

响应：

```json
{
  "max_concurrent_tasks": 1,
  "max_allowed": 4,
  "worker_pool": "prefork"
}
```

`max_allowed` 来自 `AGENT_RUNNER_CONCURRENCY_LIMIT`（1–8，默认 4）。`worker_pool` 固定为 `prefork`（Celery worker 由 `run_worker.py` 启动）。

### PUT `/api/v1/settings/runtime`

请求：`{ "max_concurrent_tasks": 2 }`（`extra=forbid`）。必须 `1 <= n <= max_allowed`，否则 422 `VALIDATION_FAILED`。改完立即作用于新抢槽；不取消已 running 的任务。Worker 先抢 Redis 槽 `crucible:running_run_ids` 再 claim；无槽保持 `queued` 并 `retry(countdown=15)`。

### Credentials（任务级凭据，P1-6 已实现）

`GET /api/v1/settings/credentials`（列表，`{items,total}`）、`POST`（新建，201）、`PUT /api/v1/settings/credentials/{id}`、`DELETE .../{id}`（204）。

- 字段：`name` / `kind`（`env_var|file`）/ `target`（env_var→大写下划线变量名；file→`.secrets/<target>` 文件名）/ `secret`（写入明文，**Fernet 加密落库**）/ `description`
- **Fernet 落库**（存 `secret_encrypted`；`seal_secret` 写入、`reveal_secret` 读取并兼容存量明文；响应只回 `secret_masked` + `has_secret`），与 LLM Provider Key 同策略
- 注入：任务引用凭据后由 Credential Proxy 按 kind 注入 agent-runner（env → 容器环境变量；file → `/workspace/.secrets/` 600），任务结束销毁

---

## 健康检查 / 监控

- `GET /health` → `{ "status": "ok", "version": "<app_version>" }`
- `GET /health/ready` → Postgres + Redis 探活；失败 503
- `GET /metrics` → Prometheus；配置 `METRICS_TOKEN` 时需 Bearer（生产强制）

---

## 错误码汇总

| HTTP | code | 场景 |
|---|---|---|
| 400 | `BAD_REQUEST` | JSON 解析失败等 |
| 401 | `UNAUTHENTICATED` | 缺 / 错 token |
| 403 | `FORBIDDEN` | 权限不足（待 P1-9 RBAC） |
| 404 | `NOT_FOUND` / `TASK_NOT_FOUND` / `REPORT_NOT_FOUND` | 资源不存在 |
| 409 | `CONFLICT` / `STATE_INVALID` / `LAB_IN_USE` | 状态机非法转换 / email 重名 / 项目名称冲突 / 靶场正被 live 任务占用 |
| 422 | `VALIDATION_FAILED` | Pydantic 字段错误 |
| 429 | `RATE_LIMITED` | （待补） |
| 503 | `SERVICE_UNAVAILABLE` | Docker / Redis / MinIO 不可用；Celery 投递失败 |

---

## 待补（P0/P1/P2 路线）

- ~~P0-0: Agent 编排~~ ✅ 平台 13 节点编排(见 docs/discovery-spec.md)
- ~~P0-1: `GET /tasks/{id}/events/stream` SSE 端点~~ ✅
- ~~P0-2: `POST /tasks/{id}/cancel` 真正生效~~ ✅（revoke + 容器销毁）
- ~~P0-3: JWT 闭环 + SSE `?token=` 鉴权~~ ✅
- ~~P0-4: `POST /reports/{id}/evidences` 上传~~ ✅
- ~~P1-6: Credential Proxy（补齐插件 `credential_store` 能力）~~ ✅（`/settings/credentials` CRUD，见上文 Credentials 节）
- P1-7: `Authorization: ApiKey xxx` 鉴权依赖
- P1-8: OIDC 回调 `/auth/oauth/{provider}/callback`
- P1-9: RBAC 权限矩阵
- 报告导出已实现(GET /reports/{id}/export?format=json|md),docx 不再支持
- P1-11: 审计日志端点 `/audit/logs`
- P2-12: OpenAPI 自动生成前端 types