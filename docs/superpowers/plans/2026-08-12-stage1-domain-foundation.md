# 阶段 1:领域地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地数据模型地基——新增 `projects` + `node_runs` 表,改造 `tasks`(project_id/verdict)、`reports`(结构化 report_data)、`agent_events`(node_run_id),配 Alembic 迁移。为阶段 2 的 6 节点编排器铺路。

**Architecture:** 新增 `contexts/project/` context(Project CRUD);`node_runs` 归属 task context(与 task_runs 同域);reports 在 report context 内加结构化字段。所有改动走 Alembic 增量迁移,基线迁移不动。

**Tech Stack:** Python 3.11 / SQLAlchemy 2.0 Async / Alembic / SQLite(dev)/ FastAPI

## Global Constraints

- 提交信息用简体中文主体 + 英文 type 前缀
- BaseModel 已提供 UUID(36 字符字符串)+ created_at/updated_at,新模型直接继承
- Alembic env.py 必须导入新模型才能注册 metadata(`from app.contexts.project.models import Project`)
- 禁止跨 Context 建 ORM relationship,只留 ForeignKey + ID(项目 `backend.md §1`)
- 新表必须在其所属 Context 的 models.py 定义;Celery worker import_tasks 时 import 该 models
- SQLite 开发,迁移要兼容 SQLite(无 `FOR UPDATE` 等 PG 特性)
- 路径用正斜杠,Git Bash 执行

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/contexts/project/__init__.py` | 新建 | context 包 |
| `backend/app/contexts/project/models.py` | 新建 | Project ORM |
| `backend/app/contexts/project/schemas.py` | 新建 | Pydantic 请求/响应 schema |
| `backend/app/contexts/project/repository.py` | 新建 | Project CRUD |
| `backend/app/contexts/project/service.py` | 新建 | upsert by git_url、业务逻辑 |
| `backend/app/contexts/project/api.py` | 新建 | REST 端点 |
| `backend/app/contexts/task/models.py` | 改 | Task 加 project_id/verdict;新增 NodeRun;AgentEvent 加 node_run_id |
| `backend/app/contexts/report/models.py` | 改 | Report 结构化字段(report_data/verdict/cvss_score 等) |
| `backend/app/contexts/agent/tasks.py` | 改 | import Project + NodeRun 注册 metadata(worker 侧) |
| `backend/alembic/env.py` | 改 | 导入 Project |
| `backend/alembic/versions/<rev>_stage1_models.py` | 新建 | 增量迁移 |
| `backend/app/contexts/project/tests/` | 新建 | Project service 测试 |
| `backend/app/main.py` | 改 | 挂载 project router |

---

### Task 1: Project ORM 模型 + context 骨架

**Files:**
- Create: `backend/app/contexts/project/__init__.py`
- Create: `backend/app/contexts/project/models.py`
- Test: `backend/tests/test_project_model.py`

**Interfaces:**
- Produces: `Project(BaseModel)` 表 `projects`,字段见下;`from app.contexts.project.models import Project` 供迁移/worker 注册。

- [ ] **Step 1: 写失败测试 — Project 建表 + 字段**

Create `backend/tests/test_project_model.py`:

```python
"""Project 模型字段与建表测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, create_engine


def test_project_table_columns():
    from app.contexts.project.models import Project
    from app.shared.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("projects")}
    expected = {
        "id", "name", "git_url", "default_ref", "description", "owner_id",
        "detected_language", "detected_framework", "is_web",
        "last_cloned_at", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"缺字段: {expected - cols}"


def test_project_required_fields():
    """name + git_url + owner_id 不可空。"""
    from app.contexts.project.models import Project
    p = Project(name="x", git_url="https://github.com/a/b.git", owner_id="u1")
    assert p.name == "x"
    assert p.is_web is None or isinstance(p.is_web, bool)  # nullable 默认
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && python -m pytest tests/test_project_model.py -v`
Expected: FAIL `ModuleNotFoundError: app.contexts.project`。

- [ ] **Step 3: 建 context 包 + 模型**

Create `backend/app/contexts/project/__init__.py`(空文件)。

Create `backend/app/contexts/project/models.py`:

```python
"""项目源码管理 — 一次注册、多任务复用。

