# 任务并行上限 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 设置页可配同时 running 的验证任务数；Linux prefork 真正并行；无槽任务保持 queued 并 retry。

**Architecture:** `platform_settings` 单行存软上限；worker 用 Redis SET+Lua 先抢槽再 claim；Linux `--pool=prefork`，Windows 仍 solo；Redis `noeviction`。不拆队列。

**Tech Stack:** FastAPI / SQLAlchemy 2.0 / Celery / Redis / React 19 / Ant Design / pytest

**Spec:** `docs/superpowers/specs/2026-08-18-task-concurrency-design.md`

## Global Constraints

- 提交：`type(scope):` 英文，描述主体简体中文；**用户未明确要求则跳过每个 Task 的 Commit 步**
- Windows 提交用 `git commit -m "subject" -m "body"`，不用 HEREDOC
- 后端测试：`d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest <args> -v`（cwd `backend`）
- 规则编号 R1–R12、API path/字段/错误码以 spec 为准，禁止改名
- 不拆 Celery 队列；不用 `--pool=threads`；不改 6 节点编排与 Lab 状态机
- `AGENT_RUNNER_CONCURRENCY_LIMIT` 合法 1–8，默认 4；不新增 `WORKER_CONCURRENCY`
- 新表放 `settings/models.py`；`shared/models.py` 与 `agent/tasks.py` 都要 import 注册
- Alembic 增量接在 `b7e4c2a19f08` 之后；更新 `test_schema_baseline.py` 的 head 断言
- 叠加上面改，不要还原无关 diff

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `infrastructure/docker-compose.yml` | 改 | Redis `noeviction` |
| `backend/run_worker.py` | 改 | win32=solo，其它=prefork+concurrency=硬顶 |
| `backend/app/core/celery_app.py` | 改 | `visibility_timeout` |
| `backend/app/core/config.py` | 改 | 硬顶 1–8 校验 |
| `backend/app/contexts/settings/models.py` | 改 | `PlatformSetting` |
| `backend/app/contexts/settings/{schemas,repository,service,api}.py` | 改 | GET/PUT `/settings/runtime` |
| `backend/app/contexts/agent/task_slots.py` | 新建 | Lua 抢槽/释放/调和 |
| `backend/app/contexts/agent/tasks.py` | 改 | 抢槽→claim；retry；import 模型 |
| `backend/app/contexts/agent/runtime_cleanup.py` | 改 | 巡检锁 + 槽调和 |
| `backend/app/shared/models.py` | 改 | 注册 `PlatformSetting` |
| `backend/alembic/versions/` | 新建 | `platform_settings` |
| `frontend/src/shared/lib/api.ts` | 改 | 类型与 GET/PUT |
| `frontend/src/features/settings/RuntimePanel.tsx` | 新建 | 运行 Tab |
| `frontend/src/pages/SettingsPage.tsx` | 改 | 第三个 Tab |
| 权威文档 | 改 | api-contract、concurrency.md、development-guide、.env.example |

自由度：API 路径/字段/错误码/表列 **低**；面板布局 **中**；Lua 封装函数名与 session 组织 **高**。

---

### Task 1: Redis 淘汰策略 + Worker 池 + 硬顶校验

**Files:**
- Modify: `infrastructure/docker-compose.yml`
- Modify: `backend/run_worker.py`
- Modify: `backend/app/core/celery_app.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/test_config_ssot.py`（若已有相关断言则扩）
- Create: `backend/tests/test_worker_pool.py`
- Create: `backend/tests/test_redis_compose_policy.py`

**Interfaces:**
- Produces: `run_worker.py` 导出 `def worker_argv() -> list[str]`（`__main__` 调用 `worker_main(worker_argv())`），便于测 Windows/Linux 参数
- Produces: `celery_app.conf.broker_transport_options["visibility_timeout"] == max(3600, agent_runner_timeout_seconds + 300)`
- Produces: `Settings.agent_runner_concurrency_limit` 仍默认 4；`<1` 或 `>8` 时 Settings 校验失败

