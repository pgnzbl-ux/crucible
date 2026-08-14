"""Lab acquire、状态更新与 live 占用查询。"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base

SHA = "a" * 40


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
    async with factory() as s:
        yield s
    await engine.dispose()


async def seed(session, *, task_id="t1", status="running"):
    from app.contexts.identity.models import User
    from app.contexts.project.models import Project
    from app.contexts.task.models import Task

    if await session.get(User, "u1") is None:
        session.add(
            User(
                id="u1",
                email="u1@x.test",
                password_hash="x",
                display_name="u1",
            )
        )
        session.add(
            Project(
                id="p1",
                name="demo",
                git_url="https://github.com/a/b",
                owner_id="u1",
            )
        )
    if await session.get(Task, task_id) is None:
        session.add(
            Task(
                id=task_id,
                project_address="https://github.com/a/b",
                vulnerability_description="d",
                owner_id="u1",
                project_id="p1",
                status=status,
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_second_task_waits_while_creating(session):
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task

    await seed(session, task_id="t1")
    await seed(session, task_id="t2")
    svc = LabService(session)
    a = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    b = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t2"
    )
    assert a.role == "create" and a.lab_id == b.lab_id
    assert a.workdir.endswith(f"/labs/{a.lab_id}")
    assert a.compose_project == f"crucible-lab-{a.lab_id.lower()}"
    assert b.role == "wait" and b.reused is False
    t2 = await session.get(Task, "t2")
    assert t2.lab_id == a.lab_id


@pytest.mark.asyncio
async def test_ready_lab_is_reused(session):
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1")
    await seed(session, task_id="t2")
    svc = LabService(session)
    a = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    await svc.mark_ready(
        a.lab_id,
        target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={},
    )
    b = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t2"
    )
    assert b.role == "reuse" and b.reused is True
    assert b.target_url == "http://10.0.0.8:3001"
    assert b.transport_shape == {"protocol": "http"}


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["failed", "expired", "destroyed"])
async def test_terminal_lab_can_be_reclaimed(session, terminal_status):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1")
    await seed(session, task_id="t2")
    svc = LabService(session)
    a = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    lab = await session.get(Lab, a.lab_id)
    lab.status = terminal_status
    await session.commit()

    b = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t2"
    )
    assert b.role == "create" and b.lab_id == a.lab_id
    assert lab.creator_task_id == "t2"


@pytest.mark.asyncio
async def test_cancel_creator_marks_failed(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1")
    svc = LabService(session)
    a = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    await svc.mark_creator_cancelled("t1")
    lab = await session.get(Lab, a.lab_id)
    assert lab.status == "failed"


@pytest.mark.asyncio
async def test_creator_reentry_and_stopped_lab_roles(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1")
    await seed(session, task_id="t2")
    svc = LabService(session)
    first = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    reentered = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    assert reentered.role == "create"

    lab = await session.get(Lab, first.lab_id)
    lab.status = "stopped"
    await session.commit()
    started = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t2"
    )
    assert started.role == "start" and started.reused is True


@pytest.mark.asyncio
async def test_touch_and_live_task_ids(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

    for task_id, status in [
        ("pending-task", "pending"),
        ("queued-task", "queued"),
        ("running-task", "running"),
        ("completed-task", "completed"),
    ]:
        await seed(session, task_id=task_id, status=status)
    svc = LabService(session)
    acquired = await svc.acquire(
        owner_id="u1",
        project_id="p1",
        commit_sha=SHA,
        task_id="running-task",
    )
    for task_id in ("pending-task", "queued-task", "completed-task"):
        await svc.bind_task(task_id, acquired.lab_id)

    lab = await session.get(Lab, acquired.lab_id)
    lab.last_seen_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await session.commit()
    await svc.touch(acquired.lab_id)

    assert lab.last_seen_at.year > 2000
    assert await svc.live_task_ids(acquired.lab_id) == [
        "pending-task",
        "queued-task",
        "running-task",
    ]


@pytest.mark.asyncio
async def test_bad_stored_json_becomes_empty_dict(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1")
    await seed(session, task_id="t2")
    svc = LabService(session)
    acquired = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    lab = await session.get(Lab, acquired.lab_id)
    lab.status = "ready"
    lab.transport_shape = "{bad"
    lab.initial_creds = "null"
    await session.commit()

    reused = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t2"
    )
    assert reused.transport_shape == {}
    assert reused.initial_creds == {}
