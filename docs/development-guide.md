# Crucible 开发指导文档

> 文档版本: v1.1 · 2026-08-10
> 定位: Crucible 架构决策与开发路线 —— 从 v2.0 分层架构痛点复盘到 v3.0 六原则六决策落地
> 阅读对象: 任何接手本项目的开发者
>
> **平台 6 节点编排是核心方向，完整设计见 `docs/agent-workflow.md`**（6 节点 source/profile/env_ready/audit/reproduce/report + 4 分支出口：非 web skip、audit gate_fail 判误报、env 5 轮失败、audit uncertain 待复核）

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
│   ├── config.py              # Settings 类型与校验；连接串无默认，从 .env 读
│   ├── database.py            # SQLAlchemy 2.0 Async（开发/生产均为 PostgreSQL）
│   ├── security.py            # JWT + bcrypt（bcrypt 锁 4.0.1 兼容 passlib）
│   ├── celery_app.py          # Celery + autodiscover agent 任务 + prefetch=1 + acks_late + visibility_timeout
│   ├── agent_runner.py        # ★ Agent Runner 编排（Docker SDK + 流式消费，替代 sandbox）
│   └── crypto.py              # Fernet 加密工具(遗留,settings 已不调用,明文存取)
├── contexts/                  # Bounded Contexts
│   ├── identity/              # 用户注册/登录/JWT
│   ├── task/                  # 任务 CRUD + TaskRun + AgentEvent + 事件流查询
│   ├── agent/                 # ★ Agent 执行平台（6 节点编排 + Claude Agent SDK + agent-runner 容器）
│   │   ├── orchestrator.py   #   ★ 6 节点编排器（循环 + 分支出口 + 断点续跑）
│   │   ├── nodes/            #   ★ 6 节点实现（source/profile/env_ready/audit/reproduce/report）
│   │   ├── ai_runner.py      #   AI 节点容器编排 + submit_result 工具 + schema 校验
│   │   ├── profile_detector.py #  节点 1 规则引擎（强 Web/非 Web 走规则，其余才 AI）
│   │   ├── sdk_adapter.py     #   Claude Agent SDK 适配器（env + prompt 构造）
│   │   └── tasks.py           #   Celery 工作流（host clone → 调 orchestrator → 实时落库）
│   ├── report/                # 报告生成 + 状态机 + MinIO 归档
│   │   └── service.py         #   报告 + 证据（对象走 shared/object_store）
│   └── settings/              # ★ LLM Provider 后台配置（多 Provider + 明文存取 + 测试连接）
└── shared/
    ├── base.py                # BaseModel（UUID + 时间戳）
    ├── events.py              # Event + EventBus（Redis Pub/Sub 标准事件结构）
    ├── object_store.py        # ★ MinIO 唯一客户端（3 桶 + kind 注册表）
    └── sse.py                 # ★ SSE 事件流（StreamingResponse + 历史回放 + 15s 心跳 + 断开清理）
```

### 2.2 前端结构

```
frontend/src/
├── app/                       # providers.tsx + layout.tsx（AppLayout + 侧边栏）
├── pages/                     # DashboardPage / TasksPage / ReportsPage / SettingsPage / LoginPage
├── features/                  # task/components（TaskTable/TaskFilterBar/TaskCreateDrawer）等
├── shared/lib/                # api.ts（类型化 API 客户端）+ meta.ts（状态/优先级/结论映射）
├── shared/hooks/              # ★ useTaskEvents.ts（SSE 实时事件流 hook）
└── styles/
```

### 2.3 基础设施

```
infrastructure/
├── docker-compose.yml         # postgres(5433) + redis(6380) + minio(9000/9001) + createbuckets
└── agent-runner/              # Agent Runner 镜像（Python + Node + 完整 Linux 命令，Claude Agent SDK 隔离执行）
    ├── Dockerfile             # build context=项目根；runner 复制到 /app/runner（勿放 /workspace，会被 host bind-mount 盖掉）
    ├── requirements.txt       # claude-agent-sdk==0.2.134
    └── runner/run_one.py      # 容器内 entrypoint（蒸馏 skill → system_prompt + JSONL）
    └── node-skills/           # 每 AI 节点一份 SKILL.md（不加载桌面插件）

plugins/                       # 桌面 Claude Code 插件（母本 / 指导材料，不进 runner 镜像）
└── vuln-verify-expert/        #   总 agent + 2 skills；平台蒸馏见 agent-runner/node-skills
```

### 2.4 数据模型

```
users ──1:N── projects ──1:N── tasks ──1:N── task_runs ──1:N── agent_events
                                │              │
                                │              └──1:N── node_runs(6 节点断点续跑)
                                └──1:1── reports ──1:N── evidences