- [ ] **Step 1: 写失败测试**

`backend/tests/test_redis_compose_policy.py`：

```python
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "infrastructure" / "docker-compose.yml"

def test_redis_uses_noeviction():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "noeviction" in text
    assert "allkeys-lru" not in text
```

`backend/tests/test_worker_pool.py`：

```python
from unittest.mock import patch
from app.core.config import get_settings

def test_windows_worker_is_solo():
    from run_worker import worker_argv
    with patch("sys.platform", "win32"):
        argv = worker_argv()
    assert "--pool=solo" in argv
    assert "--concurrency=2" not in argv

def test_linux_worker_is_prefork_with_hard_cap():
    from run_worker import worker_argv
    cap = get_settings().agent_runner_concurrency_limit
    with patch("sys.platform", "linux"):
        argv = worker_argv()
    assert "--pool=prefork" in argv
    assert f"--concurrency={cap}" in argv
```

`test_config_ssot.py` 增：构造 Settings 时 `AGENT_RUNNER_CONCURRENCY_LIMIT=0` 或 `9` 抛 `ValidationError`（其它必填 env 用 monkeypatch 补齐，仿现有 `test_settings_require_env_for_infra_urls`）。

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv\Scripts\python.exe -m pytest tests/test_redis_compose_policy.py tests/test_worker_pool.py tests/test_config_ssot.py -v
```

Expected: FAIL（仍是 allkeys-lru / 无 `worker_argv` / 硬顶无 1–8 校验）

- [ ] **Step 3: 最小实现**

- compose Redis `command`: `redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy noeviction`
- `config.py`：`agent_runner_concurrency_limit: int = Field(4, ge=1, le=8)`（`pydantic` Field）
- `celery_app.py`：`broker_transport_options={"visibility_timeout": max(3600, settings.agent_runner_timeout_seconds + 300)}`
- `run_worker.py`：

```python
def worker_argv() -> list[str]:
    from app.core.config import get_settings
    cap = get_settings().agent_runner_concurrency_limit
    if sys.platform == "win32":
        return ["worker", "--loglevel=info", "--pool=solo"]
    return ["worker", "--loglevel=info", "--pool=prefork", f"--concurrency={cap}"]
```

- [ ] **Step 4: 跑测试确认通过**

同 Step 2，Expected: PASS

---

### Task 2: `platform_settings` + GET/PUT `/settings/runtime`

**Files:**
- Modify: `backend/app/contexts/settings/models.py`
- Modify: `backend/app/contexts/settings/schemas.py`
- Modify: `backend/app/contexts/settings/repository.py`
- Modify: `backend/app/contexts/settings/service.py`
- Modify: `backend/app/contexts/settings/api.py`
- Modify: `backend/app/shared/models.py`
- Modify: `backend/app/contexts/agent/tasks.py`（仅 import `PlatformSetting` 注册 metadata）
- Create: `backend/alembic/versions/<rev>_add_platform_settings.py`（autogenerate 后人工 review）
- Modify: `backend/tests/test_schema_baseline.py`
- Create: `backend/tests/test_runtime_settings.py`

**Interfaces:**
- Produces: `class PlatformSetting` 表 `platform_settings`，列 `singleton_key` UNIQUE 默认 `"default"`，`max_concurrent_tasks` Integer NOT NULL default 1
- Produces: `RuntimeSettingsResponse(max_concurrent_tasks: int, max_allowed: int, worker_pool: Literal["solo","prefork"])`
- Produces: `RuntimeSettingsUpdateRequest(max_concurrent_tasks: int)` extra=forbid
- Produces: `SettingsService.get_runtime_settings() -> RuntimeSettingsResponse`（无行则插入 1）
- Produces: `SettingsService.update_runtime_settings(n: int) -> RuntimeSettingsResponse`
- Produces: `def worker_pool_hint() -> str`：`win32`→`solo` 否则 `prefork`
- API：`GET/PUT /api/v1/settings/runtime`，JWT 与现设置路由相同

- [ ] **Step 1: 写失败测试** `backend/tests/test_runtime_settings.py`

表格驱动校验 + get-or-create。沿用 `test_worker_claim.py` 的 sqlite memory fixture，import 全模型 `create_all`。

```python
@pytest.mark.asyncio
async def test_get_runtime_inserts_default_one(session_factory):
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService
    async with session_factory() as session:
        svc = SettingsService(SettingsRepository(session))
        got = await svc.get_runtime_settings()
        assert got.max_concurrent_tasks == 1
        again = await svc.get_runtime_settings()
        assert again.max_concurrent_tasks == 1
        # 只有一行
        from sqlalchemy import select, func
        from app.contexts.settings.models import PlatformSetting
        n = (await session.execute(select(func.count()).select_from(PlatformSetting))).scalar()
        assert n == 1