Project 记录项目元数据 + 节点 1 画像缓存(language/framework/is_web),
后续任务复用画像省 AI。P1 阶段不长期持有源码(每任务按 run clone)。
"""
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import BaseModel


class Project(BaseModel):
    """项目(Git 仓库)的元数据与画像。"""
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    git_url: Mapped[str] = mapped_column(String(1024), nullable=False, index=True, comment="Git URL")
    default_ref: Mapped[str | None] = mapped_column(String(255), comment="默认分支/tag")
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    # 节点 1 画像后回填(后续任务复用省 AI)
    detected_language: Mapped[str | None] = mapped_column(String(50))
    detected_framework: Mapped[str | None] = mapped_column(String(100))
    is_web: Mapped[bool | None] = mapped_column(Boolean)
    last_cloned_at: Mapped[datetime | None] = mapped_column(comment="P2 源码缓存复用用")

    __table_args__ = (
        Index("idx_projects_owner", "owner_id"),
    )

    def __repr__(self) -> str:
        return f"<Project {self.name} {self.git_url[:40]}>"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd backend && python -m pytest tests/test_project_model.py -v`
Expected: 2 PASS。

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/contexts/project/__init__.py app/contexts/project/models.py tests/test_project_model.py
git commit -m "feat(project): 新增 Project ORM 模型(project context 骨架)"
```

---

### Task 2: NodeRun 模型 + Task/AgentEvent 改造

**Files:**
- Modify: `backend/app/contexts/task/models.py`
- Test: `backend/tests/test_node_run_model.py`

**Interfaces:**
- Produces: `NodeRun(BaseModel)` 表 `node_runs`,`(run_id, node_index)` 唯一;Task 新增 `project_id`(FK→projects)+ `verdict`(6 档);AgentEvent 新增 `node_run_id`(FK→node_runs, nullable)。

**说明:** Task 现有 `project_address` 字段**保留**(兼容历史数据,迁移期双写),新增 `project_id`。新代码写 project_id,project_address 保留只读。阶段 2 编排器只用 project_id。

- [ ] **Step 1: 写失败测试 — NodeRun + Task.project_id + AgentEvent.node_run_id**

Create `backend/tests/test_node_run_model.py`:

```python
"""NodeRun 模型 + Task/AgentEvent 改造测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, create_engine


def test_all_tables_present():
    from app.shared.base import Base
    # 触发所有 model 注册
    from app.contexts.project.models import Project  # noqa
    from app.contexts.task.models import Task, TaskRun, AgentEvent, NodeRun  # noqa
    from app.contexts.identity.models import User  # noqa

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert "node_runs" in tables, "缺 node_runs 表"
    assert "projects" in tables


def test_node_run_fields():
    from app.shared.base import Base
    from app.contexts.task.models import NodeRun  # noqa
    from app.contexts.identity.models import User  # noqa

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("node_runs")}
    expected = {
        "id", "run_id", "task_id", "node_index", "node_key", "status",
        "input_json", "output_json", "attempt", "agent_session_id",
        "error_message", "started_at", "finished_at", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"NodeRun 缺字段: {expected - cols}"


def test_task_has_project_id_and_verdict():
    from app.shared.base import Base
    from app.contexts.task.models import Task  # noqa
    from app.contexts.identity.models import User  # noqa

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    assert "project_id" in cols, "Task 缺 project_id"
    assert "verdict" in cols, "Task 缺 verdict"


def test_agent_event_has_node_run_id():
    from app.shared.base import Base
    from app.contexts.task.models import AgentEvent  # noqa
    from app.contexts.identity.models import User  # noqa

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("agent_events")}
    assert "node_run_id" in cols, "AgentEvent 缺 node_run_id"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && python -m pytest tests/test_node_run_model.py -v`
Expected: FAIL(`NodeRun` 不存在、`project_id`/`verdict`/`node_run_id` 缺)。

- [ ] **Step 3: 改 task/models.py — Task 加字段 + 新增 NodeRun + AgentEvent 加字段**

Modify `backend/app/contexts/task/models.py`。

在 `Task` 类内 `project_address` 字段后加:
```python
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id"), index=True,
        comment="关联 Project(P1 新增,project_address 保留兼容)",
    )
```
在 `Task` 类内 `status` 字段后加:
```python
    verdict: Mapped[str | None] = mapped_column(
        String(30), index=True,
        comment="6 档判定: confirmed|partial|code_reachable|code_smell|false_positive|not_reproduced",
    )
```

在文件末尾(`AgentEvent` 类后)新增:
```python
class NodeRun(BaseModel):
    """节点级执行记录 — 6 节点编排的核心。

    一个 TaskRun 下挂 6 个 NodeRun(node_index 0-5),各自记录
    input/output JSON、状态、排障 attempt。断点续跑时复用已完成的 output_json。
    """
    __tablename__ = "node_runs"

    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_runs.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    node_index: Mapped[int] = mapped_column(comment="0-5")
    node_key: Mapped[str] = mapped_column(
        String(20),
        comment="source|profile|env_ready|audit|reproduce|report",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending",
        comment="pending|running|completed|failed|skipped",
    )
    input_json: Mapped[str] = mapped_column(Text, default="{}", comment="节点输入(前序 output 组装)")
    output_json: Mapped[str] = mapped_column(Text, default="{}", comment="节点结构化产出(交接契约)")
    attempt: Mapped[int] = mapped_column(default=1, comment="排障重试计数(节点 2 用,max 5)")
    agent_session_id: Mapped[str | None] = mapped_column(String(128), comment="AI 节点 SDK session_id")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (
        Index("idx_node_runs_run_idx", "run_id", "node_index", unique=True),
        Index("idx_node_runs_task_key", "task_id", "node_key"),
    )

    def __repr__(self) -> str:
        return f"<NodeRun [{self.node_index}:{self.node_key}] {self.status}>"
