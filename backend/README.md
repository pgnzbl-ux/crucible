# Crucible 后端

FastAPI API、Celery Worker、PostgreSQL、节点编排。产品说明与全仓启动见仓库根目录 [README.md](../README.md)。

## 职责

- HTTP API（端口 **8010**）：认证、项目资产、代码审计、漏洞线索、审计报告、验证环境与设置
- Celery Worker：认领审计运行并编排。仓库审计先扫描、聚类与 AI 研判，再按线索独立终认；定向验证是可选的次级工作流
- 对象存储：源码缓存、靶场配方、证据、报告、失败节点包（MinIO）

## 启动

仓库根 `.venv`。先拉起基础设施并构建 Agent 镜像（见根 README）。然后：

```bash
cd /path/to/Crucible && source .venv/bin/activate
cd backend
cp .env.example .env          # 首次
pip install -e ".[dev]"
pip install -r requirements.txt

# 终端 1：API
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload

# 终端 2：Worker（必须用这个入口，固定 prefork；缺扫描器二进制和本地 semgrep 规则时写入当前 .venv）
python run_worker.py
```

开发环境空库在 API 启动时按当前模型建表。也可以：

```bash
alembic upgrade head
```

接口文档：[http://localhost:8010/docs](http://localhost:8010/docs)

## 配置

连接串和密钥只写在 `.env`（模板 `.env.example`）。`app/core/config.py` 做类型与校验，不保存数据库 URL。

| 变量 | 含义 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL：`postgresql+asyncpg://crucible:crucible_secret@localhost:5433/crucible` |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis `6380`，db0 事件 / db1 broker / db2 result |
| `S3_ENDPOINT` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` | MinIO；桶名写在 `app/shared/object_store.py` |
| `CLAUDE_AGENT_SDK_ENABLED` | `true` 走真实 Agent，`false` 走 Mock |
| `AUTH_SECRET` | JWT；生产环境必填 |

LLM 端点、Key、模型只由管理员在控制台「设置 → LLM Provider」配置，不要加 `LLM_*` 环境变量；个人验证凭据仍按用户隔离。

同时运行几个任务于控制台「设置」里可改（`python run_worker.py` 固定 prefork）。

产品版本只写在 `pyproject.toml` 的 `[project].version`。`GET /health` 读取该字段。

## 目录

```
backend/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── core/                # 配置、数据库、JWT、Celery、agent-runner
│   ├── contexts/            # identity / task / agent / lab / project / report / settings / finding / discovery
│   └── shared/              # 鉴权、事件、SSE、对象存储
├── alembic/                 # 迁移链：基线 c18a0e9b4d21 + 增量 revision（head 见 .claude/skills/db-migrate）
├── tests/
├── pyproject.toml           # 依赖与产品版本
├── run_worker.py
└── .env.example
```

跨 Context 只走 Service / 事件，不直连对方 repository。

## 测试

```bash
cd backend
pytest tests -k "not smoke"
python tests/smoke_agent_runner.py   # 需要 Docker
python tests/smoke_sse.py
```

`pytest` 进程把 `DATABASE_URL` 覆盖为 sqlite 内存库，不连接 `.env` 里的 PostgreSQL。冒烟脚本按 `.env` 连真实基础设施。
