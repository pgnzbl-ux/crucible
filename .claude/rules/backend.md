---
paths: ["backend/app/contexts/**/*.py", "backend/app/shared/**/*.py", "backend/app/core/**/*.py"]
---

# Crucible 后端架构规范

> 主线对照与路线见 `docs/development-guide.md` §1-2；本文件只写**项目特有**架构约束。

## 1. Bounded Context 五件套

每个 Context 目录固定包含：`api.py / models.py / schemas.py / service.py / repository.py`。可选：`events.py`（领域事件发布器）、`tasks.py`（Celery 工作流）。

- **跨 Context 数据访问走 Service 接口或事件**，禁止直接 import 对方 repository
- **禁止跨 Context 建 ORM relationship**（mapper 报错），只保留 `ForeignKey` + 整数/UUID ID，需要时手动查
- 新表必须在其所属 Context 的 `models.py` 中定义；Celery worker 启动时 import 该 models 以注册 metadata（否则 FK 解析失败）。Alembic 基线 `c18a0e9b4d21`（`metadata.create_all`）与 `init_db()` 同源；索引/约束增量走后续 revision（如 `b7e4c2a19f08`）

## 2. 九个 Context 的职责边界

| Context | 核心模型 | 关键职责 |
|---|---|---|
| `identity` | users | 注册、登录、JWT、bcrypt（**锁 bcrypt==4.0.1**） |
| `task` | tasks / task_runs / node_runs / agent_events | 任务 CRUD、状态机(含 retry/delete/archived)、按子图断点续跑、事件查询 |
| `agent` | （无自有表，消费 settings 与 task） | Agent 执行器抽象、Celery 工作流、沙箱编排 |
| `project` | projects / source_artifacts | 项目元数据 + 按 SHA 的画像缓存；源码 tar.gz 按 owner+host 隔离缓存在 MinIO `crucible-durable`（agent 只调 `acquire_source()` / ProjectService） |
| `lab` | labs | 靶场生命周期：复用 / TTL / stop / start / rebuild / destroy，按 compose project 名操作容器 |
| `report` | reports / evidences | 报告生成 + 状态机 + MinIO 归档 |
| `finding` | raw_findings / alert_groups / adjudications / review_actions | 告警复核台：归一化告警、指纹分组、AI 二审、人工复核、revive、人工 dispatch |
| `discovery` | scan_runs | 扫描运行登记：一引擎一节点一 ScanRun，SARIF 归档 |
| `settings` | llm_providers / credentials | LLM Provider + 凭据后台 CRUD（**Fernet 落库 + 响应掩码 + 激活唯一性**） |

`finding` 与 `task` 只走逻辑指针（`Task.source_alert_group_id`，**无 FK 无 relationship**）；判决回流经领域事件 + 惰性对账，禁止 task/report 直写 `alert_groups`。

新增 Context 时单独评审，避免膨胀为"通用业务包"。

## 3. 异步链路：async/await 全栈贯通

- DB → 消息（Redis）→ HTTP（FastAPI）→ 前端流（SSE），任一环节混同步都破坏吞吐
- SQLAlchemy 2.0 **必须**用 `AsyncSession` + `select()` 形式，禁止 `session.execute(query)` 旧式
- Celery worker **不复用**主进程的 async engine —— 用独立 NullPool engine（参考 `agent/tasks.py::_worker_engine`）
- 业务时间列必须 `DateTime(timezone=True)`，与 `created_at`/`updated_at` 一致。naive datetime 一律视为 UTC（`app/shared/time.py`）。asyncpg 不能把 aware UTC 写入 `TIMESTAMP WITHOUT TIME ZONE`

## 4. Agent Runner 抽象（`core/agent_runner.py`）

`AgentRunnerManager` 是**平台能力**，不是业务代码能跳过的层。创建/重试与 worker 共用 `contexts/agent/preflight.py::require_platform_ready`（默认 LLM Provider + API Key + agent-runner 镜像）；镜像检查失败含 Docker 不可用。运行槽满不在提交时 400，入队后由 worker 按间隔重试。

`AgentRunnerSpec` 默认值即安全基线：

- 非 root + 只读 rootfs + `cap_drop: ["ALL"]`
- 资源限制（内存/CPU/PIDs）
- 超时回收
- tmpfs 挂载必须带 `uid=1000,gid=1000,mode=0755`（容器内 user=1000 才能写）

流式消费：`container.logs(stream=True, follow=True)` + `LineBufferedJsonParser`（跨字节 chunk 半行处理）。

## 5. Agent 执行（`orchestrator` + `ai_runner`）

模式化子图由 `orchestrator.py` 按 `pipeline_for` 驱动（discovery=`DEFAULT_PIPELINE` 15 键；verify=`VERIFY_PIPELINE` 7 键；`finalize` 固化终态，`report` 后处理）；AI 节点经 `ai_runner.py` 拉起 agent-runner 容器。LLM 凭据由 `sdk_adapter.build_runner_env()` 注入 `ANTHROPIC_*`（容器销毁即消失）。

事件流：容器内 `runner/run_one.py` 把 SDK Message 翻译为统一事件（`phase.updated` / `agent.thinking` / `agent.message` / `tool.call.*` / `agent.completed` / `agent.failed`），stdout JSONL 推给 worker。`agent.failed` 带 `title`/`hint`；编排失败文案见 `contexts/agent/errors.py`。

## 6. Settings / LLM Provider（`settings` context）

- API Key **Fernet 入库**(`seal_secret` 写入 `api_key_encrypted`),列表接口走 `mask_secret` 掩码。存量明文由 `reveal_secret` 兼容
- 同一时刻**仅一个** Provider 的 `is_default=true`（Agent 只读默认项）。不设独立 `enabled`；列表状态由 `is_default` 派生（默认 / 备用），启用走 `POST /llm/providers/{id}/activate`
- 测试连接真实打 Provider 的 `base_url`（不 Mock），便于配置阶段就发现端点 / 凭据错误

## 7. 报告与证据（`report` context）

- 报告与 Task **1:1**；同一 Task 多次重跑 → 历史版本而非覆盖
- 证据（截图、日志）上传 MinIO（`shared/object_store.py`），落库 `evidences` 表
- 报告状态机：`pending → generating → completed | failed`，禁止跳过中间态

## 8. 平台配置分层（禁止同一事实多处抄）

| 入口 | 写什么 |
|---|---|
| `backend/pyproject.toml` `[project].version` | **唯一**产品版本。`Settings.app_version` 只读这里。前端 `package.json` 不写 version |
| `backend/.env`（模板 `.env.example`） | **唯一**运行时连接与密钥：`DATABASE_URL`（PostgreSQL）/ `REDIS_*` / `S3_ENDPOINT`+KEYS / `AUTH_SECRET` / SDK 开关 |
| `tests/conftest.py` | **pytest 进程**把 `DATABASE_URL` 覆盖为 sqlite；不改 `.env`，不碰真实库 |
| `app/core/config.py` | 类型、校验、行为默认（environment / debug / runner 限额）。**连接串无代码默认值** |
| `shared/object_store.py` | MinIO 物理桶与 kind；禁止 `s3_bucket` 进 Settings |
| 后台 Settings Context | LLM Provider（禁止 `LLM_*` 环境变量） |
| `alembic/env.py` | 从 Settings 注入库地址；`alembic.ini` 不保存真实 URL |

改运行时库地址只改 `.env`。pytest 不要改 `.env`，覆盖点只有 `tests/conftest.py`。