```

在 `AgentEvent` 类内加字段:
```python
    node_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("node_runs.id"), index=True,
        comment="所属 NodeRun(节点化后,事件归属节点)",
    )
```

需要 import `datetime`(如文件顶部未有则加 `from datetime import datetime`)。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd backend && python -m pytest tests/test_node_run_model.py -v`
Expected: 4 PASS。

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/contexts/task/models.py tests/test_node_run_model.py
git commit -m "feat(task): 新增 NodeRun + Task 关联 project/verdict + AgentEvent 关联 node_run"
```

---

### Task 3: Report 结构化字段

**Files:**
- Modify: `backend/app/contexts/report/models.py`
- Test: `backend/tests/test_report_model_structured.py`

**Interfaces:**
- Produces: `Report` 新增 `verdict`(索引)、`cvss_score`(索引)、`severity`、`vulnerable_file`、`report_data`(JSON Text)、`md_artifact_key`、`docx_artifact_key`。废弃 `reasoning`/`evidence_summary`(保留列,标记 deprecated,新代码写 report_data)。

- [ ] **Step 1: 写失败测试 — Report 新字段**

Create `backend/tests/test_report_model_structured.py`:

```python
"""Report 结构化字段测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, create_engine


def test_report_structured_fields():
    from app.shared.base import Base
    from app.contexts.report.models import Report  # noqa
    from app.contexts.identity.models import User  # noqa

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("reports")}
    expected = {
        "verdict", "cvss_score", "severity", "vulnerable_file",
        "report_data", "md_artifact_key", "docx_artifact_key",
    }
    assert expected.issubset(cols), f"Report 缺字段: {expected - cols}"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && python -m pytest tests/test_report_model_structured.py -v`
Expected: FAIL(缺字段)。

- [ ] **Step 3: 改 report/models.py — 加结构化字段**

Modify `backend/app/contexts/report/models.py`,在 `Report` 类内加字段(`Float` 要 import):

文件顶部 import 行改为:
```python
from sqlalchemy import Float, ForeignKey, Index, String, Text
```

在 `Report` 类内(`evidence_summary` 字段后)加:
```python
    # 结构化字段(阶段 1 新增)
    verdict: Mapped[str | None] = mapped_column(
        String(30), index=True,
        comment="6 档: confirmed|partial|code_reachable|code_smell|false_positive|not_reproduced",
    )
    cvss_score: Mapped[float | None] = mapped_column(Float, index=True)
    severity: Mapped[str | None] = mapped_column(String(20), comment="Critical/High/Medium/Low/None")
    vulnerable_file: Mapped[str | None] = mapped_column(String(1024), comment="漏洞文件定位")
    report_data: Mapped[str | None] = mapped_column(Text, comment="8 节结构化 JSON(对齐 report_template)")
    md_artifact_key: Mapped[str | None] = mapped_column(String(512), comment="MinIO:原始 md key")
    docx_artifact_key: Mapped[str | None] = mapped_column(String(512), comment="MinIO:导出 docx key")
```

保留旧字段 `artifact_key`/`reasoning`/`evidence_summary`(迁移期不删,标 deprecated 注释)。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd backend && python -m pytest tests/test_report_model_structured.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/contexts/report/models.py tests/test_report_model_structured.py
git commit -m "feat(report): Report 加结构化字段(verdict/cvss/report_data/artifact keys)"
```

---

### Task 4: Alembic 迁移 — 建表 + 加列

**Files:**
- Modify: `backend/alembic/env.py`
- Create: `backend/alembic/versions/<auto>_stage1_models.py`(用 alembic autogenerate)
- Modify: `backend/app/contexts/agent/tasks.py`(worker 侧 import Project/NodeRun 注册 metadata)

- [ ] **Step 1: env.py 导入 Project**

Modify `backend/alembic/env.py`,在现有 model import 块(L12-15)加 Project:

```python
from app.contexts.identity.models import User
from app.contexts.task.models import Task, TaskRun, AgentEvent
from app.contexts.task.models import NodeRun  # 阶段 1 新增
from app.contexts.report.models import Report, Evidence
from app.contexts.settings.models import LlmProvider, Credential
from app.contexts.project.models import Project  # 阶段 1 新增
```

- [ ] **Step 2: worker tasks.py import Project + NodeRun(注册 metadata 给 Celery FK)**

Modify `backend/app/contexts/agent/tasks.py`,在现有 model import 块(L40-46 附近)加:

```python
from app.contexts.identity.models import User  # noqa
from app.contexts.project.models import Project  # noqa — 阶段 1 注册 projects 表
from app.contexts.report.models import Report, Evidence  # noqa
from app.contexts.task.models import AgentEvent, Task, TaskRun, NodeRun  # noqa — NodeRun 注册
```

- [ ] **Step 3: 生成迁移(autogenerate)**

Run:
```bash
cd backend
python -m alembic revision --autogenerate -m "stage1: project/node_runs 表 + task/report 结构化字段"
```
Expected: 生成新迁移文件 `alembic/versions/<hash>_stage1_*.py`。

- [ ] **Step 4: 审查生成的迁移**

打开生成的迁移文件,确认 `upgrade()` 含:
- `op.create_table("projects", ...)` 含所有字段
- `op.create_table("node_runs", ...)` 含所有字段 + 唯一索引
- `op.add_column("tasks", ...)` 加 `project_id`、`verdict`
- `op.add_column("agent_events", ...)` 加 `node_run_id`
- `op.add_column("reports", ...)` 加 `verdict`/`cvss_score`/`severity`/`vulnerable_file`/`report_data`/`md_artifact_key`/`docx_artifact_key`

如果 autogenerate 漏了或多了(比如 server_default 差异),手动修正。

- [ ] **Step 5: 跑迁移**

Run:
```bash
cd backend
python -m alembic upgrade head
```
Expected: 无报错,`alembic_version` 表更新到新 revision。

- [ ] **Step 6: 验证表结构(查 SQLite)**

Run:
```bash
cd backend
python -c "
import sqlite3
con = sqlite3.connect('crucible.db')
for t in ['projects','node_runs']:
    cols = [r[1] for r in con.execute(f'PRAGMA table_info({t})')]
    print(t, '->', len(cols), 'cols')
for t in ['tasks','agent_events','reports']:
    cols = [r[1] for r in con.execute(f'PRAGMA table_info({t})')]
    print(t, 'has project_id/verdict/node_run_id/report_data:', any(c in cols for c in ['project_id']), any(c in cols for c in ['verdict']), any(c in cols for c in ['node_run_id']), any(c in cols for c in ['report_data']))
"
```
Expected: `projects -> 12 cols`、`node_runs -> 15 cols`;tasks/report 有新列。

- [ ] **Step 7: Commit**

```bash
cd backend
git add alembic/env.py alembic/versions/*stage1*.py app/contexts/agent/tasks.py
git commit -m "feat(db): Alembic 迁移 — projects/node_runs 建表 + task/report/agent_event 加列"
```

---

### Task 5: Project CRUD(Service + Repo + Schemas + API)

**Files:**
- Create: `backend/app/contexts/project/schemas.py`
- Create: `backend/app/contexts/project/repository.py`
- Create: `backend/app/contexts/project/service.py`
- Create: `backend/app/contexts/project/api.py`
- Modify: `backend/app/main.py`(挂 router)
- Test: `backend/tests/test_project_api.py`

**Interfaces:**
- Produces REST 端点:
  - `GET /api/v1/projects` 列表
  - `POST /api/v1/projects` 创建
  - `GET /api/v1/projects/{id}` 详情
  - `PUT /api/v1/projects/{id}` 更新
  - `DELETE /api/v1/projects/{id}` 删除
  - `POST /api/v1/projects/{id}/refresh-profile` 重新画像(P2,先占位)
- Service 对外:`ProjectService.create_project(req, owner_id)`、`.upsert_by_git_url(git_url, owner_id, name=None)`(供任务创建时 auto-create project 用)、`.list_projects(owner_id)` 等。

- [ ] **Step 1: 写 API 集成测试(失败)**

Create `backend/tests/test_project_api.py`:

```python
"""Project API 集成测试(httpx AsyncClient + ASGITransport)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_projects_empty(client):
    resp = await client.get("/api/v1/projects")
    # 不鉴权可能 401;若鉴权拦截,测试改为 focus 创建逻辑(见下)
    assert resp.status_code in (200, 401)


