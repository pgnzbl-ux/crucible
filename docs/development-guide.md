# Crucible 开发指导文档

> 文档版本: v1.1 · 2026-08-10
> 定位: Crucible 架构决策与开发路线 —— 从 v2.0 分层架构痛点复盘到 v3.0 六原则六决策落地
> 阅读对象: 任何接手本项目的开发者
>
> **Agent 阶段化编排是核心方向，完整设计见 `docs/agent-workflow.md`**（11 节点 S0-S10 + 3 分支出口 + 回退环 + 浏览器降级策略）

---

## 1. 主线回顾：我们要干什么

### 1.1 起点：上一代 v2.0 的八大结构性痛点

上一代系统（v2.0）已具备分层架构（Route→Schema→Service→Repository）与企业基础能力（RBAC、审计、Prometheus），但存在八个结构性瓶颈：

| # | 痛点 | 核心问题 |
|---|------|---------|
| 1 | Agent 执行是"胶水层" | 直接 subprocess 调 Claude Code CLI，`bypassPermissions` 跑在宿主机权限，无抽象、无沙箱生命周期 |
| 2 | 数据层是"类 ORM 胶水" | 手写 SQL + dict，同步 SQLite 阻塞事件循环，无类型化对象 |
| 3 | 前端状态碎片化 | 只有 1 个 Zustand store，SSE 生命周期每组件自己实现 |
| 4 | API 无版本契约 | 状态变更用 POST、无 OpenAPI→TS 类型生成、响应格式不统一 |
| 5 | 前端目录无分层 | 组件全平铺在 components/ 下 |
| 6 | 安全停在开发阶段 | 无 OIDC、无 API Key、无凭据代理 |
| 7 | 部署无自动化 | 主应用传统部署、无 CI/CD |
| 8 | 报告无审阅闭环 | 报告是单条记录，无版本、无导出、证据塞数据库 |

### 1.2 目标：v3.0 架构（六原则 + 六决策）

| 原则 | 内容 |
|------|------|
| P1 | **Modular Monolith First** — Bounded Context 组织代码，未来可拆 |
| P2 | **Agent as a Platform** — Agent 执行从胶水升级为平台能力 |
| P3 | **Event-Driven Core** — 跨 Context 走事件总线 |
| P4 | **Async Everywhere** — DB→消息→HTTP→前端流全异步 |
| P5 | **Security by Default** — 凭据不落盘、沙箱真隔离、Agent 零信任 |
| P6 | **Observability Built-in** — 追踪、结构化日志、指标 |

| ID | 决策 | 理由 |
|----|------|------|
| D1 | 模块化单体（非微服务） | 团队规模 1-3 人、单库事务、Docker Compose 足够 |
| D2 | SQLAlchemy 2.0 Async | 类型安全、生态成熟、与 Pydantic v2 配合 |
| D3 | Redis Pub/Sub 事件总线 | 已部署 Redis，零额外基础设施 |
| D4 | SSE 优先于 WebSocket | 单向流足够，SSE 自动重连更简单 |
| D5 | Docker SDK（非 K8s）做沙箱 | 开发阶段 Docker 足够 |
| D6 | OIDC 增量叠加（不替换 JWT） | 不破坏现有用户 |

### 1.3 落地载体：Crucible 新项目

