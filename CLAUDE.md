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
- **Security by Default** — 沙箱真隔离、凭据不落盘、Agent 零信任

## 快速启动

```bash
# 1. 基础设施
cd infrastructure && docker compose up -d

# 2. Agent Runner 镜像（首次构建一次即可）
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base infrastructure/

# 3. 后端
cd backend
cp ../.env.example .env
pip install -e ".[dev]"
python -m app.main

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
│   │   │   ├── task/            # 任务管理 (models/schemas/service/repo/api)
│   │   │   ├── agent/           # Agent 执行平台（SDK 适配 + 容器编排）
│   │   │   │   ├── sdk_adapter.py      # Claude Agent SDK 适配器（env + prompt 构造）
│   │   │   │   ├── runner_bridge.py    # 拉起容器 + 写 prompt + 流消费组合
│   │   │   │   ├── executor.py         # ClaudeSdkExecutor / MockExecutor + 工厂
│   │   │   │   └── tasks.py            # Celery 工作流（host clone + 容器编排 + 实时落库）
│   │   │   ├── identity/        # 认证与用户管理
│   │   │   └── report/          # 报告与证据
│   │   └── shared/              # 共享基类、事件总线
│   ├── alembic/                 # 数据库迁移
│   └── tests/                   # 测试
├── frontend/
│   └── src/
│       ├── app/                 # providers、router
│       ├── pages/               # 页面组件
│       ├── features/            # 领域功能模块
│       └── shared/              # 共享组件/工具
└── infrastructure/              # Docker Compose
    ├── docker-compose.yml
    └── agent-runner/            # Agent Runner 专用镜像
        ├── Dockerfile
        ├── requirements.txt
        └── runner/run_one.py    # 容器内 entrypoint（JSONL 流）
```

## 开发规范

**架构决策与后续路线见 `docs/development-guide.md`**（架构原则/决策溯源 + P0/P1/P2 开发路线）。

## 对接第三方 LLM（DeepSeek）

Celery worker 通过 `docker run --env` 把 Anthropic 兼容端点凭据注入 `crucible-agent-runner` 容器（容器销毁 env 消失，零落盘）：

```bash
# backend/.env
CLAUDE_AGENT_SDK_ENABLED=true
LLM_BASE_URL=https://api.deepseek.com/anthropic    # DeepSeek 官方端点
LLM_API_KEY=sk-xxx                                  # 你的 DeepSeek API Key
LLM_MODEL=deepseek-v4-flash                         # v4-flash 非思考 / v4-pro 思考
```

配置说明：
- 腾讯云端点：`https://api.lkeap.cloud.tencent.com/anthropic`（模型 `deepseek-v3.2`）
- 旧模型名 `deepseek-chat`/`deepseek-reasoner` 已于 2026-07-24 弃用
- `ClaudeSdkAdapter.build_runner_env()` 自动注入 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `API_TIMEOUT_MS` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` / `PYTHONUNBUFFERED=1` 等环境变量
- 容器内 SDK 通过 `canUseTool` 回调实现白盒审计黑白名单（只允许 `Read` / `Grep` / `Glob` + 受限的 `Bash` 子集：git-read / curl / python）
- 沙箱镜像已精简（仅 Python + claude-agent-sdk + git + curl + tini），无需 Node/npm/chromium