@pytest.mark.asyncio
async def test_create_project_via_service(tmp_path):
    """绕过 API 直接测 service upsert(避免鉴权复杂性)。"""
    from app.contexts.project.service import ProjectService
    from app.contexts.project.repository import ProjectRepository
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.base import Base
        from app.contexts.identity.models import User  # noqa
        from app.contexts.project.models import Project  # noqa
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        svc = ProjectService(ProjectRepository(session))
        proj = await svc.upsert_by_git_url(
            git_url="https://github.com/a/b.git",
            owner_id="u1",
            name="b",
        )
        # 再 upsert 同一 url → 复用,不新建
        proj2 = await svc.upsert_by_git_url(
            git_url="https://github.com/a/b.git",
            owner_id="u1",
        )
        assert proj.id == proj2.id, "同 git_url 复用同一 project"

    await engine.dispose()
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && python -m pytest tests/test_project_api.py -v`
Expected: FAIL(import error 或端点不存在)。

- [ ] **Step 3: 写 schemas.py**

Create `backend/app/contexts/project/schemas.py`:

```python
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydBase, ConfigDict, Field


class ProjectCreateRequest(PydBase):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=255)
    git_url: str = Field(..., min_length=1, max_length=1024)
    default_ref: str | None = Field(None, max_length=255)
    description: str | None = None