重构以**新项目**（`D:\codeproject\Crucible\`）落地，不复用上一代代码库 —— 避免迁移期双写与兼容负担，用干净代码库直接按 v3.0 架构搭建。

---

## 2. 架构几何（当前现状）

### 2.1 后端结构

```
backend/app/
├── main.py                    # FastAPI 入口（<100 行，挂 4 个 Context 路由 + 健康检查 + Prometheus）
├── core/
│   ├── config.py              # pydantic-settings，全环境变量注入
│   ├── database.py            # SQLAlchemy 2.0 Async（SQLite 开发 / PostgreSQL 生产）
│   ├── security.py            # JWT + bcrypt（bcrypt 锁 4.0.1 兼容 passlib）
│   ├── celery_app.py          # Celery + autodiscover agent 任务 + prefetch=1 + acks_late
│   ├── agent_runner.py        # ★ Agent Runner 编排（Docker SDK + 流式消费，替代 sandbox）
│   └── crypto.py              # Fernet 加密（LLM API Key 落库加密）
├── contexts/                  # Bounded Contexts
│   ├── identity/              # 用户注册/登录/JWT
│   ├── task/                  # 任务 CRUD + TaskRun + AgentEvent + 事件流查询
│   ├── agent/                 # ★ Agent 执行平台（Claude Agent SDK + agent-runner 容器）
│   │   ├── sdk_adapter.py     #   Claude Agent SDK 适配器（env + prompt 构造）
│   │   ├── runner_bridge.py   #   容器编排 + 写 prompt + 流消费组合
│   │   ├── executor.py        #   ClaudeSdkExecutor / MockExecutor + 工厂
│   │   └── tasks.py           #   Celery 工作流（host clone → 容器 → 实时落库）
│   ├── report/                # 报告生成 + 状态机 + MinIO 归档
│   │   └── storage.py         #   MinIO (S3) 存储封装
│   └── settings/              # ★ LLM Provider 后台配置（多 Provider + 加密 + 测试连接）
│       └── seed.py            #   环境变量 → DB 种子迁移（幂等）
└── shared/
    ├── base.py                # BaseModel（UUID + 时间戳）
    ├── events.py              # Event + EventBus（Redis Pub/Sub 标准事件结构）
    └── sse.py                 # ★ SSE 事件流（StreamingResponse + 历史回放 + 15s 心跳 + 断开清理）
```

### 2.2 前端结构

```
frontend/src/
├── app/                       # providers.tsx + layout.tsx（AppLayout + 侧边栏）
├── pages/                     # DashboardPage / TasksPage / ReportsPage / SettingsPage / LoginPage
├── features/                  # ★ 空壳：task/ agent/ auth/ report/（提案要求，尚未填充）
├── shared/lib/                # api.ts（类型化 API 客户端）+ meta.ts（状态/优先级/结论映射）
├── shared/hooks/              # ★ useTaskEvents.ts（SSE 实时事件流 hook）
└── styles/
```

### 2.3 基础设施

```
infrastructure/
├── docker-compose.yml         # postgres(5433) + redis(6380) + minio(9000/9001) + createbuckets
└── agent-runner/              # Agent Runner 镜像（专用，Claude Agent SDK 隔离执行环境）
    ├── Dockerfile             # python:3.11-slim + claude-agent-sdk + git + curl + tini
    ├── requirements.txt       # claude-agent-sdk==0.2.134
    └── runner/run_one.py      # 容器内 entrypoint（JSONL 流输出）
```

### 2.4 数据模型

```
users ──1:N── tasks ──1:N── task_runs ──1:N── agent_events
                    │
                    └──1:1── reports ──1:N── evidences
                    
