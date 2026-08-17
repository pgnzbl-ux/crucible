<div align="center">

# 🔥 Crucible

**AI 驱动的漏洞自动验证平台 —— 代码与漏洞投入烈火，真伪自现。**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Claude Agent SDK](https://img.shields.io/badge/Claude%20Agent%20SDK-0.2.134-D97757?logo=anthropic&logoColor=white)](https://github.com/anthropics/claude-agent-sdk-python)

</div>

---

## 📖 目录

- [背景与动机](#-背景与动机)
- [Crucible 是什么](#-crucible-是什么)
- [核心特性](#-核心特性)
- [架构概览](#-架构概览)
- [工作流程](#-工作流程)
- [技术栈](#-技术栈)
- [项目结构](#-项目结构)
- [快速开始](#-快速开始)
- [配置说明](#-配置说明)
- [对接第三方 LLM](#-对接第三方-llm)
- [使用指南](#-使用指南)
- [测试](#-测试)
- [开发规范](#-开发规范)
- [安全模型](#-安全模型)
- [路线图](#-路线图)
- [文档索引](#-文档索引)

---

## 🎯 背景与动机

漏洞扫描器（SAST/DAST）的输出往往是**噪声大于信号**：海量告警里混杂真实可利用漏洞、
误报、代码异味。人工逐一复核成本极高，而完全依赖扫描器又会错过真正的威胁。

**安全研究员真正需要的是「验证」而非「发现」** —— 给定一个声称的漏洞，判定它：

- 是否**真实存在**（不是扫描器的误判）
- 能否**实际利用**（攻击者可观察的危害）
- **复现路径**是什么（PoC + 证据链）
- 应该**如何修复**

Crucible 把这个过程自动化：让一个**白盒优先的 AI Agent**（有源码、有靶场）按安全研究员的
方法论走完「读源码 → 走利用链 → 纸上推演 payload → 靶场一次 HTTP 确认 → 出报告」，
把扫描器输出的「假设」变成「已验证/误报」的判定。

> **白盒优先**：先用源码走通调用链、读全每一层防御，把 payload 在纸上推演到「理论可通」，
> 再用一次 HTTP 请求在靶场确认危害。推演不通即判误报，绝不在靶场上黑盒盲试。

---

## 🧩 Crucible 是什么

Crucible 是一个 **Web 平台 + Agent 执行引擎** 的组合：

| 角色 | 职责 |
|---|---|
| **Web 平台**（FastAPI + React） | 任务管理、用户认证、实时进度推送、报告归档、证据管理 |
| **Agent 执行引擎**（Claude Agent SDK + 专用容器） | 在隔离容器内按 6 节点编排执行漏洞验证(每 AI 节点独立容器 + `submit_result` 工具回传结构化 output) |

**输入**：项目 Git 地址 + 漏洞描述（可选 PoC / 推理过程）
**输出**：结构化中文验证报告（结论 + CVSS 评分 + 证据链 + 修复建议）+ 可复现的 PoC

**判定档位**（由最强可观察证据决定，禁止用诊断信号冒充确认）：
`已确认` / `部分确认` / `代码可达` / `CODE SMELL` / `误报` / `未复现`

---

## ✨ 核心特性

- 🤖 **平台 6 节点编排** —— Celery orchestrator 驱动 6 节点。AI 节点各自注入蒸馏 skill（`node-skills/`），桌面 `plugins/vuln-verify-expert` 只当方法论母本。分支出口：非 web skip、audit fail 判误报、env 5 轮失败、audit uncertain 待复核。
- 🧪 **靶场就绪与复用** —— AI 写 Dockerfile/compose，worker 启动并探活；同项目可复用已就绪靶场与配方缓存。按 README / 鉴权判断是否需要登录：有默认或可初始化账号则给出账密，公开入口标明免登录，否则说明需自行注册等前置条件
- 🔄 **实时进度推送** —— 任务详情通过 SSE 展示节点进度与 Agent 过程（思考、工具调用、结论）；完成节点分区展示审计结论与靶场地址/凭据
- 🧱 **模块化单体** —— Bounded Context 组织代码（task / agent / lab / report / identity / project / settings），事件驱动通信，未来可拆
- 🔒 **Security by Default** —— Agent 跑在独立隔离容器(非 root + 只读 rootfs + cap_drop ALL + 资源限制)。LLM 凭据通过 `docker run --env` 注入容器(容器销毁 env 消失)；平台设置库中的 API Key 明文存储，接口回显掩码
- 🌐 **多 LLM 后端** —— 通过 Anthropic 兼容端点对接 DeepSeek / 腾讯云等第三方 LLM，后台可配置多 Provider + 测试连接
- 📦 **证据归档** —— 报告与证据文件归档到 MinIO（S3 兼容），支持手动上传补充证据 + 预签名下载
- 🛑 **任务取消** —— 取消任务会停止 worker 并回收容器
- 🔐 **JWT 认证** —— 登录、路由守卫、owner 隔离、SSE 鉴权

---

## 🏗️ 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│  浏览器（React 19 + Ant Design 5）                                │
│    登录 → 提交任务 → SSE 实时进度 → 查看报告/证据                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP + SSE（JWT 鉴权）
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  后端 API（FastAPI · async）                                       │
│    contexts/ {task, agent, lab, report, identity, project, settings} │
│    shared/  {events, sse, deps}                                   │
└──────┬────────────────────┬──────────────────────┬──────────────┘
       │                    │                      │
       ▼ SQLAlchemy 2.0     ▼ Celery               ▼ Redis Pub/Sub
┌──────────────┐   ┌────────────────────┐   ┌──────────────────┐
│ PostgreSQL   │   │ Celery Worker      │   │ Redis            │
│ / SQLite     │   │  agent/tasks.py    │◄──┤ broker + pubsub  │
│              │   │   host git clone   │   └──────────────────┘
│ tasks/runs/  │   │   docker run ──────┼──┐
│ nodes/labs/  │   │   消费 JSONL 流    │  │ 每 AI 节点独立起容器
│ reports      │   │   落库 + 发布事件   │  ▼
└──────────────┘   └────────────────────┘   ┌────────────────────────┐
                                            │ agent-runner 容器        │
                                            │  (crucible-agent-runner) │
                                            │                          │
                                            │  tini → run_one.py       │
                                            │   ├─ 读 .node.json        │
                                            │   ├─ 读 node-skills/{NODE_KEY}/SKILL.md │
                                            │   ├─ system_prompt append（claude_code） │
                                            │   ├─ 注入 submit_result MCP            │
                                            │   └─ query() → JSONL                   │
                                            │                                        │
                                            │  _bundled/claude (CLI)                 │
                                            │  + 每节点一份蒸馏 skill                 │
                                            └────────────────────────┘
                                                      │
                                                      ▼
                                            ┌────────────────────────┐
                                            │ MinIO（S3 兼容）         │
                                            │  crucible-artifacts     │
                                            │  crucible-evidence      │
                                            │  crucible-source        │
                                            │  crucible-lab-recipe    │
                                            └────────────────────────┘
```

**三层隔离模型**：
1. **平台层** —— API/Worker/DB/Redis/MinIO，跑在宿主机或平台容器
2. **Agent 层** —— `agent-runner` 专用镜像，每个 AI 节点一个隔离容器；LLM 凭据经环境变量注入，随容器销毁
3. **审计层** —— Agent 输出视为不可信，所有事件走 schema 校验才落库

---

## 🔄 工作流程

一次漏洞验证任务的完整生命周期：

```
1. 用户提交任务（项目地址 + 漏洞描述）
   → API 自动按 git_url upsert Project + 创建 Task + TaskRun → 投递 Celery
        │
2. Worker 在宿主机 git clone 源码到隔离临时目录(clone 后校验非空)
        │
3. Worker 调 orchestrator.run_orchestration 驱动 6 节点循环:
   ┌─ 节点 0 source      ■代码:clone 结果包装
   ├─ 节点 1 profile    □AI:读 README/依赖建立全景 + web 门禁(规则引擎作 hints)
   │                     └─ 分支:is_web=false → skip 2-5,task completed 不下结论
   ├─ 节点 2 env_ready  □AI+■代码:写/复用靶场配方 → worker compose up + 探活
   │                     │  按登录能力给出账密、免登录说明或「需自行注册」等前置条件
   │                     └─ 分支:5 轮失败 → task failed(可 retry)
   ├─ 节点 3 audit      □AI:白盒审计 + Gate 三问（不发 HTTP）
   │                     └─ 分支:gate_fail → skip 4,判 false_positive
   ├─ 节点 4 reproduce  □AI:一次 HTTP 复现 + 6 档判定（只交 attempts/evidence，不写报告）
   └─ 节点 5 report     □AI:唯一文档作者 → 漏洞报告或验证记录 → 落 Report 表
        │
4. 每 AI 节点:写 .node.json → 起 agent-runner 容器(按 NODE_KEY 注入对应 skill)
   → 容器内调 submit_result MCP 工具回传结构化 output
   → worker schema 校验 → 落 NodeRun.output_json(断点续跑复用)
        │
5. 节点状态与 Agent 事件落库，经 Redis 推到前端任务详情
        │
6. 节点 5 产出 report_data → Report 表(verdict；confirmed/partial 才有 cvss)
   → GET /reports/{id}/export?format=json|md 导出
        │
7. 任务结束：拆靶场、回收容器、清理临时目录（重试复用已完成节点）
```

---

## 🛠️ 技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python 3.11+ · FastAPI · SQLAlchemy 2.0 Async · Celery · Pydantic v2 · python-jose (JWT) |
| **前端** | React 19 · TypeScript 5.6 · Vite 6 · Ant Design 5 · Zustand · TanStack Query · wouter |
| **数据库** | PostgreSQL 16（生产）· SQLite（开发）|
| **缓存/消息** | Redis 7（Celery broker + 事件总线 Pub/Sub + SSE 转发）|
| **对象存储** | MinIO（S3 兼容，报告 + 证据归档）|
| **Agent** | `claude-agent-sdk==0.2.134`（wheel 自带 Claude Code CLI `2.1.226`）+ 每节点 `node-skills/`（桌面 `vuln-verify-expert` 为方法论母本） |
| **编排** | Docker Compose（基础设施）+ Docker SDK（Agent 容器生命周期）|

---

## 📁 项目结构

```
Crucible/
├── backend/                         # 后端（FastAPI + Celery）
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口（<100 行）
│   │   ├── core/                    # config / database / security / celery_app / agent_runner / crypto
│   │   ├── contexts/                # Bounded Contexts
│   │   │   ├── task/                # 任务管理（CRUD + 节点进度 + 事件流 + 取消/重试）
│   │   │   ├── agent/               # 6 节点编排（orchestrator / nodes / ai_runner）
│   │   │   ├── lab/                 # 靶场生命周期（复用、TTL、配方缓存）
│   │   │   ├── project/             # 项目源码管理
│   │   │   ├── report/              # 报告 + 证据（MinIO 归档）
│   │   │   ├── identity/            # 认证（JWT + bcrypt）
│   │   │   └── settings/            # LLM Provider 配置
│   │   └── shared/                  # events / sse / deps
│   ├── run_worker.py                # Celery worker 入口（solo pool）
│   ├── pyproject.toml
│   ├── .env.example                 # 环境变量模板（复制为 .env）
│   └── tests/
│
├── frontend/                        # 前端（React + Vite）
│   └── src/
│       ├── app/                     # providers / layout
│       ├── pages/                   # Dashboard / Tasks / TaskDetail / Reports / Settings / Login
│       ├── features/                # 领域模块（task 列表与详情）
│       └── shared/                  # api / hooks / 节点与报告展示组件
│
├── plugins/                         # 桌面 Claude Code 插件（母本，不进 runner）
│   └── vuln-verify-expert/
│       ├── .claude-plugin/plugin.json
│       ├── agents/vuln-verify-expert.md
│       └── skills/                  # run-project-env / vuln-verify
│
├── infrastructure/                  # Docker
│   ├── docker-compose.yml           # postgres(5433) + redis(6380) + minio(9000/9001)
│   └── agent-runner/                # Agent Runner 专用镜像
│       ├── Dockerfile               # COPY node-skills/，不 COPY plugins/
│       ├── node-skills/             # 每 AI 节点蒸馏 SKILL.md
│       ├── requirements.txt
│       └── runner/run_one.py        # skill → system_prompt + JSONL
│
├── docs/                            # 设计文档
│   ├── development-guide.md         # 架构决策 + P0/P1/P2 路线
│   ├── agent-workflow.md            # 6 节点编排 + skill 注入
│   ├── governance/                  # 开发宪法 + 协作标准
│   └── superpowers/                 # 已评审 spec / plan
│
├── .claude/                         # Claude Code 项目规则与契约
│   ├── rules/                       # 各维度的硬性约定
│   ├── skills/                      # deploy / test / smoke / db-migrate / git-push
│   └── api-contract.md              # API 端点契约
│
├── CLAUDE.md                        # 项目级 AI 协作约定
└── README.md                        # 本文件
```

---

## 🚀 快速开始

### 前置条件

- [Docker](https://docs.docker.com/get-docker/) 20+ 与 Docker Compose v2+
- [Python](https://www.python.org/downloads/) 3.11+（运行后端）
- [Node.js](https://nodejs.org/) 18+（构建前端）
- 一个 Anthropic 兼容 LLM 凭据（推荐 [DeepSeek](https://platform.deepseek.com/)，也可用 Mock 模式跳过）

### 部署步骤

```bash
# ── 1. 克隆项目 ──
git clone <repo-url> Crucible && cd Crucible

# ── 2. 启动基础设施（PostgreSQL / Redis / MinIO）──
#    端口错开宿主机默认：5433 / 6380 / 9000 / 9001
cd infrastructure && docker compose up -d
cd ..

# ── 3. 构建 Agent Runner 镜像（首次或改 node-skills / Dockerfile 后必需）──
#    ⚠️ build context 必须是项目根；镜像含 Python + Node + 完整 Linux 命令
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .

# ── 4. 配置后端环境变量 ──
cd backend
cp .env.example .env
# 按需编辑 .env（见「配置说明」段）

# ── 5. 安装后端依赖 ──
pip install -e ".[dev]"

# ── 6. 启动后端 API（终端 1）──
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010

# ── 7. 启动 Celery worker（终端 2）──
python run_worker.py

# ── 8. 启动前端（终端 3）──
cd ../frontend
npm install
npm run dev
```

### 访问地址

| 服务 | 地址 | 凭据 |
|---|---|---|
| 前端 UI | http://localhost:5173 | 见下 |
| API 文档（Swagger） | http://localhost:8010/docs | — |
| MinIO 控制台 | http://localhost:9001 | minioadmin / minioadmin |
| PostgreSQL | localhost:5433 | crucible / crucible_secret |
| Redis | localhost:6380 | — |

**默认登录账号**（开发环境种子）：`admin@crucible.local` / `crucible123`
（由 `.env` 的 `ADMIN_EMAIL` / `ADMIN_PASSWORD` 控制）

### 验证部署

```bash
# 健康检查
curl http://localhost:8010/health
# → {"status":"ok","version":"0.3.0"}

# 登录拿 token
curl -X POST http://localhost:8010/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@crucible.local","password":"crucible123"}'
```

---

## ⚙️ 配置说明

所有基础设施与运行开关通过环境变量注入（`backend/.env`，模板见 `backend/.env.example`）。LLM Provider 在后台「设置」里配置，不走 `.env`。

### 核心配置

| 变量 | 说明 | 默认 |
|---|---|---|
| `ENVIRONMENT` | `development` / `production`（生产强制安全校验） | `development` |
| `DATABASE_URL` | 开发用 SQLite，生产必须 PostgreSQL | `sqlite+aiosqlite:///./crucible.db` |
| `REDIS_URL` | Redis 地址（端口 6380） | `redis://localhost:6380/0` |
| `AUTH_SECRET` | JWT 签名密钥（生产必配，≥32 字节） | `dev-secret-change-in-production` |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | 初始管理员种子账号 | `admin@crucible.local` / `crucible123` |

### Agent 配置

| 变量 | 说明 | 默认 |
|---|---|---|
| `CLAUDE_AGENT_SDK_ENABLED` | `true` 启用真实 Agent；`false` 走 Mock（开发默认） | `false` |
| `CLAUDE_SDK_MAX_TURNS` | Agent 最大对话轮数 | `180` |
| `AGENT_RUNNER_IMAGE` | Agent Runner 镜像名 | `crucible-agent-runner:base` |
| `AGENT_RUNNER_CPU_LIMIT` | 单容器 CPU 限制 | `1.0` |
| `AGENT_RUNNER_MEMORY_LIMIT` | 单容器内存限制 | `1g` |
| `AGENT_RUNNER_TIMEOUT_SECONDS` | 任务总超时（同时也是 Celery 硬超时） | `1800` |
| `AGENT_RUNNER_CONCURRENCY_LIMIT` | 并发容器上限 | `4` |

### 对象存储

| 变量 | 说明 | 默认 |
|---|---|---|
| `S3_ENDPOINT` | MinIO API 地址 | `http://localhost:9000` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | 与 compose 中 MinIO 账号一致 | `minioadmin` |

报告桶 `crucible-artifacts`、证据桶 `crucible-evidence`、源码缓存桶 `crucible-source` 由平台写死，启动时 `createbuckets` 容器负责创建；靶场配方桶 `crucible-lab-recipe` 在首次上传配方时按需创建。同一 `space/project`（如 `siteboon/claudecodeui`）加同一 branch/tag/commit 会命中 `source_artifacts` 表，直接从 MinIO 解开到工作区 `{audit-uuid}/{仓库名}`，不再先 `ls-remote` 对 SHA。

---

## 🌐 对接第三方 LLM

Crucible 通过 **Anthropic 兼容端点**对接第三方 LLM。凭据只在后台「设置 → LLM Provider」配置（明文入库，列表回显掩码）。Worker 把默认 Provider 注入 agent-runner 容器环境变量（容器销毁即消失）。

`.env` 里只需打开真实 Agent：

```bash
# backend/.env
CLAUDE_AGENT_SDK_ENABLED=true
```

然后登录前端 → **设置 → LLM Provider** → 新增并设为默认。可用端点：

| Provider | Base URL | 模型示例 |
|---|---|---|
| DeepSeek 官方 | `https://api.deepseek.com/anthropic` | `deepseek-v4-flash` / `deepseek-v4-pro` |
| 腾讯云知识引擎 | `https://api.lkeap.cloud.tencent.com/anthropic` | `deepseek-v3.2` |

后台能力：
- 多 Provider，同一时刻仅一个激活
- 测试连接真实打端点
- 改完立即生效，不用重启进程

---

## 📋 使用指南

### Mock 模式（默认，无需 LLM 凭据）

`CLAUDE_AGENT_SDK_ENABLED=false` 时，Agent 走 `MockExecutor`，产出可预期的模拟事件流，
用于验证全链路（任务创建 → 实时进度 → 报告生成）。

```bash
# 1. 登录前端 http://localhost:5173（admin@crucible.local / crucible123）
# 2. 「任务管理」→ 新建任务 → 填项目地址 + 漏洞描述
# 3. 打开任务详情，查看节点进度与 Agent 过程
# 4. 完成后查看报告 + 上传补充证据
```

### 真实模式

`CLAUDE_AGENT_SDK_ENABLED=true` 且后台已配置默认 LLM Provider 后，Worker 会按节点拉起 agent-runner 容器，
注入对应 `node-skills/` 蒸馏 skill，执行真实白盒验证（桌面插件不进容器）。

### API 调用示例

```bash
TOKEN=<上一步登录拿到的 access_token>

# 创建任务
curl -X POST http://localhost:8010/api/v1/tasks/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "project_address": "https://github.com/example/vuln-app.git",
    "vulnerability_description": "登录接口存在 SQL 注入，username 参数未过滤"
  }'

# SSE 实时事件流（EventSource 不能注入 header，token 走 query）
curl -N -H "Accept: text/event-stream" \
  "http://localhost:8010/api/v1/tasks/<task_id>/events/stream?token=$TOKEN"

# 取消任务
curl -X POST http://localhost:8010/api/v1/tasks/<task_id>/cancel \
  -H "Authorization: Bearer $TOKEN"

# 查看报告
curl http://localhost:8010/api/v1/reports/task/<task_id> \
  -H "Authorization: Bearer $TOKEN"
```

---

## 🧪 测试

```bash
cd backend

# 单元测试（排除需 Docker/Redis 的 smoke）
pytest tests -k "not smoke"

# Agent Runner 容器冒烟（基础生命周期 / OOM / 取消 / 行缓冲 / 清理）
python tests/smoke_agent_runner.py

# SSE 实时推送冒烟（帧格式 / 历史回放 / 断开检测 / 心跳间隔）
python tests/smoke_sse.py
```

前端类型检查：
```bash
cd frontend && npm run typecheck
```

---

## 📐 开发规范

### 架构原则

- **模块化单体优先** —— Bounded Context 组织代码，跨 Context 只用 ForeignKey + ID，**禁止**建 ORM 关系（mapper 报错）
- **事件驱动** —— 跨 Context 异步通知走 Redis Pub/Sub（`shared/events.py`），事件总线失败**不允许**影响主流程
- **异步全栈** —— DB → 消息 → HTTP → SSE 全 async，Celery worker 用独立 NullPool engine
- **Agent as a Platform** —— Agent 执行是平台能力，agent-runner 容器是唯一执行抽象

### Celery worker

- 用 `run_worker.py`（固定 `--pool=solo`，Windows 兼容），**不要**裸 `celery -A ... worker`
- `task_acks_late=True` + `worker_prefetch_multiplier=1`：崩溃可重派、不饿死
- 取消任务：`celery_app.control.revoke(run.id, terminate=True)` + SIGTERM 钩子销毁容器

### Commit 约定

- **本项目 commit message 用简体中文**（覆盖全局英文默认）
- Conventional Commits 类型前缀（`feat:` / `fix:` / `refactor:` / `chore:` / `docs:` 等）与 `Co-Authored-By:` trailer 保持英文
- 描述主体、bullet、footer 一律中文

详见 [`CLAUDE.md`](CLAUDE.md) 与 [`.claude/rules/git-workflow.md`](.claude/rules/git-workflow.md)。

---

## 🔒 安全模型

三条底线，破坏任一即不可上线：

### 1. 凭据

- 注入 Agent 容器的 LLM Key **只**走环境变量（`docker run --env`），容器销毁即消失
- 不写进 `Dockerfile` / `docker-compose.yml` / 种子脚本 / 配置文件
- 平台设置库中的 API Key 明文存储，列表接口回显掩码
- 提交前 `git grep -nE "sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}"` 必须**零命中**

### 2. Agent 容器真隔离

`AgentRunnerSpec` 默认值即安全红线：

| 项 | 要求 |
|---|---|
| 用户 | 非 root（uid=1000） |
| rootfs | 只读 |
| capabilities | `cap_drop: ["ALL"]` + `no-new-privileges` |
| 内存 / CPU / PIDs | 必须限制 |
| 网络 | 可外联（git clone / 拉镜像），与平台网络隔离 |
| 超时 | 必须有，到点强制回收 |
| tmpfs | `uid=1000,gid=1000,mode=0755` |

### 3. Agent 零信任

- Agent 输出视为**不可信**，所有事件走 schema 校验才落库
- Agent 不能直接访问平台 DB / Redis / MinIO —— 只能通过受控事件流
- `PreToolUse` hook 限制工具：允许 Read/Grep/Glob 与受限 Bash，拦截 rm/chmod/`|bash` 等破坏性命令

---

## 🗺️ 路线图

未完成项（完整说明见 [`docs/development-guide.md`](docs/development-guide.md)）：

- frontend `features/` 其余领域模块
- API Key + Service Account
- OIDC SSO
- RBAC
- 审计日志
- 插件平台能力补齐（browser MCP 等）
- OpenAPI → TS 类型生成
- OpenTelemetry + Sentry
- CI/CD
- 生产部署（DinD + mTLS）
- PostgreSQL 生产验证
- 任务/报告详情独立路由体验打磨

---

## 📚 文档索引

| 文档 | 内容 |
|---|---|
| [docs/development-guide.md](docs/development-guide.md) | 架构决策溯源 + P0/P1/P2 完整路线 + 踩坑记录 |
| [docs/agent-workflow.md](docs/agent-workflow.md) | 平台 6 节点编排 + 蒸馏 skill 注入 + 平台↔方法论母本契约 |
| [docs/governance/](docs/governance/) | 开发宪法 `constitution.md` + 协作标准 `AGENTS.md` |
| [CLAUDE.md](CLAUDE.md) | 项目级 AI 协作约定（技术栈/启动/规范） |
| [.claude/api-contract.md](.claude/api-contract.md) | API 端点契约（路由/请求/响应/错误码） |
| [.claude/rules/](.claude/rules/) | 各维度硬性约定（backend/frontend/security/concurrency/...） |
| [plugins/vuln-verify-expert/README.md](plugins/vuln-verify-expert/README.md) | 漏洞验证插件说明 |

---

## 💬 设计哲学

> **坩埚（Crucible）**：一种耐高温容器，用于熔炼、提纯。
> 在这里，扫描器的海量告警投入 Agent 的「烈火」，真实的可利用漏洞在白盒分析 +
> 靶场复现的考验中「自现」，误报被烧成灰烬。

**白盒优先，证据为王**：源码是最大的优势——先用足它，靶场只做最后确认。
诊断信号（HTTP 500 / 日志 / sink 命中）只证明可达，**永远不能**作为「已确认」的依据。

---

<div align="center">

**Crucible** · 让 AI 像安全研究员一样验证漏洞，而非像扫描器一样盲射。

</div>