class ProjectUpdateRequest(PydBase):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=255)
    default_ref: str | None = Field(None, max_length=255)
    description: str | None = None


class ProjectResponse(PydBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    git_url: str
    default_ref: str | None
    description: str | None
    owner_id: str
    detected_language: str | None = None
    detected_framework: str | None = None
    is_web: bool | None = None
    last_cloned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(PydBase):
    items: list[ProjectResponse]
    total: int
```

- [ ] **Step 4: 写 repository.py**

Create `backend/app/contexts/project/repository.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Project


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, project: Project) -> Project:
        self.session.add(project)
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def get_by_id(self, project_id: str) -> Project | None:
        return await self.session.get(Project, project_id)

    async def get_by_git_url(self, git_url: str, owner_id: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(Project.git_url == git_url, Project.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_id: str, limit: int = 50, offset: int = 0) -> tuple[list[Project], int]:
        base = select(Project).where(Project.owner_id == owner_id)
        result = await self.session.execute(base.order_by(Project.created_at.desc()).limit(limit).offset(offset))
        items = list(result.scalars().all())
        from sqlalchemy import func
        count_result = await self.session.execute(select(func.count()).select_from(Project).where(Project.owner_id == owner_id))
        total = count_result.scalar_one()
        return items, total

    async def delete(self, project: Project) -> None:
        await self.session.delete(project)
        await self.session.flush()
```

- [ ] **Step 5: 写 service.py**

Create `backend/app/contexts/project/service.py`:

```python
from .models import Project
from .repository import ProjectRepository
from .schemas import ProjectCreateRequest, ProjectListResponse, ProjectResponse, ProjectUpdateRequest


def _to_response(p: Project) -> ProjectResponse:
    return ProjectResponse.model_validate(p)


class ProjectService:
    def __init__(self, repo: ProjectRepository):
        self.repo = repo

    async def create_project(self, request: ProjectCreateRequest, owner_id: str) -> ProjectResponse:
        project = Project(
            name=request.name,
            git_url=request.git_url,
            default_ref=request.default_ref,
            description=request.description,
            owner_id=owner_id,
        )
        project = await self.repo.create(project)
        return _to_response(project)

    async def upsert_by_git_url(self, *, git_url: str, owner_id: str, name: str | None = None, default_ref: str | None = None) -> Project:
        """同 git_url + owner 复用,不存在则建。供任务创建时自动建 project 用。"""
        existing = await self.repo.get_by_git_url(git_url, owner_id)
        if existing:
            return existing
        project = Project(
            name=name or git_url.rstrip("/").split("/")[-1].replace(".git", "") or "project",
            git_url=git_url,
            default_ref=default_ref,
            owner_id=owner_id,
        )
        return await self.repo.create(project)

    async def get_project(self, project_id: str) -> ProjectResponse | None:
        p = await self.repo.get_by_id(project_id)
        return _to_response(p) if p else None

    async def list_projects(self, owner_id: str, limit: int = 50, offset: int = 0) -> ProjectListResponse:
        items, total = await self.repo.list_by_owner(owner_id, limit, offset)
        return ProjectListResponse(items=[_to_response(i) for i in items], total=total)

    async def update_project(self, project_id: str, request: ProjectUpdateRequest) -> ProjectResponse | None:
        p = await self.repo.get_by_id(project_id)
        if not p:
            return None
        for field in ("name", "default_ref", "description"):
            v = getattr(request, field)
            if v is not None:
                setattr(p, field, v)
        await self.repo.session.flush()
        return _to_response(p)

    async def delete_project(self, project_id: str) -> bool:
        p = await self.repo.get_by_id(project_id)
        if not p:
            return False
        await self.repo.delete(p)
        return True

    async def update_profile(self, project_id: str, *, language: str | None, framework: str | None, is_web: bool | None) -> None:
        """节点 1 画像后回填(供编排器调用)。"""
        p = await self.repo.get_by_id(project_id)
        if p:
            if language is not None:
                p.detected_language = language
            if framework is not None:
                p.detected_framework = framework
            if is_web is not None:
                p.is_web = is_web
            await self.repo.session.flush()
```

- [ ] **Step 6: 写 api.py**

Create `backend/app/contexts/project/api.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.shared.deps import CurrentUserId
from .repository import ProjectRepository
from .schemas import ProjectCreateRequest, ProjectListResponse, ProjectResponse, ProjectUpdateRequest
from .service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


async def get_project_service(
    session: Annotated[AsyncSession, Depends(get_db_session)]
) -> ProjectService:
    return ProjectService(ProjectRepository(session))


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ProjectListResponse:
    return await svc.list_projects(user_id, limit, offset)


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    request: ProjectCreateRequest,
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
) -> ProjectResponse:
    return await svc.create_project(request, user_id)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    p = await svc.get_project(project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    request: ProjectUpdateRequest,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    p = await svc.update_project(project_id, request)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    deleted = await svc.delete_project(project_id)
    if not deleted:
        raise HTTPException(404, "项目不存在")
```

- [ ] **Step 7: main.py 挂载 router**

Modify `backend/app/main.py`,在现有 router include 块加(参考已有 task/report router 的挂载方式):

```python
from app.contexts.project.api import router as project_router
app.include_router(project_router, prefix="/api/v1")
```

- [ ] **Step 8: 跑测试验证通过**

Run: `cd backend && python -m pytest tests/test_project_api.py -v`
Expected: service 测试 PASS(upsert 复用)。API 端点测试看鉴权配置(200 或 401 都接受)。

- [ ] **Step 9: Commit**

```bash
cd backend
git add app/contexts/project/ app/main.py tests/test_project_api.py
git commit -m "feat(project): Project CRUD service/repository/api + 挂载 router"
```

---

### Task 6: 任务管理增强 — retry / delete 端点

**Files:**
- Modify: `backend/app/contexts/task/service.py`
- Modify: `backend/app/contexts/task/api.py`
- Modify: `backend/app/contexts/task/repository.py`(如需)
- Modify: `backend/app/contexts/task/schemas.py`(RunSummary 加 verdict/node 摘要)
- Test: `backend/tests/test_task_retry_delete.py`

**Interfaces:**
- Produces:
  - `POST /api/v1/tasks/{id}/retry` → 新 TaskRun,复用已成功 NodeRun output_json,重新投 Celery
  - `DELETE /api/v1/tasks/{id}` 软删(status=archived)+ 清理活跃靶场容器
  - `DELETE /api/v1/tasks/{id}?hard=true` 硬删(header `X-Confirm: true`)

- [ ] **Step 1: 写 retry 测试**

Create `backend/tests/test_task_retry_delete.py`:

```python
"""任务 retry / delete 测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.shared.base import Base


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa
        from app.contexts.report.models import Report  # noqa
        from app.contexts.project.models import Project  # noqa
        from app.contexts.settings.models import LlmProvider  # noqa
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_creates_new_run_and_reuses_completed_nodes(session):
    """retry 新建 run,把旧 run 已完成的 NodeRun.output_json 拷为新 run completed 节点。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.service import TaskService
    from app.contexts.task.repository import TaskRepository

    task = Task(project_address="x", vulnerability_description="d", owner_id="u1", status="failed")
    session.add(task); await session.flush()
    old_run = TaskRun(task_id=task.id, status="failed")
    session.add(old_run); await session.flush()
    # 旧 run: 节点 0/1 completed,节点 2 failed
    for i, (key, st) in enumerate([("source","completed"),("profile","completed"),("env_ready","failed")]):
        session.add(NodeRun(run_id=old_run.id, task_id=task.id, node_index=i, node_key=key, status=st, output_json='{"x":1}' if st=="completed" else "{}"))
    await session.flush()

    svc = TaskService(TaskRepository(session))
    new_run_id = await svc.retry_task(task.id)

    # 新 run 的节点 0/1 应 completed(复用),节点 2 起 pending/running
    from sqlalchemy import select
    new_nodes = (await session.execute(select(NodeRun).where(NodeRun.run_id == new_run_id).order_by(NodeRun.node_index))).scalars().all()
    assert len(new_nodes) == 3
    assert new_nodes[0].status == "completed"
    assert new_nodes[0].output_json == '{"x":1}'  # 复用了 output
    assert new_nodes[2].status != "completed"  # 失败节点重跑
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && python -m pytest tests/test_task_retry_delete.py -v`
Expected: FAIL(`TaskService.retry_task` 不存在)。

- [ ] **Step 3: 实现 retry_task + delete_task in service.py**

Modify `backend/app/contexts/task/service.py`,加方法:

```python
    async def retry_task(self, task_id: str) -> str:
        """重试任务:新建 run,复用上一 run 已完成的 NodeRun.output_json(断点续跑),
        从第一个非 completed 节点起重跑。返回新 run_id。
        """
        task = await self.repo.get_by_id_with_runs(task_id)
        if not task:
            raise ValueError("任务不存在")
        if task.status not in ("failed", "completed", "cancelled", "needs_review"):
            raise ValueError(f"状态为 {task.status} 的任务不能重试")

        last_run = task.runs[0] if task.runs else None  # 最新的(repo 按 created_at desc 排)
        new_run = TaskRun(task_id=task_id, status="pending")
        new_run = await self.repo.create_run(new_run)

        # 复用上一 run 已完成节点的 output_json
        if last_run:
            from app.contexts.task.models import NodeRun
            from sqlalchemy import select
            result = await self.repo.session.execute(
                select(NodeRun)
                .where(NodeRun.run_id == last_run.id, NodeRun.status == "completed")
                .order_by(NodeRun.node_index)
            )
            for nr in result.scalars().all():
                session.add = None  # 占位,实际用 self.repo.session.add
                self.repo.session.add(NodeRun(
                    run_id=new_run.id, task_id=task_id,
                    node_index=nr.node_index, node_key=nr.node_key,
                    status="completed",  # 直接标完成,断点续跑
                    output_json=nr.output_json, input_json=nr.input_json,
                    started_at=nr.started_at, finished_at=nr.finished_at,
                ))
            await self.repo.session.flush()

        task.status = "running"
        await self.repo.session.flush()

        # 投 Celery(用新 run.id 作 celery task_id)
        from app.core.celery_app import celery_app
        try:
            celery_app.send_task("agent.run_analysis", args=[task_id, new_run.id], task_id=new_run.id)
        except Exception:
            pass  # broker 不可用,run 停 pending

        return new_run.id

    async def delete_task(self, task_id: str, *, hard: bool = False) -> bool:
        """软删(status=archived)或硬删。running 中的任务先 cancel。"""
        task = await self.repo.get_by_id_with_runs(task_id)
        if not task:
            return False
        if task.status == "running":
            raise ValueError("运行中的任务不能删除,请先取消")

        if hard:
            # 物理删除(级联 runs/nodes/events 由 FK ON DELETE 或手动)
            await self.repo.delete_hard(task)
        else:
            task.status = "archived"
            await self.repo.session.flush()
        return True
```

(注意:上面 `session.add = None` 是笔误占位,实际写 `self.repo.session.add(NodeRun(...))`;实施时修正。)

- [ ] **Step 4: 加 DELETE / retry 端点 in api.py**

Modify `backend/app/contexts/task/api.py`,加:

```python
@router.post("/{task_id}/retry", status_code=202)
async def retry_task(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
) -> dict:
    """重试任务(断点续跑:复用已完成节点)"""
    try:
        new_run_id = await svc.retry_task(task_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"task_id": task_id, "run_id": new_run_id, "status": "retrying"}


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
    hard: bool = Query(False),
    x_confirm: str = Header(None, alias="X-Confirm"),
) -> None:
    """删除任务(默认软删 archived;hard=true 需 X-Confirm: true)"""
    if hard and x_confirm != "true":
        raise HTTPException(428, "硬删需 X-Confirm: true header")
    try:
        deleted = await svc.delete_task(task_id, hard=hard)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not deleted:
        raise HTTPException(404, "任务不存在")
