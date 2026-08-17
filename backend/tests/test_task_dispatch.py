"""Task/TaskRun 必须提交后再投递 Celery，投递失败必须可观察。"""
from unittest.mock import patch

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


def _request():
    from app.contexts.task.schemas import TaskCreateRequest

    return TaskCreateRequest(
        project_address="https://github.com/acme/demo",
        vulnerability_description="demonstration vulnerability",
    )


@pytest.mark.asyncio
async def test_create_commits_before_celery_dispatch(session):
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    events: list[str] = []
    real_commit = session.commit

    async def tracked_commit():
        events.append("commit")
        await real_commit()

    session.commit = tracked_commit
    with patch(
        "app.core.celery_app.celery_app.send_task",
        side_effect=lambda *args, **kwargs: events.append("send"),
    ):
        await TaskService(TaskRepository(session)).create_task(_request(), "u1")

    assert events[:2] == ["commit", "send"]


@pytest.mark.asyncio
async def test_create_marks_task_and_run_failed_when_dispatch_fails(session):
    from sqlalchemy import select

    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskDispatchError, TaskService

    with patch(
        "app.core.celery_app.celery_app.send_task",
        side_effect=RuntimeError("broker down"),
    ):
        with pytest.raises(TaskDispatchError, match="投递"):
            await TaskService(TaskRepository(session)).create_task(_request(), "u1")

    task = (await session.execute(select(Task))).scalar_one()
    run = (await session.execute(select(TaskRun))).scalar_one()
    assert task.status == "failed"
    assert run.status == "failed"
    assert "broker down" in (run.error_message or "")