llm_providers（独立，后台管理）
```

### 2.5 运行时两种模式

| 模式 | 沙箱运行时 | 用途 |
|------|-----------|------|
| host（默认） | AgentRunnerManager 直连宿主 docker.sock | Windows 开发机 |
| dind（生产） | 每任务嵌套 DinD + mTLS（2376） | 全 Docker 化部署，详见 README.md |

---

## 3. 干了什么（已完成清单）

### 3.1 提案章节对照

| 提案要求 | 状态 | 落点 |
|---------|------|------|
| Bounded Context 模块化 | ✅ | contexts/{task,agent,report,identity,settings} |
| SQLAlchemy 2.0 Async | ✅ | database.py + Alembic + 全异步 |
| Repository 现代化 | ✅ | 每 Context repository.py（类型化 + select） |
| Agent Adapter 抽象 | ✅ | executor.py（Protocol + 双实现 + 工厂） |
| Agent Runner 编排 | ✅ | core/agent_runner.py（容器编排 + 流式消费 + 行缓冲 + 凭据零落盘） |
| Event Bus（Redis Pub/Sub） | ✅ | shared/events.py（统一事件结构） |
| 事件持久化 + 查询 | ✅ | agent_events 表 + GET /tasks/{id}/events |
| **SSE 实时事件推送（P0-1）** | ✅ | shared/sse.py（StreamingResponse + 历史回放 + 15s 心跳 + 断开清理）+ GET /tasks/{id}/events/stream + 前端 useTaskEvents hook |
| 报告生成 + MinIO 归档 | ✅ | report/service.py + storage.py |
| LLM 后台配置（新增） | ✅ | settings context（Fernet 加密 + 测试连接 + 种子迁移） |
| DeepSeek 对接（新增） | ✅ | ClaudeCodeAdapter.build_env（8 变量注入，零落盘） |
| 前端 pages/ shared/ 分层 | ✅ | AppLayout + 4 页面 |
| 事件流展示（Timeline） | ✅ | TasksPage 详情 Drawer（SSE 实时 + 连接状态指示） |
| Prometheus 指标 | ✅ | main.py Instrumentator |

### 3.2 已通过的验证

- 全链路（Mock 模式）：POST /tasks → Celery → 沙箱 clone → Agent → 事件持久化 → 报告生成 → MinIO 归档 ✅
- SSE 实时推送：agent/tasks.py 落库后 → Redis Pub/Sub → shared/sse.py 转发 → 前端 useTaskEvents hook（历史回放 + 15s 心跳 + 断开清理）✅
- 沙箱安全：OOM 限制、网络隔离、非 root、只读 rootfs、凭据零落盘（grep 0 命中）✅
- LLM 后台：CRUD / 掩码 / Fernet 密文落库 / 激活唯一性 / 测试连接真实打 DeepSeek 端点 ✅
- 种子迁移幂等性 ✅

---

## 4. 还有什么要干（差距清单 + 开发路线）

> 按优先级排序。P0 = 当前功能闭环所必需；P1 = 提案明确要求；P2 = 生产/体验增强。

### P0 — 功能闭环

> ⭐ **核心方向：Agent 阶段化编排**（从"单次注入黑盒"到"11 节点阶段流水线"）。
> 这是后续所有 Agent 相关工作的骨架，完整节点定义/契约/跳转见 `docs/agent-workflow.md`。

| # | 任务 | 现状 | 目标 | 涉及 |
|---|------|------|------|------|
| 0 | **Agent 阶段化编排**（Stage Orchestrator） | 单次 claude 调用跑全流程（黑盒） | S0-S10 逐阶段编排 + 结构化契约 + 分支跳转（非web结束/误报Quick-Stop/回退环） | 新增 agent/stages.py + preflight.py + prompts.py，重构 executor.py / tasks.py |
| 1 | **SSE 实时事件推送** ✅ | `GET /api/v1/tasks/{id}/events/stream` 已就绪，前端 `useTaskEvents` 替换 3s 轮询 | （已完成） | shared/sse.py + task/api.py + frontend shared/hooks/useTaskEvents.ts |
| 2 | **取消任务真正生效** | cancel 仅改 DB 状态 | `celery_app.control.revoke(task_id)` + 沙箱销毁 + run 标记 cancelled | task/service.py + agent/tasks.py |
| 3 | **JWT 登录闭环** | api.ts 有 token 逻辑，LoginPage 未联调 | 登录后存 token，路由守卫，owner_id 从 token 解析（当前硬编码 "system"） | frontend pages/LoginPage + api.ts；task/report api.py 的 owner 注入 |
| 4 | **Evidence 上传链路** | storage.py 有 upload_evidence 但 service 未接 | 报告生成时证据（日志/截图）上传 MinIO + evidences 表落库 + 前端展示 | report/service.py + api.py |

> **P0 顺序**：0（阶段化编排骨架）→ ~~1（SSE）~~ ✅ → 2（cancel revoke）→ 3（登录）→ 4（证据）。
> 阶段化编排是前提 —— SSE 推送的正是阶段事件。

### P1 — 提案明确要求

| # | 任务 | 说明 | 涉及 |
|---|------|------|------|
| 5 | **frontend features/ 填充** | task/agent/auth/report 四个领域模块：store.ts + hooks.ts（useSSE、useTaskEvents） | frontend/features/* |
| 6 | **Credential Proxy** | 任务级凭据引用（credential_refs）→ Secret 注入沙箱（环境变量/临时文件 600）→ 任务结束销毁 | agent/executor.py + 新 core/credential_proxy.py |
| 7 | **API Key + Service Account** | 平台级 API 认证（用于 CI/脚本调用） | identity context |
| 8 | **OIDC SSO（增量叠加）** | 保持 JWT，新增 OIDC 认证方式（Keycloak） | identity context |
| 9 | **RBAC 迁移** | 上一代有 4 角色 8 权限表，Crucible 尚未引入 | identity context + 路由守卫 |
| 10 | **报告版本历史 + 导出** | Report 增加版本记录；PDF/DOCX 导出（python-docx，报告模块已具备 md→docx 转换） | report context + frontend |
| 11 | **审计日志 Context** | 平台操作审计（谁在何时做了什么） | 新 contexts/audit + event bus 订阅 |

### P2 — 生产 / 体验增强

| # | 任务 | 说明 |
|---|------|------|
| 12 | **OpenAPI → TS 类型生成** | 后端导出 OpenAPI schema，前端自动生成 types.ts（当前手动维护 api.ts 类型） |
| 13 | **OpenTelemetry + Sentry** | config 已有 sentry_dsn 字段，接入初始化；OTel 追踪 |
| 14 | **CI/CD（GitHub Actions）** | 后端 pytest + ruff、前端 tsc + build、构建镜像 |
| 15 | **生产部署实现（嵌套 DinD + mTLS）** | README 已设计完整，落地 `DindManager` + `core/docker_tls.py`（CA 签发），`SANDBOX_RUNTIME=dind` |
| 16 | **PostgreSQL 生产验证** | asyncpg 连接 + Alembic 迁移在 PG 上跑通 |
| 17 | **前端 TaskDetailPage / ReportPage 独立路由** | 当前详情是 Drawer，长内容体验一般 |

### 4.1 建议实施顺序

```
第一轮（P0，功能闭环）:  0（阶段化编排骨架）→ 1（SSE ✅）→ 2（cancel）→ 3（登录）→ 4（证据）
第二轮（P1，提案对齐）:  5 → 6 → 7 → 8 → 9 → 10 → 11
第三轮（P2，生产就绪）:  12 → 13 → 14 → 15 → 16 → 17
```

每个 P0 完成后跑一遍全链路冒烟（任务创建 → 事件流 → 报告）作为回归门禁。

---

## 5. 开发规范（硬性约定）

### 5.1 Context 边界

- **禁止**跨 Context 直接访问 Repository；跨 Context 数据传递用 Service 接口（DI）或事件
- **禁止**跨 Context 建 ORM relationship（SQLAlchemy mapper 报错）；只留 ForeignKey + ID，需要时手动查
- 每个 Context 目录结构固定：`api.py / models.py / schemas.py / service.py / repository.py`（+ `events.py` / `tasks.py` 按需）
- 新表必须在其所属 Context 的 models.py 中定义；Celery worker 侧 import 该 models 以注册 metadata（否则 FK 解析失败）

### 5.2 事件驱动

- 跨 Context 异步通知走 `shared/events.py` 的 `Event`（统一结构：event_id / event_type / aggregate / payload / correlation_id）
- 事件频道规则：`domain.{aggregate_type}.{event_type}`（`publish_domain_event`）
- 事件总线失败**不允许**影响主流程（try/except 包裹）

### 5.3 安全红线

- **凭据零落盘**：LLM key / 证书只走环境变量或运行时卷注入，绝不写镜像层或容器文件系统
- agent-runner 容器必须：非 root + 只读 rootfs + cap_drop ALL + 资源限制 + 超时回收（`AgentRunnerSpec` 默认值）
- 凭据零落盘：`ANTHROPIC_*` 等 8 个变量通过 `docker run --env` 注入 agent-runner 容器，容器销毁 env 消失
- API Key 落库必须 Fernet 加密（`core/crypto.py`），列表接口只回显掩码
- 生产环境（`ENVIRONMENT=production`）强制校验：必须 AUTH_SECRET + 禁止 SQLite（config validator）

### 5.4 踩坑记录（务必先读）

| 坑 | 结论 |
|----|------|
| bcrypt 5.0 与 passlib 不兼容 | 锁 `bcrypt==4.0.1` |
| Docker SDK 7.x 移除 exec_run timeout | 用容器内 `timeout N` 命令包裹 |
| Docker SDK `containers.create` 不支持 stop_grace_period | 去掉该参数 |
| 容器 user=1000 写不了 tmpfs | tmpfs 挂载加 `uid=1000,gid=1000,mode=0755` |
| Celery worker 复用 async engine 报 "attached to a different loop" | worker 用独立 NullPool engine（`tasks.py::_worker_engine`） |
| 沙箱 internal 网络断外联导致 git clone 失败 | 默认 `sandbox_network_internal=False`（可外联），专用网络只隔离平台 |
| agent-runner 网络断外联导致 git clone 失败 | 默认 `agent_runner_network=crucible-sandbox-net`（可外联）；如需断外联设 `internal=True` |
| agent-runner 容器进程被强 kill 留下孤儿 | 双重保险：①Celery SIGTERM 钩子（`tasks.py::_on_sigterm`）主动 docker kill；②`agent_runner_manager.cleanup_stale()` 定期巡检 |
| container.logs(stream=True) 按字节 chunk 切分破坏 JSONL 行边界 | `LineBufferedJsonParser` 按 `\n` 累积字节再 json.loads；EOF 时调 `flush()` 处理最后一行 |
| 宿主机端口冲突（5432/6379 已被占用） | Crucible 用 5433/6380 |
| Windows 下 Celery 用 solo pool | `run_worker.py` 已固定 `--pool=solo` |
| **chromium headless 在 read_only rootfs 下崩溃** | 需额外挂 `/tmp` + `/dev/shm` tmpfs + 容器 env `HOME=/workspace`（crashpad / ProcessSingleton 依赖可写 HOME），且 `/workspace` 不能挂 `noexec`（chromium + 搭靶场二进制都需可执行） |
| **nginx/反代默认缓冲 SSE** | 响应头加 `X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`（sse.py 端点已加） |
| **Redis Pub/Sub 离线即丢消息** | SSE 连接先回放 DB 历史（`_replay_history`）再订阅 Pub/Sub，保证"刚发生的事件"不丢 |
| **EventSource 无法注入 Authorization header** | token 走 query 参数 `?token=xxx`（前端 useTaskEvents 已实现；后端鉴权待 P0-3 接入） |
| **SSE 客户端断开不清理 → Redis 连接泄漏** | `stream_task_events` finally 块强制 `unsubscribe + pubsub.close + r.close`；并发规范见 concurrency.md §5 |

---

## 6. 快速参考

### 6.1 启动（开发模式）

```bash
cd infrastructure && docker compose up -d          # 基础设施
docker build -f agent-runner/Dockerfile -t crucible-agent-runner:base .   # 首次构建 agent-runner 镜像
cd backend && python -m uvicorn app.main:app --port 8010        # API
cd backend && python run_worker.py                              # Celery worker
cd frontend && npm run dev                                      # 前端 5173
```

### 6.2 测试

```bash
cd backend
python tests/smoke_agent_runner.py    # agent-runner 冒烟（基础/OOM/取消/行缓冲/清理）
python tests/smoke_sse.py              # SSE 实时推送冒烟（帧格式/历史回放/断开检测/心跳）
pytest                           # 单元测试（待补，见 backlog P1）
```

### 6.3 关键配置（backend/.env）

| 变量 | 说明 |
|------|------|
| `SANDBOX_RUNTIME` | `host`（开发）\| `dind`（生产嵌套，未实现） |
| `CLAUDE_CODE_ENABLED` | true 启用真实 Agent（否则 Mock） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | DeepSeek 等第三方 LLM |
| `SETTINGS_ENCRYPT_KEY` | Fernet 密钥（生产必配，开发从 AUTH_SECRET 派生） |

---

> **下一步**：P0-1（SSE）已完成。按 §4.1 顺序继续 **P0-0（Agent 阶段化编排 S0-S10 骨架）** 或 **P0-2（取消任务真正生效）**。每完成一项更新本文件的「已完成清单」并跑全链路冒烟。
