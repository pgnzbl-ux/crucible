"""跨 Context：Lab 占用查询走 TaskService，禁止 Lab 直查 Task ORM。"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.project.models import Project  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        from app.contexts.task.models import AgentEvent, NodeRun, Task, TaskRun  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()


async def _seed_task(session, *, task_id: str, lab_id: str | None, status: str):
    from app.contexts.identity.models import User
    from app.contexts.task.models import Task

    if await session.get(User, "u1") is None:
        session.add(User(id="u1", email="u1@x.test", password_hash="x", display_name="u1"))
    session.add(
        Task(
            id=task_id,
            project_address="https://github.com/a/b",
            vulnerability_description="d",
            owner_id="u1",
            lab_id=lab_id,
            status=status,
        )
    )
    await session.flush()


def test_lab_service_does_not_import_task_orm():
    from app.contexts.lab import service as lab_service

    source = inspect.getsource(lab_service)
    assert "from app.contexts.task.models import Task" not in source
    assert "select(Task" not in source


@pytest.mark.asyncio
async def test_list_live_ids_by_lab_ids_batches_and_skips_terminal(session):
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    await _seed_task(session, task_id="t-live-a", lab_id="lab-a", status="running")
    await _seed_task(session, task_id="t-pend-a", lab_id="lab-a", status="pending")
    await _seed_task(session, task_id="t-done-a", lab_id="lab-a", status="completed")
    await _seed_task(session, task_id="t-live-b", lab_id="lab-b", status="queued")
    await _seed_task(session, task_id="t-none", lab_id=None, status="running")

    mapping = await TaskService(TaskRepository(session)).list_live_ids_by_lab_ids(
        ["lab-a", "lab-b", "lab-empty"]
    )
    assert mapping["lab-a"] == ["t-live-a", "t-pend-a"]
    assert mapping["lab-b"] == ["t-live-b"]
    assert mapping["lab-empty"] == []


@pytest.mark.asyncio
async def test_bind_lab_sets_task_lab_id(session):
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    await _seed_task(session, task_id="t1", lab_id=None, status="pending")
    svc = TaskService(TaskRepository(session))
    await svc.bind_lab("t1", "lab-1", commit=False)
    await session.flush()
    task = await session.get(Task, "t1")
    assert task is not None
    assert task.lab_id == "lab-1"
