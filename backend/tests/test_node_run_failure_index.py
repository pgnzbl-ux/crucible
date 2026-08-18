"""node_run 失败索引：put 一次；上传失败不改节点终态。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base
from app.shared.object_store import MemoryObjectStore, set_object_store_for_tests


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.project.models import Project  # noqa: F401
        from app.contexts.task.models import AgentEvent, NodeRun, NodeRunFailure, Task, TaskRun  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
    set_object_store_for_tests(None)


@pytest.mark.asyncio
async def test_record_node_run_failure_puts_and_indexes(session):
    from app.contexts.task.models import NodeRun, NodeRunFailure, Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from sqlalchemy import select

    task = Task(
        project_address="https://github.com/a/b.git",
        vulnerability_description="x",
        owner_id="u1",
        status="failed",
    )
    session.add(task)
    await session.flush()
    run = TaskRun(task_id=task.id, status="failed")
    session.add(run)
    await session.flush()
    nr = NodeRun(
        run_id=run.id,
        task_id=task.id,
        node_index=2,
        node_key="env_ready",
        status="failed",
        error_message="健康检查不过",
    )
    session.add(nr)
    await session.flush()

    store = MemoryObjectStore()
    set_object_store_for_tests(store)
    await TaskService(TaskRepository(session)).record_node_run_failure(
        owner_id="u1",
        task_id=task.id,
        run_id=run.id,
        node_run_id=nr.id,
        node_key="env_ready",
        error_class="health_check",
        failed_stage="health_check",
        language="java",
        attempt_count=5,
        bundle=b"tar-bytes",
    )
    row = (
        await session.execute(select(NodeRunFailure).where(NodeRunFailure.node_run_id == nr.id))
    ).scalar_one()
    assert row.bundle_key.startswith("node_run/u1/")
    assert row.bucket == "crucible-task"
    assert row.error_class == "health_check"
    assert store.get_at("node_run", row.bundle_key) == b"tar-bytes"


@pytest.mark.asyncio
async def test_record_node_run_failure_put_error_keeps_node_message(session):
    from app.contexts.task.models import NodeRun, NodeRunFailure, Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from sqlalchemy import select

    class BoomStore(MemoryObjectStore):
        def put(self, *a, **k):
            raise RuntimeError("minio down")

    task = Task(
        project_address="https://github.com/a/b.git",
        vulnerability_description="x",
        owner_id="u1",
        status="failed",
    )
    session.add(task)
    await session.flush()
    run = TaskRun(task_id=task.id, status="failed")
    session.add(run)
    await session.flush()
    nr = NodeRun(
        run_id=run.id,
        task_id=task.id,
        node_index=0,
        node_key="source",
        status="failed",
        error_message="源码克隆失败: 网络错误",
    )
    session.add(nr)
    await session.flush()
    original = nr.error_message

    set_object_store_for_tests(BoomStore())
    await TaskService(TaskRepository(session)).record_node_run_failure(
        owner_id="u1",
        task_id=task.id,
        run_id=run.id,
        node_run_id=nr.id,
        node_key="source",
        error_class="unknown",
        failed_stage=None,
        language=None,
        attempt_count=1,
        bundle=b"x",
    )
    await session.refresh(nr)
    assert nr.status == "failed"
    assert nr.error_message == original
    rows = (await session.execute(select(NodeRunFailure))).scalars().all()
    assert rows == []