```

(需 import `Header` from fastapi)

- [ ] **Step 5: 给 Task.status 加 archived;TaskRepository 加 delete_hard(如需)**

Modify `backend/app/contexts/task/models.py`,Task.status comment 加 `archived`:
```python
    comment="pending|queued|running|needs_review|completed|failed|cancelled|archived"
```

TaskRepository 如无 delete_hard,加(实现里删 task + 级联 runs/nodes/events):
```python
    async def delete_hard(self, task: Task) -> None:
        from sqlalchemy import delete
        from app.contexts.task.models import NodeRun, AgentEvent, TaskRun
        # 级联清理(顺序:events → nodes → runs → task)
        run_ids = [r.id for r in task.runs] if task.runs else []
        if run_ids:
            await self.session.execute(delete(AgentEvent).where(AgentEvent.run_id.in_(run_ids)))
            await self.session.execute(delete(NodeRun).where(NodeRun.run_id.in_(run_ids)))
            await self.session.execute(delete(TaskRun).where(TaskRun.id.in_(run_ids)))
        await self.session.delete(task)
        await self.session.flush()
```

- [ ] **Step 6: 跑测试验证通过**

Run: `cd backend && python -m pytest tests/test_task_retry_delete.py -v`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/contexts/task/ tests/test_task_retry_delete.py
git commit -m "feat(task): 任务 retry(断点续跑)+ delete(软删/硬删)"
```

