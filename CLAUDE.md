# Crucible — AI 漏洞验证平台

> 坩埚：代码与漏洞投入烈火，真伪自现。

## 项目概述

Crucible 是一个 AI 驱动的漏洞自动验证平台。安全研究员提交项目地址和漏洞描述，平台通过 Claude Code Agent 在隔离沙箱中自动分析源码、复现漏洞、生成报告。

## 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLAlchemy 2.0 Async / Celery / Redis
- **前端**: React 19 / TypeScript / Vite 6 / Ant Design 6 / TanStack Query（依赖里的 Zustand 未使用，勿新增引用）
- **数据库**: PostgreSQL 16（开发连 Docker `localhost:5433`；生产同引擎）
- **对象存储**: MinIO (S3 兼容)
- **Agent**: Claude Agent SDK (Python 0.2.134) — 跑在独立 Docker 容器 `crucible-agent-runner:base`
- **部署**: 基础设施容器化（PostgreSQL/Redis/MinIO）；**后端（API+Celery worker）宿主机进程部署**（systemd）——宿主为 **Linux（含 WSL）**，工作区 POSIX 路径，Celery 固定 prefork。后端需直连宿主 Docker daemon 编排沙箱/靶场，禁止容器化（含挂 docker.sock），详见 `docs/development-guide.md` §2.5/§5.4

## 架构原则

- **模块化单体** — Bounded Context 组织代码（task / agent / lab / project / report / settings / identity），不盲目微服务
- **事件驱动** — Context 间通过 Redis Pub/Sub 异步通信
- **Agent GateWay** — Agent 执行抽象为平台能力而非胶水代码
- **Agent 平台 15 节点编排** — Celery worker 的 `orchestrator.py` 按 `DEFAULT_PIPELINE` 驱动 15 节点(source/profile/scan_gitleaks/scan_osv/scan_semgrep/api_inventory/env_ready/cluster/api_hunt/screen/triage/dispatch/audit/reproduce/report)。验证任务(`task_type=verify`)跑其中 6 个终认节点、扫描/清单/猎洞/聚类/轻量快审/AI 二审/调度 `VERIFY_MODE` skip；审计任务(`discovery`)跑全图、dispatch 把合格可疑真洞入 Redis db3 终认队（LeadWorker 复用同一套 audit/reproduce）。AI 节点用独立 agent-runner 容器 + `submit_result` 回传结构化 output
- **Security by Default** — 沙箱真隔离、Agent 零信任。LLM 凭据通过 `docker run --env` 注入容器(容器销毁 env 消失,这部分零落盘成立)。LLM API Key / 任务凭据 **Fernet 加密落库**（`seal_secret` / `reveal_secret`，存量明文可读），响应层 `mask_secret` 掩码。

## 快速启动

```bash
# 1. 基础设施
cd infrastructure && docker compose up -d

# 2. Agent Runner 镜像（首次或改 runner/Dockerfile 时构建；skill 不进镜像）
#    build context 必须是项目根，Dockerfile 只 COPY runner/
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .

# 3. 后端（端口统一 8010，与前端 Vite 代理对齐；`main.py` 直接运行入口默认 8000，请用 uvicorn 显式指定 8010）
#    数据库：Docker PostgreSQL 5433（.env 里 DATABASE_URL）
cd backend
cp .env.example .env
pip install -e ".[dev]"
uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload

# 4. 前端
cd frontend && npm install && npm run dev
```

## 项目结构

