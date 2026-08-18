---
paths: ["backend/app/**/*.py", "backend/tests/**/*.py"]
---

# Crucible 测试规范

> 当前测试基线含 smoke_agent_runner.py / smoke_sse.py + pytest 单元测试(test_orchestrator/test_ai_runner/test_profile_detector 等)；pytest 单元测试为 P1 backlog。

## 1. 测试层级与时机

| 层级 | 范围 | 工具 | 速度要求 |
|---|---|---|---|
| 单元 | Repository / Service / Schemas | pytest + pytest-asyncio | < 100ms / case |
| 集成 | API 端点 + SQLite in-memory（`tests/conftest.py` 覆盖 `DATABASE_URL`）+ 内存 Redis fake | httpx.AsyncClient + ASGITransport | < 1s / case |
| Celery | `agent/tasks.py` 工作流（`task_always_eager=True`） | pytest-celery | < 5s / case |
| 端到端冒烟 | 全链路（创建任务 → 沙箱 → Agent mock → 报告） | 脚本（参考 `smoke_sandbox.py`） | < 30s |
| 沙箱冒烟 | 真实 Docker：sandbox 创建 / exec / OOM / 网络 / 清理 | `tests/smoke_sandbox.py` | < 60s |

**任何 P0 改动都要补至少一个端到端冒烟**。

## 2. Mock 边界

- **必须 Mock**：LLM API（外部依赖、不稳定）、Redis Pub/Sub（集成测试用 fake）、MinIO（S3 fake）
- **必须真实**：pytest 用 SQLite（禁止连 `.env` 的 PostgreSQL）；Docker 沙箱（smoke）
- Agent 默认走 Mock,`CLAUDE_AGENT_SDK_ENABLED=true` 时切换真实 Executor(6 节点编排)

## 3. 测试数据

- 每个测试自建数据，不共享 fixture state（除非显式 `scope="session"`）
- 用户密码用 `bcrypt.hashpw` 生成，**不复用**硬编码串
- Fernet key 测试用临时生成的，不复用 dev 派生 key

## 4. 沙箱测试特别要求（参考现有 `smoke_sandbox.py`）

- 每个 case 用唯一标签前缀（如 `test-{uuid}）` 避免冲突
- `try/finally` 强制清理容器，case 失败也要清理
- 断言包含：退出码、stdout 关键字、容器状态（`running` → `exited`）
- OOM 测试用 `docker inspect` 看 `State.OOMKilled`

## 5. CI 期望（待 P2-14 接入）

- `pytest -x --cov=app --cov-report=term-missing`
- `ruff check app tests`
- 前端 `tsc --noEmit && npm run build`
- 必须 `ENVIRONMENT=test` 运行（不走生产校验）

## 6. 已知待补

- [ ] pytest 全量单元测试（docs §4 P1 backlog）
- [ ] Celery 任务 e2e
- [ ] RBAC 权限矩阵测试（待 P1-9）
- [ ] OIDC 集成测试（待 P1-8）