@pytest.mark.parametrize("value", [0, 9, -1])
def test_update_request_rejects_out_of_range(value):
    from pydantic import ValidationError
    from app.contexts.settings.schemas import RuntimeSettingsUpdateRequest
    with pytest.raises(ValidationError):
        RuntimeSettingsUpdateRequest(max_concurrent_tasks=value)
```

API 层用现有 settings 测试风格（若无 httpx 设置测，service 测足够；再补一份 ASGI 测 GET 401 无 token / PUT 2 返回 `max_allowed`）。

`test_schema_baseline.py`：`assert "platform_settings" in insp.get_table_names()`；列含 `singleton_key`、`max_concurrent_tasks`。Alembic 链：新 revision `down_revision == "b7e4c2a19f08"`，`_alembic_head()` 等于新 revision id。

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv\Scripts\python.exe -m pytest tests/test_runtime_settings.py tests/test_schema_baseline.py -v
```

Expected: FAIL（无模型 / 无路由）

- [ ] **Step 3: 实现模型、get-or-create、API**

- PUT 越界必须走 **422 `VALIDATION_FAILED`**。仓库里裸 `ValueError` 会被打成 400，禁止用它表达越界。做法：`RuntimeSettingsUpdateRequest` 用 Pydantic `Field(ge=1)` + `model_validator` 读取 `get_settings().agent_runner_concurrency_limit`，超出则 `raise ValueError("...")`（发生在 schema 校验阶段 → `RequestValidationError` → 422）。不要在 service 里再抛 ValueError。
- get-or-create 必须防并发双插：捕获 `IntegrityError` 后按 `singleton_key=="default"` 再读。
- `cd backend && alembic revision --autogenerate -m "add_platform_settings"`，review 后只含 `platform_settings` 一张表。

- [ ] **Step 4: 跑测试确认通过**

同 Step 2，Expected: PASS

---

### Task 3: Redis 运行槽 + worker 准入 + 巡检调和

**Files:**
- Create: `backend/app/contexts/agent/task_slots.py`
- Create: `backend/tests/test_task_slots.py`
- Modify: `backend/app/contexts/agent/tasks.py`
- Modify: `backend/app/contexts/agent/runtime_cleanup.py`
- Modify: `backend/tests/test_worker_claim.py` 或新建 `backend/tests/test_run_analysis_slots.py`

**Interfaces:**
- Produces（`task_slots.py`，async Redis，连接 **`settings.redis_url`（db0）**，与事件总线同库；**禁止**写到 broker db1。key 名必须是 spec 的两个）：
  - `SLOT_SET_KEY = "crucible:running_run_ids"`
  - `SWEEPER_LOCK_KEY = "crucible:runtime_sweeper"`
  - `async def try_acquire_slot(run_id: str, max_slots: int) -> bool`
  - `async def release_slot(run_id: str) -> None`
  - `async def reconcile_slots(running_run_ids: set[str]) -> None`
  - `async def try_sweeper_lock() -> bool`（`SET NX EX 240`，True=拿到锁）