```
Crucible/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口 (<100行)
│   │   ├── core/                # 配置、数据库、安全、Celery、agent-runner 编排
│   │   ├── contexts/            # Bounded Contexts
│   │   │   ├── task/            # 任务管理 (models/schemas/service/repo/api) + NodeRun/重试/删除
│   │   │   ├── agent/           # Agent 执行平台（15 节点编排 + SDK 适配 + 容器编排）
│   │   │   │   ├── orchestrator.py     # ★ 15 节点编排器（就绪波次 + 分支出口 + 断点续跑）
│   │   │   │   ├── contracts/          # 公开 Input/Handoff/ControlSignals + DEFAULT_PIPELINE
│   │   │   │   ├── nodes/              # ★ 15 节点实现（含 api_inventory / api_hunt + scan_*/env_ready/cluster/screen/triage/dispatch/audit/reproduce/report）
│   │   │   │   ├── api_inventory/      # 确定性 API 清单 parsers
│   │   │   │   ├── stacks/             # 语言/框架单一注册表
│   │   │   │   ├── ai_runner.py        # AI 节点容器编排 + submit_result 工具 + schema 校验
│   │   │   │   ├── profile_detector.py # profile 规则引擎 hints / SDK 关闭回退
│   │   │   │   ├── sdk_adapter.py      # Claude Agent SDK 适配器（env + prompt 构造）
│   │   │   │   └── tasks.py            # Celery 工作流（host clone + 调 orchestrator + 实时落库）
│   │   │   ├── identity/        # 认证与用户管理
│   │   │   ├── lab/             # Lab 靶场生命周期（复用、TTL、容器管理）
│   │   │   ├── finding/         # 告警复核台（RawFinding/AlertGroup/Adjudication + 判决回流）
│   │   │   ├── discovery/       # 扫描运行（ScanRun）登记
│   │   │   ├── project/         # ★ 项目源码管理（projects 表 CRUD + 画像缓存）
│   │   │   └── report/          # 报告与证据（结构化 report_data + md 渲染导出）
│   │   └── shared/              # 共享基类、事件总线、SSE、鉴权依赖
├── frontend/
│   └── src/
│       ├── app/                 # providers、layout、路由守卫
│       ├── pages/               # 页面组件
│       ├── features/            # 领域功能模块（task/components 等）
│       └── shared/              # api / hooks（useTaskEvents）/ lib / components（NodeSteps/ReportContent）
├── plugins/                     # 桌面 Claude Code 插件（母本，不进 runner）
│   └── vuln-verify-expert/
│       ├── agents/vuln-verify-expert.md
│       ├── skills/run-project-env/
│       └── skills/vuln-verify/
└── infrastructure/              # Docker Compose
    ├── docker-compose.yml
    └── agent-runner/            # Agent Runner 专用镜像
        ├── Dockerfile           # build context=项目根；skill 不 COPY，起容器 -v 挂当前节点
        ├── requirements.txt
        ├── node-skills/         # 每 AI 节点 SKILL.md 母本（host；-v → /node-skill）
        └── runner/run_one.py    # 容器内 entrypoint（/node-skill/SKILL.md → system_prompt + JSONL）
```

## 开发规范

**架构决策与后续路线见 `docs/development-guide.md`**（架构原则/决策溯源 + P0/P1/P2 开发路线）。

**工程治理规范见 `docs/governance/`**：`constitution.md`（开发宪法）+ `AGENTS.md`（协作标准），源自 `ai-coding-best-practice` Harness 资产包，裁决顺序 `constitution.md > AGENTS.md > 既有代码`。

**本项目 commit message 用简体中文**。覆盖全局 CLAUDE.md「commit message 英文」默认。Conventional Commits 类型前缀（`feat:` / `fix:` / `refactor:` / `chore:` / `docs:` 等）与 `Co-Authored-By:` trailer 仍保持英文（工具链解析要求）；描述主体、bullet、footer 一律中文。

## 对接第三方 LLM（DeepSeek）

LLM 凭据只在后台「设置 → LLM Provider」配置。`.env` 只负责打开真实 Agent：

```bash
# backend/.env
CLAUDE_AGENT_SDK_ENABLED=true
```

Celery worker 从默认 Provider 取端点/Key/模型，经 `docker run --env` 注入 `crucible-agent-runner`（容器销毁 env 消失，零落盘）。

配置说明：
- DeepSeek 官方：`https://api.deepseek.com/anthropic`（`deepseek-v4-flash` / `deepseek-v4-pro`；思考 thinking.type=enabled|disabled 默认 enabled，强度 output_config.effort=low|high|max 默认 high）
- 旧模型名 `deepseek-chat`/`deepseek-reasoner` 已于 2026-07-24 弃用
- `ClaudeSdkAdapter.build_runner_env()` 注入 `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` / `API_TIMEOUT_MS` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` / `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0`（内层 bwrap 与锁定 Docker 冲突；Bash 凭据由 runner PreToolUse `env -u` 剥离）/ `CLAUDE_CODE_MAX_CONTEXT_TOKENS` / `PYTHONUNBUFFERED=1` 等变量；凭据按 `auth_mode` 只注入 `ANTHROPIC_AUTH_TOKEN` 或 `ANTHROPIC_API_KEY` 之一，非 auto effort 另注入 CLI effort 变量
- 容器内 SDK 保留完整 Claude Code 工具集并使用 `bypassPermissions` 实现无人值守自动化；`allowed_tools` 仅为自动批准提示，不是白名单。平台通过 `PreToolUse` Bash 硬拒绝、Docker 隔离、`setting_sources=[]`、strict MCP 与子进程凭据清理建立边界
- 沙箱镜像：Python 3.11.15 + Node 20.20.2 + 完整 Linux 命令（非 slim）。靶场项目运行时仍在 `.vuln-env`，不在本镜像里装 JDK/Go/PHP/Chromium