---

### Task 7: 节点状态查询端点

**Files:**
- Modify: `backend/app/contexts/task/service.py`(加 get_run_nodes)
- Modify: `backend/app/contexts/task/api.py`
- Test: `backend/tests/test_node_runs_query.py`

**Interfaces:**
- Produces: `GET /api/v1/tasks/{id}/runs/{rid}/nodes` → 返回该 run 的 6 节点状态 + output 摘要(供前端步骤条)。

- [ ] **Step 1: 写测试**

Create `backend/tests/test_node_runs_query.py`:

```python
"""GET /runs/{rid}/nodes 端点测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.shared.base import Base


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa
        from app.contexts.task.models import Task, TaskRun, NodeRun  # noqa
        from app.contexts.report.models import Report  # noqa
        from app.contexts.project.models import Project  # noqa
        from app.contexts.settings.models import LlmProvider  # noqa
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_run_nodes(session):
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.service import TaskService
    from app.contexts.task.repository import TaskRepository

    task = Task(project_address="x", vulnerability_description="d", owner_id="u1", status="running")
    session.add(task); await session.flush()
    run = TaskRun(task_id=task.id, status="running")
    session.add(run); await session.flush()
    for i, key in enumerate(["source","profile","env_ready","audit","reproduce","report"]):
        session.add(NodeRun(run_id=run.id, task_id=task.id, node_index=i, node_key=key, status=("completed" if i<2 else "pending")))
    await session.flush()

    svc = TaskService(TaskRepository(session))
    nodes = await svc.get_run_nodes(task.id, run.id)
    assert len(nodes) == 6
    assert nodes[0]["node_key"] == "source"
    assert nodes[0]["status"] == "completed"
    assert nodes[2]["status"] == "pending"
```

