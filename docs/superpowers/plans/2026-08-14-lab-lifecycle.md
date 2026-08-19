# 靶场生命周期与管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 同一用户 + 同一项目 + 同一 commit 共用一套靶场；任务结束不拆；静默 1 小时才销毁；独立管理页按项目分组操作容器。

**Architecture:** 新 Bounded Context `lab`（`labs` 表 + `LabService`）。task / agent 只调 Service，不直连 repository。Compose 项目名改为 `crucible-lab-{lab_id}`。AI 仍把配方写在任务 workspace 的 `{repo}/.vuln-env/`（bind mount 限制），创建者在 `compose up` 前把该目录拷到 `{agent_runner_workdir_base}/labs/{lab_id}/`。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 Async / Alembic / Celery / React 19 / Ant Design 6 / Vitest

**Spec:** `docs/superpowers/specs/2026-08-14-lab-lifecycle-design.md`

## Global Constraints

- 提交：`type(scope):` 英文，描述主体简体中文；用户未要求则执行者仍按本计划逐步 commit
- `BaseModel` 已提供 UUID + `created_at`/`updated_at`，`Lab` 直接继承
- 禁止跨 Context ORM relationship；`labs.creator_task_id` 只存字符串，不建指向 `tasks` 的 FK（避免与 `tasks.lab_id` 环）
- SQLite 开发：acquire 的 CAS 用 `UPDATE ... WHERE status IN (...)` + 唯一约束，不用 `SELECT FOR UPDATE`
- 409 占用：`HTTPException(409, detail={"code":"LAB_IN_USE","message":"...","task_ids":[...]})`
- Mock（`claude_agent_sdk_enabled=false`）不写 `labs` 行、不碰 Docker
- TTL 写死 3600，不做设置页
- 路径用正斜杠；Windows 下 compose `-f` 仍把 `\` 换成 `/`

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/contexts/lab/__init__.py` | 新建 | context 包 |
| `backend/app/contexts/lab/models.py` | 新建 | `Lab` ORM |
| `backend/app/contexts/lab/repository.py` | 新建 | 按复用键查/CAS |
| `backend/app/contexts/lab/service.py` | 新建 | acquire / touch / expire / 管理操作 |
| `backend/app/contexts/lab/schemas.py` | 新建 | API schema |
| `backend/app/contexts/lab/api.py` | 新建 | `/api/v1/labs` |
| `backend/app/contexts/lab/docker_ops.py` | 新建 | compose stop/start/ps、单容器操作 |
| `backend/app/contexts/lab/errors.py` | 新建 | `LabBusyError` / `LabNotFoundError` |
| `backend/app/contexts/task/models.py` | 改 | `tasks.lab_id` |
| `backend/app/contexts/agent/nodes/env_ready.py` | 改 | acquire、lab workdir、`-p lab_id` |
| `backend/app/contexts/agent/nodes/reproduce.py` | 改 | `touch(lab_id)` |
| `backend/app/contexts/agent/runtime_cleanup.py` | 改 | 任务结束只拆 runner；TTL 拆靶场 |
| `backend/app/contexts/agent/tasks.py` | 改 | import Lab；finally 不 down 靶场 |
| `backend/app/contexts/task/service.py` | 改 | cancel 标 lab failed；删除不 down 靶场 |
| `backend/app/core/database.py` | 改 | SQLite 补 `tasks.lab_id` |
| `backend/alembic/env.py` | 改 | import Lab |
| `backend/alembic/versions/<rev>_add_labs.py` | 新建 | 迁移 |
| `backend/app/main.py` | 改 | 挂 lab router |
| `frontend/src/features/lab/LabStacks.tsx` | 新建 | 按项目分组的靶场列表 |
| `frontend/src/pages/LabsPage.tsx` | 新建 | 页面壳 |
| `frontend/src/App.tsx` / `layout.tsx` / `routes.ts` / `api.ts` | 改 | 路由、菜单、客户端 |
| 权威文档 | 改 | api-contract、编排 spec、env-builder |

---

### Task 1: Lab ORM + `tasks.lab_id` + 迁移

**Files:**
- Create: `backend/app/contexts/lab/__init__.py`
- Create: `backend/app/contexts/lab/models.py`
- Modify: `backend/app/contexts/task/models.py`（`Task` 增加 `lab_id`）
- Modify: `backend/alembic/env.py`
- Modify: `backend/app/contexts/agent/tasks.py`（import `Lab` 注册 metadata）
- Modify: `backend/app/core/database.py`（`_ensure_dev_columns` 补 `tasks.lab_id`）
- Create: `backend/alembic/versions/<rev>_add_labs_and_task_lab_id.py`（autogenerate 后人工 review）
- Test: `backend/tests/test_lab_model.py`

