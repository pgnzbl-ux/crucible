"""Task/TaskRun 必须提交后再投递 Celery，投递失败必须可观察。"""
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest.fixture(autouse=True)
def _runner_image_ok(monkeypatch):
    monkeypatch.setattr(
        "app.core.agent_runner.agent_runner_manager.image_exists",
        lambda *args, **kwargs: True,
    )


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


async def _seed_default_llm(session):
    from app.contexts.settings.models import LlmProvider

    session.add(
        LlmProvider(
            name="test",
            provider_type="deepseek",
            base_url="https://api.deepseek.com/anthropic",
            api_key_encrypted="sk-test",
            model="deepseek-v4-flash",
            is_default=True,
        )
    )
    await session.flush()


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
    await _seed_default_llm(session)
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

    await _seed_default_llm(session)
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


@pytest.mark.asyncio
async def test_create_rejects_unsafe_git_url(session):
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.schemas import TaskCreateRequest
    from app.contexts.task.service import TaskService

    req = TaskCreateRequest(
        project_address="file:///etc/passwd",
        vulnerability_description="demonstration vulnerability",
    )
    with pytest.raises(ValueError, match="Git"):
        await TaskService(TaskRepository(session)).create_task(req, "u1")


@pytest.mark.asyncio
async def test_redispatch_stale_queued_uses_original_run_id(session):
    from datetime import datetime, timedelta, timezone

    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    task = Task(
        project_address="https://github.com/acme/demo",
        vulnerability_description="d",
        owner_id="u1",
        status="queued",
    )
    session.add(task)
    await session.flush()
    run = TaskRun(
        task_id=task.id,
        status="pending",
        created_at=now - timedelta(seconds=120),
    )
    session.add(run)
    await session.commit()

    sent: list[tuple] = []
    with patch(
        "app.core.celery_app.celery_app.send_task",
        side_effect=lambda *args, **kwargs: sent.append((args, kwargs)),
    ):
        ids = await TaskService(TaskRepository(session)).redispatch_stale_queued(
            min_age_seconds=60, now=now
        )

    assert ids == [run.id]
    assert sent[0][0][0] == "agent.run_analysis"
    assert sent[0][1]["args"] == [task.id, run.id]
    assert sent[0][1]["task_id"] == run.id


@pytest.mark.asyncio
async def test_create_rejects_without_default_llm_provider(session):
    from sqlalchemy import select

    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    with patch("app.core.celery_app.celery_app.send_task") as send:
        with pytest.raises(ValueError, match="LLM Provider"):
            await TaskService(TaskRepository(session)).create_task(_request(), "u1")

    assert (await session.execute(select(Task))).scalar_one_or_none() is None
    send.assert_not_called()


@pytest.mark.asyncio
async def test_create_rejects_default_provider_without_api_key(session):
    from app.contexts.settings.models import LlmProvider
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    session.add(
        LlmProvider(
            name="empty",
            provider_type="deepseek",
            base_url="https://api.deepseek.com/anthropic",
            api_key_encrypted="  ",
            model="deepseek-v4-flash",
            is_default=True,
        )
    )
    await session.flush()

    with patch("app.core.celery_app.celery_app.send_task") as send:
        with pytest.raises(ValueError, match="API Key"):
            await TaskService(TaskRepository(session)).create_task(_request(), "u1")
    send.assert_not_called()


@pytest.mark.asyncio
async def test_create_rejects_without_runner_image(session, monkeypatch):
    from sqlalchemy import select

    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    monkeypatch.setattr(
        "app.core.agent_runner.agent_runner_manager.image_exists",
        lambda *args, **kwargs: False,
    )
    await _seed_default_llm(session)

    with patch("app.core.celery_app.celery_app.send_task") as send:
        with pytest.raises(ValueError, match="agent-runner 镜像"):
            await TaskService(TaskRepository(session)).create_task(_request(), "u1")

    assert (await session.execute(select(Task))).scalar_one_or_none() is None
    send.assert_not_called()


@pytest.mark.asyncio
async def test_redispatch_skips_fresh_queued(session):
    from datetime import datetime, timezone

    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    task = Task(
        project_address="https://github.com/acme/demo",
        vulnerability_description="d",
        owner_id="u1",
        status="queued",
        created_at=now,
    )
    session.add(task)
    await session.flush()
    session.add(TaskRun(task_id=task.id, status="pending", created_at=now))
    await session.commit()

    with patch("app.core.celery_app.celery_app.send_task") as send:
        ids = await TaskService(TaskRepository(session)).redispatch_stale_queued(
            min_age_seconds=60, now=now
        )

    assert ids == []
    send.assert_not_called()