- `try_acquire_slot` 用 Lua：已是 member → True；`SCARD>=max` → False；否则 `SADD` → True。`max_slots<1` 视为 1（防御，正常路径不会发生）
- `run_analysis`：不可认领则 return；`try_acquire_slot` 失败则 `raise self.retry(countdown=15)` 且 **task 装饰器 `max_retries=None`**；成功才 `claim_task_run`；claim 失败 `release_slot`；`finally` 仅当 `acquired` 释放
- 禁止在等槽时 `asyncio.sleep` 占进程
- `start_background_sweeper` / `sweep_all_runtimes`：先 `try_sweeper_lock`，失败 skip；成功后在现有孤儿扫描之外调用 `reconcile_slots`（running 的 run_id 集合来自 DB `TaskRun.status=="running"`）

- [ ] **Step 1: 写失败测试** `backend/tests/test_task_slots.py`

用 fakeredis 或内存 fake。若仓库无 fakeredis，写可注入的 Redis 协议替身（`eval`/`sadd`/`srem`/`scard`/`sismember`/`smembers`/`set`）挂到模块，**不要**为测槽去连 6380。

表格：

| max | 已有 members | 申请 run | 期望 |
|---|---|---|---|
| 1 | {} | r1 | True，set={r1} |
| 1 | {r1} | r2 | False，set={r1} |
| 1 | {r1} | r1 | True，set={r1}（幂等） |
| 2 | {r1} | r2 | True，set={r1,r2} |

`reconcile_slots({r2})` 在 set={r1,r2} 之后 → set={r2}；DB running 有 r3 不在 set → 补上 r3。

`test_run_analysis_slots.py`：mock `try_acquire_slot` 返回 False 时，`run_analysis` 抛 `Retry`（或 celery `retry`），且 DB 任务仍为 `queued`。mock True 时才进入 claim。用 `celery_app.conf.task_always_eager` 时 retry 行为要核对：eager 下 `retry` 可能立即重入——该测应用直接调 `_run_analysis` 前的同步包装，或对 `self.retry` patch 成抛自定义异常再断言。

- [ ] **Step 2: 跑测试确认失败**

```
cd backend && .venv\Scripts\python.exe -m pytest tests/test_task_slots.py tests/test_run_analysis_slots.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 Lua + 接入 `run_analysis` + 巡检**

`@celery_app.task(bind=True, name="agent.run_analysis", max_retries=None)` 保持 `acks_late` 全局配置。

伪序（实现须等价 spec §6）：

```python
acquired = False
try:
    async with session_factory() as session:
        task = await _load_task(...)
        if 不可认领: return
        max_n = await SettingsService(...).get_runtime_settings()
    if not await try_acquire_slot(run_id, max_n.max_concurrent_tasks):
        raise self.retry(countdown=15)
    acquired = True
    # 原 claim + 编排
finally:
    if acquired:
        await release_slot(run_id)
```

`get_runtime_settings` 会 get-or-create，worker 无行时也不会全拒绝（R3）。

Redis 异常：向上抛，让本轮 Celery 失败（可再由 worker 丢失重投）；禁止吞掉后继续 claim。

- [ ] **Step 4: 跑测试确认通过**

```
cd backend && .venv\Scripts\python.exe -m pytest tests/test_task_slots.py tests/test_run_analysis_slots.py tests/test_worker_claim.py tests/test_runtime_cleanup.py -v
```

Expected: PASS（旧认领/巡检回归）

---

### Task 4: 设置页「运行」Tab

**Files:**
- Modify: `frontend/src/shared/lib/api.ts`
- Create: `frontend/src/features/settings/RuntimePanel.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Create: `frontend/src/features/settings/RuntimePanel.test.tsx`（若前端已有 vitest；没有则只保证 `tsc --noEmit`，不要新加测试框架）

**Interfaces:**
- Produces: `type RuntimeSettings = { max_concurrent_tasks: number; max_allowed: number; worker_pool: 'solo' | 'prefork' }`
- Produces: `api.getRuntimeSettings()` → GET `/settings/runtime`
- Produces: `api.updateRuntimeSettings(data: { max_concurrent_tasks: number })` → PUT `/settings/runtime`