**Interfaces:**
- Produces: `Lab` 表 `labs`；`lab_project_name_from_id(lab_id) -> str` 约定为 `crucible-lab-{lab_id.lower()}`（实现放 Task 2 的 runtime 或本模型旁的纯函数，本任务先把列建对）。
- Produces: `Task.lab_id: str | None`，`ForeignKey("labs.id")`，无 relationship。

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_lab_model.py`:

```python
"""Lab 表字段、唯一约束、tasks.lab_id。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_labs_table_columns():
    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.project.models import Project  # noqa: F401
    from app.contexts.lab.models import Lab  # noqa: F401
    from app.contexts.task.models import Task  # noqa: F401
    from app.shared.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("labs")}
    expected = {
        "id", "owner_id", "project_id", "commit_sha", "status",
        "compose_project", "workdir", "target_url", "compose_path",
        "transport_shape", "initial_creds", "last_seen_at", "ttl_seconds",
        "creator_task_id", "error_message", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"缺字段: {expected - cols}"
    task_cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert "lab_id" in task_cols


def test_labs_unique_owner_project_sha():
    from app.contexts.identity.models import User  # noqa: F401
    from app.contexts.project.models import Project  # noqa: F401
    from app.contexts.lab.models import Lab
    from app.contexts.task.models import Task  # noqa: F401
    from app.shared.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        kwargs = dict(
            owner_id="u1", project_id="p1", commit_sha="a" * 40,
            status="creating", compose_project="crucible-lab-x",
            workdir="/tmp/labs/x", ttl_seconds=3600,
        )
        s.add(Lab(**kwargs))
        s.commit()
        s.add(Lab(**kwargs, id="other"))
        try:
            s.commit()
            raise AssertionError("应拒绝重复 owner+project+sha")
        except IntegrityError:
            s.rollback()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_lab_model.py -v`

Expected: `ImportError: cannot import name 'Lab'`

- [ ] **Step 3: 最小实现**

`backend/app/contexts/lab/__init__.py` 空文件或 `"""Lab 靶场生命周期。"""`。

`models.py`：`Lab(BaseModel)`，`__tablename__ = "labs"`，字段与测试 `expected` 一致。`status` 默认 `"creating"`；`ttl_seconds` 默认 `3600`；`transport_shape`/`initial_creds` 用 `Text` 存 JSON 字符串，默认 `"{}"`。`UniqueConstraint("owner_id", "project_id", "commit_sha", name="uq_labs_owner_project_sha")`。`owner_id`/`project_id` 用 `String(36)` + `ForeignKey("users.id")` / `ForeignKey("projects.id")`，**不要** relationship。

`Task` 增加：

```python
lab_id: Mapped[str | None] = mapped_column(
    String(36), ForeignKey("labs.id"), index=True,
    comment="共用靶场 Lab",
)
```

`alembic/env.py` 增加 `from app.contexts.lab.models import Lab`。

`tasks.py` 增加 `from app.contexts.lab.models import Lab  # noqa: F401`。

`database.py` `_ensure_dev_columns`：若 `tasks` 存在且无 `lab_id`，`ALTER TABLE tasks ADD COLUMN lab_id VARCHAR(36)`。

然后：

```bash
cd backend && alembic revision --autogenerate -m "add labs table and tasks.lab_id"
```

人工确认 upgrade 含 `labs` 表、唯一约束、`tasks.lab_id`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_lab_model.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/contexts/lab backend/app/contexts/task/models.py backend/alembic backend/app/core/database.py backend/app/contexts/agent/tasks.py backend/tests/test_lab_model.py
git commit -m "$(cat <<'EOF'
feat(lab): 增加 labs 表与 tasks.lab_id

同一用户、项目、commit 共用靶场需要持久化状态，不能再绑在 task_id 上。
EOF
)"
```

---

### Task 2: LabService.acquire / touch / 占用查询（纯库，不碰 Docker）

**Files:**
- Create: `backend/app/contexts/lab/repository.py`
- Create: `backend/app/contexts/lab/service.py`
- Test: `backend/tests/test_lab_acquire.py`

**Interfaces:**
- Consumes: `Lab` 模型；`Task.lab_id` / `Task.status`
- Produces:

```python
@dataclass(frozen=True)
class AcquireResult:
    lab_id: str
    role: str  # "create" | "wait" | "reuse" | "start"
    status: str
    workdir: str
    compose_project: str
    target_url: str | None
    compose_path: str | None
    transport_shape: dict
    initial_creds: dict
    reused: bool  # role in {"reuse", "start"}

class LabService:
    def __init__(self, session: AsyncSession) -> None: ...
    async def acquire(
        self, *, owner_id: str, project_id: str, commit_sha: str, task_id: str
    ) -> AcquireResult: ...
    async def touch(self, lab_id: str) -> None: ...
    async def mark_ready(self, lab_id: str, *, target_url: str, compose_path: str,
                         transport_shape: dict, initial_creds: dict) -> None: ...
    async def mark_failed(self, lab_id: str, error: str) -> None: ...
    async def mark_creator_cancelled(self, task_id: str) -> None: ...
    async def bind_task(self, task_id: str, lab_id: str) -> None: ...
    async def live_task_ids(self, lab_id: str) -> list[str]: ...
```

`LIVE_TASK_STATUSES = frozenset({"pending", "queued", "running"})`。

`workdir` = `{get_settings().agent_runner_workdir_base}/labs/{lab_id}`（统一 `/`）。

`compose_project` = `crucible-lab-{lab_id.lower()}`。

acquire 规则（必须按这次序）：

1. `bind_task` 在返回前执行（等待者也写 `tasks.lab_id`）。
2. 无行：insert `creating`，`role="create"`，`creator_task_id=task_id`，`touch`。
3. 命中 `failed|expired|destroyed`：`UPDATE labs SET status='creating', creator_task_id=:tid WHERE id=:id AND status IN (...)`；rowcount=1 则 `create`，否则重读再走下面分支。
4. `creating` 且 `creator_task_id==task_id`：`create`（创建者重入）。
5. `creating` 且创建者是别人：`wait`。
6. `ready`：`reuse`，`touch`。
7. `stopped`：`start`，`touch`（本任务稍后 `compose start`，本任务不改 docker）。
8. insert 撞唯一约束：rollback 后按 id 重读，走 4–7。

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_lab_acquire.py`，session fixture 抄 `test_project_api.py`（额外 import `Lab`）。文件顶部放种子（后续 TTL/API 测试原样复制这段，不要 import 测试模块）：

```python
SHA = "a" * 40

async def seed(session, *, task_id="t1", status="running"):
    from app.contexts.identity.models import User
    from app.contexts.project.models import Project
    from app.contexts.task.models import Task
    if await session.get(User, "u1") is None:
        session.add(User(
            id="u1", email="u1@x.test", password_hash="x", display_name="u1",
        ))
        session.add(Project(id="p1", name="demo", git_url="https://github.com/a/b", owner_id="u1"))
    if await session.get(Task, task_id) is None:
        session.add(Task(
            id=task_id, project_address="https://github.com/a/b",
            vulnerability_description="d", owner_id="u1", project_id="p1",
            status=status,
        ))
    await session.commit()
```

关键用例：

```python
@pytest.mark.asyncio
async def test_second_task_waits_while_creating(session):
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task
    await seed(session, task_id="t1")
    await seed(session, task_id="t2")
    svc = LabService(session)
    a = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1")
    b = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t2")
    assert a.role == "create" and a.lab_id == b.lab_id
    assert b.role == "wait" and b.reused is False
    t2 = await session.get(Task, "t2")
    assert t2.lab_id == a.lab_id


@pytest.mark.asyncio
async def test_ready_lab_is_reused(session):
    from app.contexts.lab.service import LabService
    await seed(session, task_id="t1")
    await seed(session, task_id="t2")
    svc = LabService(session)
    a = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1")
    await svc.mark_ready(
        a.lab_id, target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    b = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t2")
    assert b.role == "reuse" and b.reused is True
    assert b.target_url == "http://10.0.0.8:3001"


@pytest.mark.asyncio
async def test_failed_lab_can_be_reclaimed(session):
    from app.contexts.lab.service import LabService
    await seed(session, task_id="t1")
    await seed(session, task_id="t2")
    svc = LabService(session)
    a = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1")
    await svc.mark_failed(a.lab_id, "boom")
    b = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t2")
    assert b.role == "create" and b.lab_id == a.lab_id


@pytest.mark.asyncio
async def test_cancel_creator_marks_failed(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService
    await seed(session, task_id="t1")
    svc = LabService(session)
    a = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1")
    await svc.mark_creator_cancelled("t1")
    lab = await session.get(Lab, a.lab_id)
    assert lab.status == "failed"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_lab_acquire.py -v`

Expected: `ImportError` 或 `LabService` 不存在

- [ ] **Step 3: 实现 repository + service**

repository：`get(id)`、`get_by_key(owner_id, project_id, commit_sha)`、`add(Lab)`、`cas_reclaim(id, from_statuses, creator_task_id)`。

service 实现上面规则；JSON 用 `json.loads`/`dumps`，坏 JSON 当 `{}`。`mark_creator_cancelled`：若存在 lab 且 `status==creating` 且 `creator_task_id==task_id` → `failed`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_lab_acquire.py tests/test_lab_model.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/contexts/lab backend/tests/test_lab_acquire.py
git commit -m "$(cat <<'EOF'
feat(lab): 实现 acquire 创建/等待/复用

第二任务必须看见 creating 并写入 lab_id，否则 TTL 会在排队时误拆。
EOF
)"
```

---

### Task 3: 任务结束 / 取消 / 删除不再拆靶场

**Files:**
- Modify: `backend/app/contexts/agent/runtime_cleanup.py`
- Modify: `backend/app/contexts/agent/tasks.py`（`finally` 仍拆 runner，去掉对 lab compose down 的依赖——改 `teardown_task_runtime` 即可）
- Modify: `backend/app/contexts/task/service.py`（cancel 调用 `mark_creator_cancelled`）
- Modify: `backend/tests/test_runtime_cleanup.py`
- Test: `backend/tests/test_lab_teardown.py`（可与 runtime 测试合并）

**Interfaces:**
- Consumes: `LabService.mark_creator_cancelled`
- Produces: `teardown_task_runtime(task_id)` **只** `agent_runner_manager.remove_for_task`，**不**再 `docker_compose_down`。
- 巡检 `sweep_orphan_runtimes` 仍拆已结束任务的 **runner**；compose 项目名 `crucible-lab-*` 不再当成 task_id 去 down（Task 5 接 TTL）。本任务先改：`collect_task_ids_from_containers` **不要**把 `com.docker.compose.project` 的 `crucible-lab-{uuid}` 当成 task_id（否则会拿 lab_id 去拆 runner）。只认标签 `crucible.task_id` 与 host_workdir 路径。

- [ ] **Step 1: 改失败测试（先改断言再改实现）**

把 `test_teardown_task_runtime_downs_lab_and_runners` 改名为行为断言：

```python
@pytest.mark.asyncio
async def test_teardown_task_runtime_only_removes_agent_runner():
    from app.contexts.agent.runtime_cleanup import teardown_task_runtime

    with patch("app.contexts.agent.runtime_cleanup.agent_runner_manager") as mock_mgr, patch(
        "app.contexts.agent.nodes.env_ready.docker_compose_down", new_callable=AsyncMock,
    ) as mock_down:
        mock_mgr.host_workdir_path.return_value = "/tmp/audit-abc"
        await teardown_task_runtime("abc")
    mock_mgr.remove_for_task.assert_called_once()
    mock_down.assert_not_awaited()
```

同步改 `test_teardown_kills_runner_before_compose_down`：不再要求 compose down。

`test_lab_project_name_is_stable_and_prefixed` 仍可保留函数（id 进、小写项目名出），参数语义改为 lab_id，断言不变。

增加：

```python
def test_collect_ids_ignores_lab_compose_project():
    from types import SimpleNamespace
    from app.contexts.agent.runtime_cleanup import collect_task_ids_from_containers

    c = SimpleNamespace(
        labels={
            "com.docker.compose.project": "crucible-lab-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        },
        attrs={"Mounts": []},
    )
    assert collect_task_ids_from_containers([c], "/tmp/crucible/audit") == set()
```

仍收集 `crucible.task_id` 标签。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_runtime_cleanup.py -v`

Expected: `mock_down.assert_not_awaited` 失败（现在还会 down）

- [ ] **Step 3: 实现**

`teardown_task_runtime` 删除 `docker_compose_down` 调用。

`collect_task_ids_from_containers` 去掉 `task_id_from_lab_project(...)` 那一段。

`cancel_task` 在标 cancelled 之后、`schedule_teardown_task_runtime` 之前：

```python
from app.contexts.lab.service import LabService
await LabService(self.session).mark_creator_cancelled(task_id)
```

（`TaskService` 已有 session。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_runtime_cleanup.py tests/test_lab_acquire.py tests/test_task_retry_delete.py -v`

Expected: PASS（retry/delete 仍 mock teardown，行为兼容）

- [ ] **Step 5: Commit**

```bash
git add backend/app/contexts/agent/runtime_cleanup.py backend/app/contexts/task/service.py backend/tests/test_runtime_cleanup.py
git commit -m "$(cat <<'EOF'
fix(lab): 任务结束只拆 agent-runner 不拆靶场

多任务复用同一套 compose，按 task_id down 会把别人的靶场拆掉。
EOF
)"
```

---

### Task 4: env_ready 接入 acquire（创建 / 等待 / 复用）

**Files:**
- Modify: `backend/app/contexts/agent/nodes/env_ready.py`
- Modify: `backend/app/contexts/agent/nodes/reproduce.py`
- Modify: `backend/app/contexts/agent/nodes/base.py`（`NodeContext.lab_id: str | None = None`）
- Modify: `backend/tests/test_node_env_ready.py`
- Modify: `backend/tests/test_compose_path.py`（`-p` 改为 lab_id）
- Test: `backend/tests/test_env_ready_lab_reuse.py`

**Interfaces:**
- Consumes: `LabService.acquire/mark_ready/mark_failed/touch`；`ctx.db_session`、`ctx.owner_id`、`ctx.project_id`
- Produces: 创建者 `docker_compose_up(..., lab_id=result.lab_id)` 且 compose 文件在 `result.workdir`；复用者不调 `run_ai_turn` / `docker_compose_up`，产出带 `reused: true`
- `sync_recipe_to_lab(src_repo_dir: str, lab_workdir: str) -> None`：把 `{src_repo_dir}/.vuln-env` 拷到 `{lab_workdir}/.vuln-env`（存在则覆盖）
- `_compose_project_args(lab_id)` 用 lab_id，不再用 task_id
- 端口占用 `exclude_project` 用 `crucible-lab-{lab_id}`
- Mock SDK：保持现有直接返回，**不** acquire
- `project_id` 为空：`ProjectService.upsert_by_git_url(ctx.project_address, ctx.owner_id)`，失败则 `RuntimeError`
- 等待：每 2s `acquire` 或 `get`；`ready` → 复用产出；`failed` → 再 acquire 一次转 create；超时 `get_settings().agent_runner_timeout_seconds`
- `stopped` + role `start`：`docker_ops.compose_start(compose_project)` 成功后 `mark_ready`（url 沿用库里的）或失败 `mark_failed`
- reproduce：`if ctx.lab_id: await LabService(ctx.db_session).touch(ctx.lab_id)`；orchestrator 把 `task.lab_id` 填进 `NodeContext.lab_id`

配方拷贝必须在 AI `submit_result` 之后、`compose up` 之前，因为 runner 只能写任务 workspace。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_env_ready_lab_reuse.py`：

```python
@pytest.mark.asyncio
async def test_env_ready_reuses_ready_lab_without_compose_up(tmp_path):
    from app.contexts.agent.nodes.env_ready import EnvReadyNode
    from app.contexts.agent.nodes.base import NodeContext

    lab = SimpleNamespace(
        lab_id="lab1", role="reuse", status="ready", reused=True,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001", compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    ctx = NodeContext(
        task_id="t2", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="https://github.com/a/b", project_ref=None,
        previous_outputs={"source": {"commit_sha": "a"*40, "repo_dirname": "b"},
                          "profile": {"is_web": True}},
        project_id="p1", owner_id="u1", db_session=object(),
    )
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.docker_compose_up", new_callable=AsyncMock) as up:
        gs.return_value.claude_agent_sdk_enabled = True
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.touch = AsyncMock()
        out = await EnvReadyNode().execute(ctx)
    assert out["target_url"] == "http://10.0.0.8:3001"
    assert out["reused"] is True
    ai.assert_not_awaited()
    up.assert_not_awaited()
```

第二用例：`role="wait"` 第一次 acquire 返回 wait，第二次返回 reuse（`side_effect`），`asyncio.sleep` patch 成立即返回。

第三用例：`role="create"` 时 `docker_compose_up` 的 `-p` / 路径走 lab（可对 `docker_compose_up` 的 `lab_id` kwarg 断言；若仍是 positional `task_id`，本任务改成 `lab_id=`）。

把 `test_compose_up_uses_lab_project_name` 的 `task_id="Task-1"` 改成 `lab_id="Lab-1"`，期望 `-p crucible-lab-lab-1`。

现有 `test_node_env_ready.py` 的 execute 测试在 SDK 开启路径会调 acquire：给这些测试 **patch `LabService.acquire`** 返回 `role="create"` 且 workdir/compose 指向他们已 `_write_compose` 的目录，或在模块级 autouse mock create。不要让真实 DB 进这些单测。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_env_ready_lab_reuse.py tests/test_compose_path.py::test_compose_up_uses_lab_project_name -v`

Expected: reuse 测试里 `run_ai_turn` 仍被调用，或 `reused` 缺失

- [ ] **Step 3: 实现 env_ready 分支**

`execute` 在 SDK 开启后：

```python
sha = (ctx.previous_outputs.get("source") or {}).get("commit_sha")
if not sha:
    raise RuntimeError("env_ready 缺少 source.commit_sha，不能 acquire 靶场")
project_id = ctx.project_id
if not project_id:
    # ProjectService.upsert_by_git_url ...
result = await LabService(ctx.db_session).acquire(
    owner_id=ctx.owner_id, project_id=project_id, commit_sha=sha, task_id=ctx.task_id,
)
if result.role == "reuse":
    return {..., "reused": True}
if result.role == "wait":
    # poll until reuse/create/timeout
if result.role == "start":
    ok = await compose_start(result.compose_project)
    ...
# create: 现有循环，但：
# - docker_compose_up 用 lab_id=result.lab_id, host_workdir=result.workdir
# - AI 仍 host_workdir=ctx.host_workdir（源码在任务目录）
# - 每轮 up 前 sync_recipe_to_lab(repo_abs, result.workdir)
# - 成功 mark_ready；5 轮失败 mark_failed 再 raise
```

`docker_compose_up` 签名把 `task_id` 改名为 `lab_id`（全库替换调用点）。

orchestrator 创建 `NodeContext` 时加 `lab_id=getattr(task, "lab_id", None)`。reproduce 开头 touch。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_env_ready_lab_reuse.py tests/test_node_env_ready.py tests/test_compose_path.py tests/test_lab_acquire.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/contexts/agent backend/tests/test_env_ready_lab_reuse.py backend/tests/test_node_env_ready.py backend/tests/test_compose_path.py
git commit -m "$(cat <<'EOF'
feat(lab): env_ready 按 lab 状态创建或复用

第二任务不应再 compose up；配方拷到 lab 目录才能在任务目录清理后重建。
EOF
)"
```

---

### Task 5: TTL 巡检 + 僵死 creating

**Files:**
- Modify: `backend/app/contexts/lab/service.py`（`expire_silent_labs`、`fail_stale_creating`）
- Modify: `backend/app/contexts/lab/docker_ops.py`（本任务创建：`compose_down(project: str)` 调 `docker compose -p {project} down -v --remove-orphans`）
- Modify: `backend/app/contexts/agent/runtime_cleanup.py`（5 分钟循环里调用 lab 巡检；遗留 `crucible-lab-{task_id}` 无 labs 行则 down）
- Test: `backend/tests/test_lab_ttl.py`

**Interfaces:**
- Produces:

```python
async def expire_silent_labs(self, *, now: datetime | None = None) -> list[str]:
    """status in {ready, stopped} 且 last_seen_at+ttl 已过 且 live_task_ids 为空
    → compose_down → status=expired。返回 lab_id 列表。"""

async def fail_stale_creating(self) -> list[str]:
    """creating 且 live_task_ids 为空 → status=failed，best-effort compose_down。"""
```

遗留孤儿：`docker ps -a --format '{{.Label "com.docker.compose.project"}}'`，项目名匹配 `^crucible-lab-` 且后缀 **不是** 任何 `labs.id`（大小写不敏感）→ `compose_down`。不要误拆正在用的 `crucible-lab-{lab_id}`。

- [ ] **Step 1: 写失败测试**

把 Task 2 的 `seed`/`SHA`/session fixture 复制进本文件。`t1` 默认 `status="running"`，expire 前把该任务改成 `completed` 才允许 TTL 拆：

```python
@pytest.mark.asyncio
async def test_ttl_expires_ready_lab_without_live_tasks(session):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, patch
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task

    await seed(session, task_id="t1")
    svc = LabService(session)
    r = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1")
    await svc.mark_ready(
        r.lab_id, target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    (await session.get(Task, "t1")).status = "completed"
    lab = await session.get(Lab, r.lab_id)
    lab.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await session.commit()
    with patch("app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock) as down:
        expired = await svc.expire_silent_labs()
    assert r.lab_id in expired
    down.assert_awaited()
    assert (await session.get(Lab, r.lab_id)).status == "expired"


@pytest.mark.asyncio
async def test_ttl_skips_lab_with_running_task(session):
    from datetime import datetime, timedelta, timezone
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1", status="running")
    svc = LabService(session)
    r = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1")
    await svc.mark_ready(
        r.lab_id, target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    lab = await session.get(Lab, r.lab_id)
    lab.last_seen_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await session.commit()
    expired = await svc.expire_silent_labs()
    assert expired == []
    assert (await session.get(Lab, r.lab_id)).status == "ready"
```

时间用注入 `now=`，避免 flaky。`last_seen_at is None` 视为立即过期。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_lab_ttl.py -v`

Expected: `expire_silent_labs` 不存在

- [ ] **Step 3: 实现 docker_ops.compose_down + service 两方法**

把现有 sweeper 启动处（`runtime_cleanup` 里 `start_sweeper`）在每次 `sweep_orphan_runtimes` 后打开独立 session 调 `LabService.expire_silent_labs()` 与 `fail_stale_creating()`。不要在 `teardown_task_runtime` 里 down lab。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_lab_ttl.py tests/test_runtime_cleanup.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/contexts/lab backend/app/contexts/agent/runtime_cleanup.py backend/tests/test_lab_ttl.py
git commit -m "$(cat <<'EOF'
feat(lab): TTL 静默销毁与僵死 creating 巡检

靶场不再跟任务走，只能在完全无人使用且超时后 down。
EOF
)"
```

---

### Task 6: 管理 API（分组列表 + 停起重建销毁 + 容器操作）

**Files:**
- Create: `backend/app/contexts/lab/schemas.py`
- Create: `backend/app/contexts/lab/api.py`
- Create: `backend/app/contexts/lab/errors.py`（`LabBusyError`、`LabNotFoundError`）
- Modify: `backend/app/contexts/lab/docker_ops.py`（`compose_stop/start`、`list_containers`、`container_stop/start/restart/rm`）
- Modify: `backend/app/contexts/lab/service.py`（管理方法）
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_lab_api.py`

**Interfaces:**
- Produces HTTP（均需登录，只看 `owner_id`）：

| 方法 | 路径 |
|---|---|
| GET | `/api/v1/labs` |
| GET | `/api/v1/labs/{id}` |
| POST | `/api/v1/labs/{id}/actions/stop` |
| POST | `/api/v1/labs/{id}/actions/start` |
| POST | `/api/v1/labs/{id}/actions/rebuild` |
| DELETE | `/api/v1/labs/{id}` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/stop` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/start` |
| POST | `/api/v1/labs/{id}/containers/{name}/actions/restart` |
| DELETE | `/api/v1/labs/{id}/containers/{name}` |

`list_grouped(owner_id)` 返回 spec §4 JSON，每个 lab 额外带 `live_task_count: int`（`len(live_task_ids)`，前端用来 disable 按钮）。`ttl_remaining_seconds = max(0, int(ttl_seconds - (now - last_seen_at).total_seconds()))`。

写操作前 `live_task_ids` 非空 → raise `LabBusyError` → API `409`。GET 详情 `touch`。容器名必须 `docker ps -a --filter label=com.docker.compose.project={compose_project}` 命中，否则 404。

rebuild：无 compose 文件 → 400「缺少配方，请从验证任务重新创建」。有文件：status=`creating` → `docker compose -p ... -f ... up -d --build` → ready/failed。

destroy：`compose_down` → `destroyed`。

- [ ] **Step 1: 写失败测试**

用 FastAPI `TestClient`/`httpx.AsyncClient` 若项目已有 auth fixture 则复用；没有就直接测 `LabService.stop_lab`：

把 Task 2 的 `seed`/`SHA`/session fixture 复制进本文件。

```python
@pytest.mark.asyncio
async def test_stop_rejected_when_task_running(session):
    from app.contexts.lab.errors import LabBusyError
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1", status="running")
    svc = LabService(session)
    r = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1")
    await svc.mark_ready(
        r.lab_id, target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    with pytest.raises(LabBusyError) as ei:
        await svc.stop_lab(r.lab_id, owner_id="u1")
    assert "t1" in ei.value.task_ids


@pytest.mark.asyncio
async def test_list_grouped_by_project(session):
    from unittest.mock import AsyncMock, patch
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1")
    svc = LabService(session)
    r = await svc.acquire(owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1")
    await svc.mark_ready(
        r.lab_id, target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    with patch("app.contexts.lab.docker_ops.list_containers", new_callable=AsyncMock, return_value=[]):
        grouped = await svc.list_grouped("u1")
    assert grouped[0]["project_id"] == "p1"
    assert grouped[0]["labs"][0]["id"] == r.lab_id
    assert grouped[0]["labs"][0]["containers"] == []
    assert grouped[0]["labs"][0]["live_task_count"] >= 1
```

API 层补一条：未登录 401；别人的 lab 404。看 `test_project_api.py` 是否打 HTTP——若只测 service，本任务 API 用同样风格测 service，再加 `test_lab_router_busy_returns_409` patch service 用 FastAPI app。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_lab_api.py -v`

Expected: `LabBusyError` / `stop_lab` 不存在

- [ ] **Step 3: 实现**

`docker_ops.list_containers(project)`：

```
docker ps -a --filter label=com.docker.compose.project={project} --format "{{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}"
```

解析为 `{name, status, ports, image}`。

`container_*`：先 `assert_container_in_project(name, project)` 再 `docker stop/start/restart/rm -f {name}`。

`main.py`：`app.include_router(lab_router, prefix="/api/v1")`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_lab_api.py tests/test_lab_ttl.py tests/test_lab_acquire.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/contexts/lab backend/app/main.py backend/tests/test_lab_api.py
git commit -m "$(cat <<'EOF'
feat(lab): 靶场管理 API 与占用 409

有任务在跑时禁止停拆，避免复现半路掉线。
EOF
)"
```

---

### Task 7: 前端「靶场管理」页

**Files:**
- Create: `frontend/src/features/lab/LabStacks.tsx`
- Create: `frontend/src/pages/LabsPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/app/layout.tsx`
- Modify: `frontend/src/shared/lib/routes.ts`
- Modify: `frontend/src/shared/lib/api.ts`
- Test: `frontend/src/features/lab/LabStacks.test.tsx`（若现有 vitest 对组件不熟，测纯函数 `canMutateLab(liveTaskCount: number) => liveTaskCount === 0` 放同文件或 `labUi.ts`）

**Interfaces:**
- Consumes: Task 6 JSON
- Produces: 路由 `/labs`；侧栏业务组增加「靶场管理」；`api.listLabs` / `getLab` / `labAction` / `labContainerAction`

- [ ] **Step 1: 写失败测试**

`frontend/src/features/lab/labUi.ts`：

```ts
export function canMutateLab(liveTaskCount: number): boolean {
  return liveTaskCount === 0
}
```

`LabStacks.test.tsx`：

```ts
import { describe, expect, it } from 'vitest'
import { canMutateLab } from './labUi'

describe('canMutateLab', () => {
  it('blocks when tasks are using the lab', () => {
    expect(canMutateLab(1)).toBe(false)
    expect(canMutateLab(0)).toBe(true)
  })
})
```

先写测试再写 `labUi.ts`（RED）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/features/lab/LabStacks.test.tsx`

Expected: 无法解析 `./labUi`

- [ ] **Step 3: 实现页面**

`LabsPage`：`AppLayout` + `PageHeader` 标题「靶场管理」+ `LabStacks`。

`LabStacks`：`useQuery(['labs'], api.listLabs)`；`Collapse` 或嵌套 `Table`：项目名 → lab 行（短 SHA、`status` Tag、可点击 `target_url`、TTL 倒计时用 `ttl_remaining_seconds` + 本地 tick）→ 展开 `containers`。按钮：停/起/重建/销毁；容器：停/起/重启/删除。`canMutateLab(lab.live_task_count ?? 0)` 为 false 时 disable，Tooltip「有验证任务占用，请先取消任务」。列表 schema 若无 `live_task_count`，Task 6 在每个 lab 对象加上该字段（`len(live_task_ids)`）——**若 Task 6 漏了，本任务回补 service/schema**。

`api.ts` 类型与路径对齐 Task 6。`layout.tsx` `NAV_ITEMS` 业务组加 `{ key: '/labs', icon: <CloudServerOutlined />, label: '靶场管理' }`，`selectedKey` 增加 `/labs` 前缀。`App.tsx` 增加 RequireAuth 路由。`routes.ts` 面包屑：业务 / 靶场管理。

- [ ] **Step 4: 跑测试 + 类型检查**

Run: `cd frontend && npx vitest run src/features/lab && npx tsc --noEmit`

Expected: PASS，无 tsc 错误

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "$(cat <<'EOF'
feat(lab): 增加按项目分组的靶场管理页

对照 Docker Desktop：项目下挂 compose 容器，占用中禁止手动拆。
EOF
)"
```

---

### Task 8: 权威文档同步

**Files:**
- 状态改为「已评审」

- Modify: `docs/superpowers/specs/2026-08-12-platform-node-orchestration-design.md`（§3.1 完成不再 `cleanup_lab_containers` 靶场；取消路径只拆 runner）
- Modify: `.claude/api-contract.md`（取消/删除不再 down 靶场；新增 Lab API 一节）
- Modify: `plugins/vuln-verify-expert/agents/env-builder.md`（创建者配方仍写 `{source_path}/.vuln-env`；平台会拷到 lab 目录。等待/复用不会进本 agent）
- Modify: `docs/development-guide.md` 若有「任务结束拆靶场」清单句，改成 TTL
- Modify: `CLAUDE.md` 仅当结构树要出现 `contexts/lab/`（一行）

**Interfaces:** 无代码接口。验证：全文搜索 `crucible-lab-{task_id}`、`任务结束`+`compose down` 的过时说法，只保留「历史孤儿 / 升级残留」说明。

- [ ] **Step 1: 改文档（无失败测试；对照 spec §8 清单）**

api-contract Lab 节用 spec §4 的表格 + 409 `LAB_IN_USE`。取消接口段删掉「再 compose down 靶场」。

- [ ] **Step 2: 一致性检查**

Run: `git grep -nE "crucible-lab-\{task_id\}|cleanup_lab_containers" -- docs .claude CLAUDE.md plugins`

Expected: 命中处都解释为「已废止 / 仅扫历史残留」，没有当现行行为。

- [ ] **Step 3: 回归相关测试**

Run: `cd backend && python -m pytest tests/test_lab_model.py tests/test_lab_acquire.py tests/test_lab_ttl.py tests/test_lab_api.py tests/test_env_ready_lab_reuse.py tests/test_node_env_ready.py tests/test_runtime_cleanup.py tests/test_compose_path.py -q`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add docs .claude plugins CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(lab): 同步靶场复用与 TTL 契约

编排完成即拆靶场的旧约定会与多任务复用冲突，文档必须先改现行行为。
EOF
)"
```

---

## Self-Review（对照 spec）

| Spec | 任务 |
|---|---|
| 复用键 owner+project+sha | Task 1–2 |
| creating 等待 / ready 复用 / stopped start | Task 2、4 |
| 配方落 lab 目录（经拷贝） | Task 4 |
| 任务结束不拆靶场 | Task 3 |
| 取消创建者 → failed | Task 2–3 |
| TTL 1h + touch 点（acquire/复现/GET/写操作） | Task 2、4、5、6 |
| 僵死 creating | Task 5 |
| 历史 `crucible-lab-{task_id}` 孤儿 | Task 5 |
| 管理 API + 409 | Task 6 |
| 前端 /labs 分组 | Task 7 |
| Mock 不写 labs | Task 4 |
| 文档 §8 | Task 8 |
| 容器级停起重启删、靶场级重建 | Task 6–7 |
| 无 TTL 设置页 / 无跨用户 / 无容器 rebuild | 全局约束，未列入任务 |