llm_providers(独立,后台管理) / credentials(凭据,明文存)
Task 加 project_id(FK→projects) + verdict(6 档);Report 加 report_data 等结构化字段
```

### 2.5 部署形态（2026-08-19 定稿：路线 1 — 后端宿主机进程部署）

**架构事实**：后端（API + Celery worker）直接编排宿主 Docker daemon——`docker.from_env()`（agent_runner.py）创建 agent-runner 沙箱、`subprocess docker compose`（lab/docker_ops.py）调度靶场、任务工作区靠「宿主路径 bind mount」进沙箱。三者共享**同一文件系统视图**这一前提。

**因此后端不能容器化**（含挂 docker.sock 的 sidecar 形式）：一旦后端进容器，`host_workdir` 解析为容器内路径，宿主 daemon 视角找不到 → 每个任务拉起沙箱即失败；挂 socket 本身也违背 compose_policy 禁止 socket 挂载的安全模型。

| 组件 | 部署方式 |
|------|---------|
| PostgreSQL / Redis / MinIO | 容器（`infrastructure/docker-compose.yml`） |
| **后端 API + Celery worker** | **宿主机进程（systemd 托管）** |
| agent-runner 沙箱 | 宿主 daemon 按需创建/销毁（后端调度） |
| Lab 靶场 compose | 宿主 daemon 按需创建/销毁（后端调度） |
| 前端 | 静态构建产物，Nginx 托管 |

部署模板：`infrastructure/deploy/`（systemd unit + Nginx + 操作手册）。

> 历史注：曾规划「嵌套 DinD + mTLS」的全容器化方案（每任务独立 dockerd），已放弃——复杂度高且与 bind-mount 语义天然冲突。未来多机扩展走「runner-node agent 服务化」（每机轻量 daemon，后端 HTTP 派活），另立项。

---

## 3. 干了什么（已完成清单）

### 3.1 提案章节对照

| 提案要求 | 状态 | 落点 |
|---------|------|------|
| Bounded Context 模块化 | ✅ | contexts/{task,agent,report,identity,settings} |
| SQLAlchemy 2.0 Async | ✅ | database.py + 唯一 Alembic 基线（ORM create_all）+ 全异步 |
| Repository 现代化 | ✅ | 每 Context repository.py（类型化 + select） |
| Agent Adapter 抽象 | ✅ | sdk_adapter.py + ai_runner.py（env 注入 + 容器编排） |
| Agent Runner 编排 | ✅ | core/agent_runner.py（容器编排 + 流式消费 + 行缓冲 + 凭据零落盘） |
| **平台 6 节点编排** | ✅ | orchestrator 驱动 6 节点 + 蒸馏 skill 注入 runner；audit uncertain→跳 reproduce、report 产 needs_review 验证记录 |
| Event Bus（Redis Pub/Sub） | ✅ | shared/events.py（统一事件结构） |
| 事件持久化 + 查询 | ✅ | agent_events 表 + GET /tasks/{id}/events |
| **SSE 实时事件推送（P0-1）** | ✅ | shared/sse.py（StreamingResponse + 历史回放 + 15s 心跳 + 断开清理）+ GET /tasks/{id}/events/stream + 前端 useTaskEvents hook |
| 报告生成 + MinIO 归档 | ✅ | report/service.py + shared/object_store.py（3 桶 durable/task/public） |
| **源码 MinIO 缓存** | ✅ | `source_artifacts` 按 owner+host+project+ref；branch/HEAD 必须 ls-remote 对上 SHA 才用 MinIO；画像 `profile_json` 绑同一 SHA，SHA 变则清空重检 |
| **靶场配方 MinIO 复用** | ✅ | `lab/recipe_store` 按 owner+project+SHA 存 `.vuln-env/`；env_ready 创建者先 download 短路（命中改宿主口 + 一次 up），未命中/失败再 AI；成功 upload；rebuild 可从 MinIO 拉回 |
| **靶场 compose 就地执行** | ✅ | 2026-08-18：`.vuln-env` 留在 `{host_workdir}/{repo}/` 就地 up（`-p crucible-lab-{id}` 隔离），删 lab 文件暂存层；`validate_compose_file` 边界=任务 host_workdir；rebuild=clone 源码+拉配方+up --build。修复多模块 build.context 结构性失败（见 troubleshooting/2026-08-18 诊断根因 A） |
| **MCP 工具 schema 收敛** | ✅ | 2026-08-19：submit_result 工具 schema 顶层只允许 type/properties/required/description，禁 JSON Schema 组合器（allOf/if/then 等）——第三方网关遇顶层组合器静默丢工具，audit 节点曾因此 no_submit；条件形状由后端 validate_output + SKILL.md 表达（见 troubleshooting/2026-08-18 诊断 §9 根因 C） |
| **Agent 潜问题修复第一批（止血+安全）** | ✅ | 2026-08-19：(4) Celery `task_time_limit` 从 run 硬顶推导（`agent_run_hard_timeout_seconds+300`），不再绑单容器 1800s 全链必 SIGKILL；(9a) 失败保留 host_workdir 前删 `.secrets/`；(9b) compose_policy 拒绝 `.secrets` bind mount（AI 一行 volumes 不能把凭据挂进靶场）；(7) 缓存配方命中路径 `_upload_then_mark_ready` 补传 repo；(8) AgentRunnerManager 懒连接 Docker（daemon 不可达不再炸 import）；(3) screenshots `/workspace/` 容器前缀映射回宿主 workdir；(2) env_ready 走 `validate=False`——排障环自带逐项校验+回喂，平台层先斩后奏曾使回喂分支永不可达 |
| **AI 节点形状回喂环（P0#1）** | ✅ | 2026-08-19：`run_ai_node_with_shape_retry`（ai_runner）——audit/reproduce/report 的 output 形状校验失败不再一次判死，把 `previous_error`+`previous_submit_summary` 拼进 input_json 重跑（上限 2 轮重试）；执行层失败（no_submit/SIGKILL/超时）仍立即抛；每轮失败留 `.node-failure` 快照；三个 SKILL.md 增补回喂条款（判定结论不变只修形状） |
| **容器执行边界加固（P0#5/6）** | ✅ | 2026-08-19：`run_with_streaming` 超时路径三处止血——(a) 超时 stop 失败降级 kill、双双失败写 `summary.stop_failed`（曾 `except:pass` 静默吞 → 容器不停、流不 EOF、worker 无限挂死无线索）；(b) `wait()` 带兜底宽限（超时场景 timeout=30s，ReadTimeout 按 137 收尾，不再无界阻塞）；(c) 超时瞬间 agent 已正常退出（exit 0）保留真实退出码不丢产物（边界竞态）；docker 客户端 read timeout 对齐 `agent_runner_timeout_seconds+120s`（默认 60s 会在 agent 静默思考 >60s 时腰斩 follow 流） |
| **env_ready 中危修复批次** | ✅ | 2026-08-19：(1) 探活/publish 支持 https——容器口 443/8443/9443 推断 scheme，`health_check` 返回 (ok, port, scheme)，`publish_target_url(scheme=)` 透传（HTTPS 入口靶场不再 5 轮全死；自签证书放宽校验）；(2) reuse/start 复用前快探 `_reused_lab_alive`（DB ready ≠ 应用活着），死靶场 mark_failed→reclaim→缓存配方重建，不烧 AI；(3) 缓存路径凭据补查失败先 `docker_compose_down` 再抛（防 DB=failed/容器在跑的端口泄漏）；(4) `upload_recipe` 返回 bool，MinIO 故障发警告事件不再静默吞；(7) `_create_lab` 外层异常本体优先于陈旧 last_error |
| LLM 后台配置（新增） | ✅ | settings context（Fernet 加密 + 测试连接 + 种子迁移） |
| DeepSeek 对接 | ✅ | ClaudeSdkAdapter.build_runner_env() 注入 ANTHROPIC_* env(零落盘,容器销毁消失) |
| 前端 pages/ shared/ 分层 | ✅ | AppLayout + 4 页面 |
| 事件流展示（Timeline） | ✅ | 详情「事件流」Tab：thinking / 工具 / 人类可读错误 + SSE 历史回放；流程图/进度条可点，按 sequence 归属节点 |
| Prometheus 指标 | ✅ | main.py Instrumentator |

### 3.2 已通过的验证

- 全链路（Mock 模式）：POST /tasks → Celery → 沙箱 clone → Agent → 事件持久化 → 报告生成 → MinIO 归档 ✅
- SSE 实时推送：agent/tasks.py 落库后 → Redis Pub/Sub → shared/sse.py 转发 → 前端 useTaskEvents hook（历史回放 + 15s 心跳 + 断开清理）✅
- 沙箱安全：OOM 限制、网络隔离、非 root、只读 rootfs、凭据零落盘（grep 0 命中）✅
- LLM 后台：CRUD / 掩码 / **明文落库(响应层 mask_secret 掩码)** / `is_default` 唯一启用 / 测试连接真实打 DeepSeek 端点 ✅(crypto.encrypt_secret 遗留未用；无独立 enabled)
- 种子迁移幂等性 ✅

---

## 4. 还有什么要干（差距清单 + 开发路线）

> 按优先级排序。P0 = 当前功能闭环所必需；P1 = 提案明确要求；P2 = 生产/体验增强。

### P0 — 功能闭环

> ⭐ **核心方向：Agent 阶段化编排**（从"单次注入黑盒"到"11 节点阶段流水线"）。
> 这是后续所有 Agent 相关工作的骨架，完整节点定义/契约/跳转见 `docs/agent-workflow.md`。

| # | 任务 | 现状 | 目标 | 涉及 |
|---|------|------|------|------|
| 0 | **平台 6 节点编排** ✅ | orchestrator 驱动 6 节点；AI 节点注入 `node-skills/` 蒸馏 skill（不加载桌面插件）。reproduce 第一枪打 audit 模板、容器内判定即停、拒 docker；confirmed/partial 的 PoC 正本入 reports 四列；节点 5 用 reproduce.poc 覆盖 poc_commands | orchestrator.py + nodes/ + node-skills/ |
| 1 | **SSE 实时事件推送** ✅ | `GET /api/v1/tasks/{id}/events/stream` 已就绪，前端 `useTaskEvents` 替换 3s 轮询 | （已完成） | shared/sse.py + task/api.py + frontend shared/hooks/useTaskEvents.ts |
| 2 | **取消任务真正生效** ✅ | 提交 cancelled 后立刻返回；后台只拆 agent-runner；靶场由 TTL 1h 静默销毁，并在 `/labs` 管理；编排器遇取消停跑 | （已完成） | task/service.py + agent/runtime_cleanup.py + orchestrator.py |
| 3 | **JWT 登录闭环** ✅ | `shared/deps.py::CurrentUserId` 注入 owner_id（去硬编码 "system"）；前端路由守卫 + 401 跳登录 + SSE `?token=` 鉴权 | （已完成） | shared/deps.py + task/report api.py + App.tsx + LoginPage + layout.tsx |
| 4 | **Evidence 上传链路** ✅ | `POST/GET /reports/{id}/evidences`（MinIO + 预签名 URL）+ 前端 EvidenceList 上传/下载 | （已完成） | report/service.py + report/api.py + frontend EvidenceList |

> **P0 顺序**：~~0（阶段化编排骨架）~~ ✅ → ~~1（SSE）~~ ✅ → ~~2（cancel）~~ ✅ → ~~3（登录）~~ ✅ → ~~4（证据）~~ ✅。
> **P0 全部完成**。6 节点编排由 Python orchestrator 驱动；AI 节点注入蒸馏 skill（见 `docs/agent-workflow.md`）。

### P1 — 提案明确要求

| # | 任务 | 说明 | 涉及 |
|---|------|------|------|
| 5 | **frontend features/ 填充** | task/agent/auth/report 四个领域模块：store.ts + hooks.ts（useSSE、useTaskEvents） | frontend/features/* |
| 6 | **Credential Proxy** ✅ | 任务级凭据(credentials 表,**明文存取**)→ 任务引用 credential_refs → 注入 agent-runner(env_var → 容器 env / file → `.secrets/` 600)→ 任务结束销毁 | settings context + core/credential_proxy.py + task/agent 接入 |
| 7 | **API Key + Service Account** | 平台级 API 认证（用于 CI/脚本调用） | identity context |
| 8 | **OIDC SSO（增量叠加）** | 保持 JWT，新增 OIDC 认证方式（Keycloak） | identity context |
| 9 | **RBAC 迁移** | 上一代有 4 角色 8 权限表，Crucible 尚未引入 | identity context + 路由守卫 |
| 10 | **报告导出** | report_data JSON 入库;GET /reports/{id}/export?format=json\|md 导出(md 由 renderer.py 渲染)。**不生成 docx**,原 plugin md_to_docx.py 不用 | report context + frontend |
| 11 | **审计日志 Context** | 平台操作审计（谁在何时做了什么） | 新 contexts/audit + event bus 订阅 |

### P2 — 生产 / 体验增强

| # | 任务 | 说明 |
|---|------|------|
| 12 | **OpenAPI → TS 类型生成** | 后端导出 OpenAPI schema，前端自动生成 types.ts（当前手动维护 api.ts 类型） |
| 13 | **OpenTelemetry + Sentry** | config 已有 sentry_dsn 字段，接入初始化；OTel 追踪 |
| 14 | **CI/CD（GitHub Actions）** | 后端 pytest + ruff、前端 tsc + build、构建镜像 |
| 15 | **生产部署实现（宿主机 systemd + Nginx）** | 路线 1 定稿（2026-08-19）：后端宿主机进程部署，DinD 方案放弃（与 bind-mount 语义冲突）。模板已落地 `infrastructure/deploy/`（systemd unit ×3 + Nginx + 操作手册），待真机验证 |
| 16 | **PostgreSQL 生产验证** | asyncpg 连接 + Alembic 迁移在 PG 上跑通 |
| 17 | **前端 TaskDetailPage / ReportPage 独立路由** ✅ | 详情已是独立路由，不再用 Drawer |

### 4.1 建议实施顺序

```
第一轮（P0，功能闭环）:  0（插件化编排 ✅）→ 1（SSE ✅）→ 2（cancel ✅）→ 3（登录 ✅）→ 4（证据 ✅）
第二轮（P1，提案对齐）:  5 → 6 → 7 → 8 → 9 → 10 → 11
第三轮（P2，生产就绪）:  12 → 13 → 14 → 15 → 16 → 17
```

P0 全部完成。每个 P1 完成后跑一遍全链路冒烟（任务创建 → 事件流 → 报告）作为回归门禁。

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
- API Key 当前**明文落库**(`settings/service.py` 存 `api_key_encrypted` 字段),列表接口走 `mask_secret` 掩码。`core/crypto.py` 的 Fernet 工具遗留未用
- LLM Provider 管理接口必须 JWT；`base_url` 须为 HTTPS 域名，解析到公网或 TUN fake-ip（`198.18.0.0/15`），禁止 IP 字面量和重定向；仍拒绝真实私网/回环/元数据地址
- Task/Report/Evidence 查询与写操作必须绑定 owner；非 owner 与不存在统一 404
- AI 生成 Compose 在宿主执行前走 `lab/compose_policy.py` 高危字段拒绝
- 创建/重试任务必须先 commit 再投递 Celery；投递失败把 Task/Run 标 failed 并返回 503。`retry?from_node=` 只允许 env_ready/audit/reproduce/report，前置未完成则 400
- 生产环境（`ENVIRONMENT=production`）强制校验：必须 AUTH_SECRET + 禁止 SQLite（config validator）

### 5.4 部署红线（2026-08-19 定稿）

- **后端（API + Celery worker）禁止容器化部署**——`docker.from_env()` 沙箱编排、`subprocess docker compose` 靶场调度、`host_workdir` bind mount 三者都依赖「后端与宿主 daemon 同一文件系统视图」。挂 docker.sock 的 sidecar 同样禁止（路径语义错乱 + 违背 socket 挂载禁令）
- 生产部署形态：基础设施容器（compose）+ 后端宿主机进程（systemd：`crucible-api` / `crucible-worker`）+ 前端 Nginx 静态托管
- 多机扩展不靠容器化后端，走「runner-node agent 服务化」路线（未来立项）

### 5.5 踩坑记录（务必先读）

| 坑 | 结论 |
|----|------|
| bcrypt 5.0 与 passlib 不兼容 | 锁 `bcrypt==4.0.1` |
| Docker SDK 7.x 移除 exec_run timeout | 用容器内 `timeout N` 命令包裹 |
| Docker SDK `containers.create` 不支持 stop_grace_period | 去掉该参数 |
| 容器 user=1000 写不了 tmpfs | tmpfs 挂载加 `uid=1000,gid=1000,mode=0755` |
| Celery worker 复用 async engine 报 "attached to a different loop" | worker 用独立 NullPool engine（`tasks.py::_worker_engine`） |
| 沙箱 internal 网络断外联导致 git clone 失败 | 默认 `sandbox_network_internal=False`（可外联），专用网络只隔离平台 |
| agent-runner 网络断外联 / DNS 失败 | 网必须 `internal=False`（旧 internal 网会重建）；容器 `dns=223.5.5.5,8.8.8.8,1.1.1.1`（Docker Desktop 自定义网的 127.0.0.11 经常失效） |
| agent-runner / 靶场容器残留堆积 | `teardown_task_runtime` 只拆任务 runner；配方上传失败必须补偿 `compose down`；终态 Lab 由巡检 `cleanup_terminal_runtimes` 兜底。靶场静默满 TTL 1h 且无 live 任务才销毁，管理入口为 `/labs` |
| 删除 Runner `host.docker.internal` 会打断 reproduce | reproduce 通过宿主映射端口访问 Lab，本阶段保留 host-gateway；彻底隔离需 Runner 加入 Lab 网络或出站代理 |
| container.logs(stream=True) 按字节 chunk 切分破坏 JSONL 行边界 | `LineBufferedJsonParser` 按 `\n` 累积字节再 json.loads；EOF 时调 `flush()` 处理最后一行 |
| 宿主机端口冲突（5432/6379 已被占用） | Crucible 用 5433/6380 |
| Windows 下 Celery 用 solo pool | `run_worker.py`：Windows `--pool=solo`（并行=1）；Linux `--pool=prefork`，进程数=`AGENT_RUNNER_CONCURRENCY_LIMIT` |
| **chromium headless 在 read_only rootfs 下崩溃** | 需额外挂 `/tmp` tmpfs + 容器 env `HOME=/tmp`（crashpad / ProcessSingleton 依赖可写 HOME；HOME 不得指向共享 `/workspace`，否则后续节点会读到上一跳 SDK 缓存），且 `/workspace` 不能挂 `noexec`（chromium + 搭靶场二进制都需可执行） |
| **nginx/反代默认缓冲 SSE** | 响应头加 `X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`（sse.py 端点已加） |
| **Redis Pub/Sub 离线即丢消息** | SSE 连接先订阅 Pub/Sub 再回放 DB 历史（`_replay_history`），实时帧丢掉已回放的 sequence；断线续播带 `Last-Event-ID` / `last_event_id` |
| **EventSource 无法注入 Authorization header** | token 走 query 参数 `?token=xxx`（前端 useTaskEvents + 后端 SSE owner 校验已接入） |
| **SSE 客户端断开不清理 → Redis 连接泄漏** | `stream_task_events` finally 块强制 `unsubscribe + pubsub.close + r.close`；并发规范见 concurrency.md §5 |

---

## 6. 快速参考

### 6.1 启动（开发模式）

```bash
cd infrastructure && docker compose up -d          # 基础设施
# agent-runner 镜像（Python + Node + 完整 Linux 命令；build context 必须是项目根）
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .
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