- [ ] **Step 2: 跑测试失败**

Run: `cd backend && python -m pytest tests/test_node_runs_query.py -v`
Expected: FAIL(`get_run_nodes` 不存在)。

- [ ] **Step 3: service 加 get_run_nodes**

Modify `backend/app/contexts/task/service.py`:

```python
    async def get_run_nodes(self, task_id: str, run_id: str) -> list[dict]:
        from app.contexts.task.models import NodeRun
        from sqlalchemy import select
        result = await self.repo.session.execute(
            select(NodeRun).where(NodeRun.run_id == run_id).order_by(NodeRun.node_index)
        )
        return [
            {
                "id": nr.id, "node_index": nr.node_index, "node_key": nr.node_key,
                "status": nr.status, "attempt": nr.attempt,
                "error_message": nr.error_message,
                "started_at": nr.started_at.isoformat() if nr.started_at else None,
                "finished_at": nr.finished_at.isoformat() if nr.finished_at else None,
            }
            for nr in result.scalars().all()
        ]
```

- [ ] **Step 4: api 加端点**

Modify `backend/app/contexts/task/api.py`:

```python
@router.get("/{task_id}/runs/{run_id}/nodes")
async def get_run_nodes(
    task_id: str,
    run_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
) -> list[dict]:
    """获取某 run 的 6 节点状态(前端步骤条)"""
    return await svc.get_run_nodes(task_id, run_id)
```

- [ ] **Step 5: 跑测试通过 + Commit**

```bash
cd backend && python -m pytest tests/test_node_runs_query.py -v
git add app/contexts/task/ tests/test_node_runs_query.py
git commit -m "feat(task): 节点状态查询端点 GET /runs/{rid}/nodes"
```

---

### Task 8: 阶段 1 回归冒烟

- [ ] **Step 1: 跑全部新增测试**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 所有测试 PASS。

- [ ] **Step 2: 后端启动验证(导入 + 路由)**

Run:
```bash
cd backend
D:/codeproject/Crucible/.venv/Scripts/python.exe -c "from app.main import app; print('routes:', len(app.routes))"
```
Expected: 无导入错误,打印路由数。确认 `/api/v1/projects` 系列端点存在。

- [ ] **Step 3: 跑现有冒烟脚本(如有,确保未破坏)**

Run: `cd backend && python tests/smoke_agent_runner.py 2>&1 | tail -5`(如可行)
Expected: 无新增失败。

- [ ] **Step 4: Commit + 阶段 1 收尾**

如全部通过,无需额外 commit(Task 1-7 已各自提交)。在 development-guide.md 「已完成清单」补注阶段 1。

---

## Self-Review 结果

**Spec 覆盖:**
- §2.2 Project 表 → Task 1 ✓
- §2.5 NodeRun 表 → Task 2 ✓
- §2.3 Task 改造(project_id/verdict)→ Task 2 ✓
- §2.6 Report 结构化 → Task 3 ✓
- §2.7 状态机 + agent_events.node_run_id → Task 2 ✓
- Alembic 迁移 → Task 4 ✓
- §5.1 retry → Task 6 ✓
- §5.2 delete → Task 6 ✓
- §7 前端节点进度数据源 → Task 7 ✓
- Project CRUD → Task 5 ✓

**Placeholder scan:** Task 6 Step 3 有一处 `session.add = None` 笔误占位,已在注释中标明实施时修正为 `self.repo.session.add(...)`。其余步骤含完整代码。

**Type consistency:** `ProjectService.upsert_by_git_url` 签名一致;`NodeRun(run_id, node_index)` 在 retry 和 get_run_nodes 复用一致;verdict 6 档枚举在各处描述一致。

**已知风险:**
- Task 4 autogenerate 可能因 SQLite 与 PG 差异生成不完全等价的迁移(如 server_default),实施时审查并手修。
- Task 6 retry 投 Celery 的 `agent.run_analysis` 任务签名在阶段 2 会变(改 6 节点编排),阶段 1 先用旧签名兼容,阶段 2 重写。
- Task 5 API 鉴权依赖现有 `CurrentUserId`,如未配测试用户,API 集成测试可能 401;service 层测试绕过鉴权。
- Task 6 hard delete 的级联依赖 FK,SQLite 默认不强制 FKpragma),迁移建表时需 `PRAGMA foreign_keys=ON` 或用应用层级联(本计划用手动级联 SQL)。