- [ ] **Step 1: 加类型与 API 客户端**
- [ ] **Step 2: `RuntimePanel`**
  - `useQuery` 拉 GET；`useMutation` PUT 成功后 `invalidateQueries`
  - `InputNumber` min=1 max=`max_allowed`
  - `worker_pool === 'solo'` 时禁用输入与保存，并用页面内 `Alert`：同时运行任务数仅 Linux 可配置；Windows 同一时刻只跑 1 个任务，把数字调到 2 也不会并行
  - 说明：立即作用于排队任务；不中断已 running
  - 错误走 `useErrorToast`
- [ ] **Step 3: SettingsPage 第三个 Tab `key: 'runtime', label: '运行'`**；PageHeader subtitle 改为覆盖模型、凭据、并行
- [ ] **Step 4: 验证**

```
cd frontend && npx tsc --noEmit
```

Expected: 无错误。若有现成 vitest 文件模式，补一条「solo 时渲染 Alert」的测。

---

### Task 5: 权威文档同步

**Files:**
- Modify: `.claude/api-contract.md`
- Modify: `.claude/rules/concurrency.md` 与 `.cursor/rules/concurrency.mdc`（只改与本功能冲突的条目，保持短摘录）
- Modify: `docs/development-guide.md`
- Modify: `backend/.env.example`
- Modify: `docs/superpowers/specs/2026-08-18-task-concurrency-design.md` 状态行：实现合并前保持「待确认」；**本 Task 不要改成已确认，除非用户已审 spec 且代码已按它落地**

文档必须写明：

- GET/PUT `/api/v1/settings/runtime` 字段与 422
- Linux prefork / Windows solo
- 先抢槽再 claim；retry 15s；key `crucible:running_run_ids`
- Redis `noeviction`
- `AGENT_RUNNER_CONCURRENCY_LIMIT` = 设置上限 + Linux 进程数（1–8）

- [ ] **Step 1: 改文档**
- [ ] **Step 2: 全文搜 `allkeys-lru`、`--concurrency=2`、`单 worker 单任务`，过时表述改掉或加「仅 Windows solo」**

```
rg "allkeys-lru|--concurrency=2|prefetch_multiplier=1：单 worker 单任务" 
```

Expected: 权威文档不再把 solo 写成全平台唯一模型。

- [ ] **Step 3: 回归**

```
cd backend && .venv\Scripts\python.exe -m pytest tests/test_runtime_settings.py tests/test_task_slots.py tests/test_run_analysis_slots.py tests/test_worker_pool.py tests/test_redis_compose_policy.py tests/test_schema_baseline.py tests/test_worker_claim.py tests/test_config_ssot.py -q
cd frontend && npx tsc --noEmit
```

Expected: PASS

---

## 验证对照 spec 验收

| 验收 | 主要由 |
|---|---|
| A1 默认 1 | Task 2 |
| A2 两个 running 一个 queued | Task 3（集成/手工 Linux）；单测满员拒绝 |
| A3 Lab 复用 | 不改代码；回归 `tests/test_lab_acquire.py` 若时间够可跑 |
| A4/A5 升降上限 | Task 3 行为 + 手工 |
| A6 取消 queued | 现 cancel 契约 + 不可认领不 claim |
| A7 422 | Task 2 |
| A8 Windows Alert | Task 4 |
| A9 noeviction | Task 1 |
| A10 Lua/幂等 | Task 3 |

## 回滚

- 代码回退；`alembic downgrade -1` 删 `platform_settings`（需用户确认生产库操作）
- `DEL crucible:running_run_ids`
- compose 策略回 LRU 会恢复误杀风险，回滚代码时一并考虑

## 自检

- spec R1–R12 均有 Task：R1–R9→T2/T3，R10 明确不改 Lab，R11→全局约束，R12→T1
- 无 TBD；API 名与 spec 一致
- 未解决问题：无