### 6.3 关键配置（`backend/.env` 是运行时唯一入口）

`config.py` 只做类型与校验，**不**再抄一份连接串。产品版本只写 `backend/pyproject.toml`（前端 `package.json` 不写 version）。桶名见 `shared/object_store.py`。LLM 凭据只在后台「设置」。pytest 由 `tests/conftest.py` 把 `DATABASE_URL` 覆盖为 sqlite，不读运行时库。

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | Docker PostgreSQL：`postgresql+asyncpg://crucible:crucible_secret@localhost:5433/crucible` |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis 6380，db0 事件 / db1 broker / db2 result |
| `S3_ENDPOINT` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` | MinIO 连接；桶名写死 `crucible-durable` / `crucible-task` / `crucible-public` |
| `CLAUDE_AGENT_SDK_ENABLED` | `true` 启用真实 Agent（否则 Mock） |
| `AGENT_RUNNER_IMAGE` / `AGENT_RUNNER_TIMEOUT_SECONDS` | Agent Runner 镜像与单容器超时（每 AI 轮独立计时） |
| `AGENT_RUN_HARD_TIMEOUT_SECONDS` | run 级巡检硬顶（默认 7200s）：live run 超过才被孤儿巡检强拆；与单容器超时解耦（2026-08-18，修复 run 总年龄误杀） |
| `AGENT_RUNNER_CONCURRENCY_LIMIT` | 并行硬顶（1–8，默认 4）：设置项上限 + Linux worker 进程数 |
| `AUTH_SECRET` | JWT；生产必填 |
| `SETTINGS_ENCRYPT_KEY` | 遗留配置(当前 settings 明文存取,Fernet 未生效) |

---

> **下一步**：P0 全部完成（0-4）。按 §4.1 顺序进入 **P1** —— 建议 P1-5（frontend features 填充）或 P1-6（Credential Proxy，补齐插件 `credential_store` 能力，见 agent-workflow.md §4）。每完成一项更新本文件的「已完成清单」并跑全链路冒烟。
