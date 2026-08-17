# Crucible — AI 漏洞验证平台

> 坩埚：代码与漏洞投入烈火，真伪自现。

## 项目概述

Crucible 是一个 AI 驱动的漏洞自动验证平台。安全研究员提交项目地址和漏洞描述，平台通过 Claude Code Agent 在隔离沙箱中自动分析源码、复现漏洞、生成报告。

## 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLAlchemy 2.0 Async / Celery / Redis
- **前端**: React 19 / TypeScript / Vite 6 / Ant Design 5 / Zustand / TanStack Query
- **数据库**: PostgreSQL 16 (生产) / SQLite (开发)
- **对象存储**: MinIO (S3 兼容)
- **Agent**: Claude Agent SDK (Python 0.2.134) — 跑在独立 Docker 容器 `crucible-agent-runner:base`
- **部署**: Docker Compose (全容器化)

## 架构原则

- **模块化单体** — Bounded Context 组织代码（task / agent / report / identity），不盲目微服务
- **事件驱动** — Context 间通过 Redis Pub/Sub 异步通信
- **Agent GateWay** — Agent 执行抽象为平台能力而非胶水代码
- **Agent 平台 6 节点编排** — Celery worker 的 `orchestrator.py` 驱动 6 节点(source/profile/env_ready/audit/reproduce/report),AI 节点用独立 agent-runner 容器 + `submit_result` 工具回传结构化 output;分支出口:非 web→skip 2-5、audit gate_fail→skip 4 判误报、env_ready 5 轮失败→task failed(2026-08-12 重塑,替代旧插件单次大调用)
- **Security by Default** — 沙箱真隔离、Agent 零信任。LLM 凭据通过 `docker run --env` 注入容器(容器销毁 env 消失,这部分零落盘成立);**注意:LLM API Key 当前明文存库**(`settings/service.py` 明文存取,响应层 `mask_secret` 掩码),`core/crypto.py` 的 Fernet 工具遗留未用

## 快速启动

```bash
# 1. 基础设施
cd infrastructure && docker compose up -d

# 2. Agent Runner 镜像（首次构建一次即可）
#    build context 必须是项目根，Dockerfile COPY node-skills/ 与 runner/
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .

# 3. 后端（端口统一 8010，与前端 Vite 代理对齐；`main.py` 直接运行入口默认 8000，请用 uvicorn 显式指定 8010）
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
│   │   │   ├── agent/           # Agent 执行平台（6 节点编排 + SDK 适配 + 容器编排）
│   │   │   │   ├── orchestrator.py     # ★ 6 节点编排器（循环 + 分支出口 + 断点续跑）
│   │   │   │   ├── nodes/              # ★ 6 节点实现（source/profile/env_ready/audit/reproduce/report）
│   │   │   │   ├── ai_runner.py        # AI 节点容器编排 + submit_result 工具 + schema 校验
│   │   │   │   ├── profile_detector.py # 节点 1 规则引擎 hints / SDK 关闭回退
│   │   │   │   ├── sdk_adapter.py      # Claude Agent SDK 适配器（env + prompt 构造）
│   │   │   │   ├── executor.py         # ClaudeSdkExecutor / MockExecutor + 工厂（遗留兼容）
│   │   │   │   └── tasks.py            # Celery 工作流（host clone + 调 orchestrator + 实时落库）
│   │   │   ├── identity/        # 认证与用户管理
│   │   │   ├── lab/             # Lab 靶场生命周期（复用、TTL、容器管理）
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
        ├── Dockerfile           # build context=项目根，COPY node-skills/ 进镜像
        ├── requirements.txt
        ├── node-skills/         # 每 AI 节点蒸馏 SKILL.md
        └── runner/run_one.py    # 容器内 entrypoint（skill → system_prompt + JSONL）
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
- 腾讯云端点：`https://api.lkeap.cloud.tencent.com/anthropic`（模型 `deepseek-v3.2`）
- 旧模型名 `deepseek-chat`/`deepseek-reasoner` 已于 2026-07-24 弃用
- `ClaudeSdkAdapter.build_runner_env()` 注入 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `API_TIMEOUT_MS` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` / `PYTHONUNBUFFERED=1` 等环境变量
- 容器内 SDK 通过 `canUseTool` 回调实现白盒审计黑白名单（只允许 `Read` / `Grep` / `Glob` + 受限的 `Bash` 子集：git-read / curl / python）
- 沙箱镜像：Python 3.11.15 + Node 20.20.2 + 完整 Linux 命令（非 slim）。靶场项目运行时仍在 `.vuln-env`，不在本镜像里装 JDK/Go/PHP/Chromium
