---
paths: ["backend/app/contexts/**/*.py", "backend/app/shared/**/*.py", "backend/app/core/**/*.py"]
---

# Crucible 后端架构规范

> 主线对照与路线见 `docs/development-guide.md` §1-2；本文件只写**项目特有**架构约束。

## 1. Bounded Context 五件套

每个 Context 目录固定包含：`api.py / models.py / schemas.py / service.py / repository.py`。可选：`events.py`（领域事件发布器）、`tasks.py`（Celery 工作流）。

- **跨 Context 数据访问走 Service 接口或事件**，禁止直接 import 对方 repository
- **禁止跨 Context 建 ORM relationship**（mapper 报错），只保留 `ForeignKey` + 整数/UUID ID，需要时手动查
- 新表必须在其所属 Context 的 `models.py` 中定义；Celery worker 启动时 import 该 models 以注册 metadata（否则 FK 解析失败）。部署用唯一 Alembic 基线 `c18a0e9b4d21`（`metadata.create_all`），与 `init_db()` 同源

## 2. 六个 Context 的职责边界

| Context | 核心模型 | 关键职责 |
|---|---|---|
| `identity` | users | 注册、登录、JWT、bcrypt（**锁 bcrypt==4.0.1**） |
| `task` | tasks / task_runs / node_runs / agent_events | 任务 CRUD、状态机(含 retry/delete/archived)、6 节点断点续跑、事件查询 |
| `agent` | （无自有表，消费 settings 与 task） | Agent 执行器抽象、Celery 工作流、沙箱编排 |
| `project` | projects / source_artifacts | 项目元数据 + 按 SHA 的画像缓存；源码 tar.gz 按 owner+host 隔离缓存在 MinIO `crucible-source`（agent 只调 `acquire_source()` / ProjectService） |
| `report` | reports / evidences | 报告生成 + 状态机 + MinIO 归档 |
| `settings` | llm_providers / credentials | LLM Provider + 凭据后台 CRUD（**明文存取 + 响应掩码 + 激活唯一性**） |

新增 Context 时单独评审，避免膨胀为"通用业务包"。

## 3. 异步链路：async/await 全栈贯通

- DB → 消息（Redis）→ HTTP（FastAPI）→ 前端流（SSE），任一环节混同步都破坏吞吐
- SQLAlchemy 2.0 **必须**用 `AsyncSession` + `select()` 形式，禁止 `session.execute(query)` 旧式
- Celery worker **不复用**主进程的 async engine —— 用独立 NullPool engine（参考 `agent/tasks.py::_worker_engine`）

## 4. Agent Runner 抽象（`core/agent_runner.py`）

`AgentRunnerManager` 是**平台能力**，不是业务代码能跳过的层。

`AgentRunnerSpec` 默认值即安全基线：

- 非 root + 只读 rootfs + `cap_drop: ["ALL"]`
- 资源限制（内存/CPU/PIDs）
- 超时回收
- tmpfs 挂载必须带 `uid=1000,gid=1000,mode=0755`（容器内 user=1000 才能写）

流式消费：`container.logs(stream=True, follow=True)` + `LineBufferedJsonParser`（跨字节 chunk 半行处理）。

## 5. Agent 执行器协议（`agent/executor.py`）

`Executor` 是 Protocol，两个实现：

- `ClaudeSdkExecutor` —— 拉起 agent-runner 容器（Claude Agent SDK），**通过 `build_runner_env()` 注入 8 个 ANTHROPIC_* 环境变量实现凭据零落盘**
- `MockExecutor` —— 单测 / 开发默认

新增 Agent 实现必须注册到工厂；调用方不 `import` 具体类。

事件流：`agent-runner` 容器内 `runner/run_one.py` 把 SDK Message 翻译为统一事件结构（`phase.updated` / `agent.thinking` / `agent.message` / `tool.call.*` / `agent.completed` / `agent.failed`），通过 stdout JSONL 推给 worker。`agent.failed` 带 `title`/`hint`；编排失败文案见 `contexts/agent/errors.py`。

## 6. Settings / LLM Provider（`settings` context）

- API Key 当前**明文入库**(`settings/service.py` 存 `api_key_encrypted`),列表接口走 `mask_secret` 掩码。`core/crypto.py::encrypt_secret` 遗留未用
- 同一时刻**仅一个** Provider 的 `is_default=true`（Agent 只读默认项）。不设独立 `enabled`；列表状态由 `is_default` 派生（默认 / 备用），启用走 `POST /llm/providers/{id}/activate`
- 测试连接真实打 Provider 的 `base_url`（不 Mock），便于配置阶段就发现端点 / 凭据错误

## 7. 报告与证据（`report` context）

- 报告与 Task **1:1**；同一 Task 多次重跑 → 历史版本而非覆盖
- 证据（截图、日志）上传 MinIO (`core/storage.py`)，落库 `evidences` 表
- 报告状态机：`pending → generating → completed | failed`，禁止跳过中间态