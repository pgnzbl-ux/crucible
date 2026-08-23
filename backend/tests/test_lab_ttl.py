"""Lab TTL 与僵死 creating 巡检。"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base
from app.contexts.lab.service import ttl_remaining_seconds

SHA = "a" * 40


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("creating", None),
        ("failed", None),
        ("expired", None),
        ("destroyed", None),
        ("ready", 3500),
        ("stopped", 3500),
    ],
)
def test_ttl_remaining_only_when_lab_is_ready_or_stopped(status, expected):
    now = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)
    last_seen = now - timedelta(seconds=100)
    assert ttl_remaining_seconds(status, last_seen, 3600, now) == expected


def test_ttl_remaining_ready_without_last_seen_is_zero():
    now = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)
    assert ttl_remaining_seconds("ready", None, 3600, now) == 0


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


async def ready_lab(session, *, task_status="completed"):
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task

    await seed(session, task_id="t1")
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    await svc.mark_ready(
        result.lab_id,
        target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={},
    )
    (await session.get(Task, "t1")).status = task_status
    await session.commit()
    return svc, result


@pytest.mark.asyncio
async def test_ttl_expires_ready_lab_without_live_tasks(session):
    from app.contexts.lab.models import Lab

    svc, result = await ready_lab(session)
    now = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
    lab = await session.get(Lab, result.lab_id)
    lab.last_seen_at = now - timedelta(seconds=3600)
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        expired = await svc.expire_silent_labs(now=now)

    assert expired == [result.lab_id]
    down.assert_awaited_once_with(result.compose_project)
    assert (await session.get(Lab, result.lab_id)).status == "expired"


@pytest.mark.asyncio
async def test_ttl_skips_lab_with_running_task(session):
    from app.contexts.lab.models import Lab

    svc, result = await ready_lab(session, task_status="running")
    now = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
    lab = await session.get(Lab, result.lab_id)
    lab.last_seen_at = now - timedelta(hours=2)
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        expired = await svc.expire_silent_labs(now=now)

    assert expired == []
    down.assert_not_awaited()
    assert (await session.get(Lab, result.lab_id)).status == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ready", "stopped"])
async def test_missing_last_seen_is_immediately_expired(session, status):
    from app.contexts.lab.models import Lab

    svc, result = await ready_lab(session)
    lab = await session.get(Lab, result.lab_id)
    lab.status = status
    lab.last_seen_at = None
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        expired = await svc.expire_silent_labs(
            now=datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        )

    assert expired == [result.lab_id]
    down.assert_awaited_once_with(result.compose_project)


@pytest.mark.asyncio
async def test_creating_without_live_task_is_failed_and_cleaned(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task

    await seed(session)
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    (await session.get(Task, "t1")).status = "cancelled"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down",
        new_callable=AsyncMock,
        side_effect=RuntimeError("docker unavailable"),
    ) as down:
        failed = await svc.fail_stale_creating()

    assert failed == [result.lab_id]
    down.assert_awaited_once_with(result.compose_project)
    assert (await session.get(Lab, result.lab_id)).status == "failed"


@pytest.mark.asyncio
async def test_failed_lab_runtime_is_cleaned_by_sweeper(session):
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task

    await seed(session)
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    await svc.mark_failed(result.lab_id, "upload failed")
    (await session.get(Task, "t1")).status = "failed"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down",
        new_callable=AsyncMock,
    ) as down:
        cleaned = await svc.cleanup_terminal_runtimes()

    assert cleaned == [result.lab_id]
    down.assert_awaited_once_with(result.compose_project)


@pytest.mark.asyncio
async def test_cleanup_does_not_promote_expired_without_http_probe(session):
    from app.contexts.lab.models import Lab

    svc, result = await ready_lab(session)
    lab = await session.get(Lab, result.lab_id)
    lab.status = "expired"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=[{"name": "web", "status": "Up 4 minutes", "ports": "", "image": "x"}],
    ), patch(
        "app.contexts.lab.docker_ops.compose_down",
        new_callable=AsyncMock,
    ) as down:
        cleaned = await svc.cleanup_terminal_runtimes()

    assert cleaned == [result.lab_id]
    down.assert_awaited_once_with(result.compose_project)
    await session.refresh(lab)
    assert lab.status == "expired"


@pytest.mark.asyncio
async def test_ttl_down_failure_does_not_block_other_labs(session):
    from app.contexts.lab.models import Lab

    first_service, first = await ready_lab(session)
    await seed(session, task_id="t2", status="completed")
    second = await first_service.acquire(
        owner_id="u1", project_id="p1", commit_sha="b" * 40, task_id="t2"
    )
    await first_service.mark_ready(
        second.lab_id,
        target_url="http://10.0.0.9:3002",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={},
    )
    (await session.get(Lab, first.lab_id)).last_seen_at = None
    (await session.get(Lab, second.lab_id)).last_seen_at = None
    await session.commit()

    async def down_one_fails(project: str) -> None:
        if project == first.compose_project:
            raise RuntimeError("first down failed")

    with patch(
        "app.contexts.lab.docker_ops.compose_down",
        new_callable=AsyncMock,
        side_effect=down_one_fails,
    ) as down:
        expired = await first_service.expire_silent_labs()

    assert set(expired) == {first.lab_id, second.lab_id}
    assert down.await_count == 2
    assert (await session.get(Lab, first.lab_id)).status == "expired"
    assert (await session.get(Lab, second.lab_id)).status == "expired"


@pytest.mark.asyncio
async def test_ttl_restores_ready_when_task_appears_after_claim(session):
    from app.contexts.lab.models import Lab

    svc, result = await ready_lab(session)
    lab = await session.get(Lab, result.lab_id)
    lab.last_seen_at = None
    await session.commit()

    with patch.object(
        svc,
        "live_task_ids",
        new_callable=AsyncMock,
        side_effect=[[], ["t1"]],
    ), patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        expired = await svc.expire_silent_labs()

    assert expired == []
    down.assert_not_awaited()
    assert (await session.get(Lab, result.lab_id)).status == "ready"


@pytest.mark.asyncio
async def test_creating_restores_claim_when_task_appears_after_claim(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task

    await seed(session)
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    (await session.get(Task, "t1")).status = "cancelled"
    await session.commit()

    with patch.object(
        svc,
        "live_task_ids",
        new_callable=AsyncMock,
        side_effect=[[], ["t1"]],
    ), patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        failed = await svc.fail_stale_creating()

    assert failed == []
    down.assert_not_awaited()
    assert (await session.get(Lab, result.lab_id)).status == "creating"


@pytest.mark.asyncio
async def test_creating_waiter_does_not_keep_dead_creator_lease_alive(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task

    await seed(session)
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    (await session.get(Task, "t1")).status = "cancelled"
    await session.commit()

    with patch.object(
        svc,
        "live_task_ids",
        new_callable=AsyncMock,
        side_effect=[["waiter-task"], ["waiter-task"]],
    ), patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        failed = await svc.fail_stale_creating()

    assert failed == [result.lab_id]
    down.assert_awaited_once_with(result.compose_project)
    assert (await session.get(Lab, result.lab_id)).status == "failed"


@pytest.mark.asyncio
async def test_compose_down_uses_project_only_command():
    from app.contexts.lab.docker_ops import compose_down

    completed = MagicMock(returncode=0, stdout="", stderr="")
    with patch("app.contexts.lab.docker_ops.subprocess.run", return_value=completed) as run:
        await compose_down("crucible-lab-abc")

    run.assert_called_once_with(
        [
            "docker",
            "compose",
            "-p",
            "crucible-lab-abc",
            "down",
            "-v",
            "--remove-orphans",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.asyncio
async def test_compose_down_surfaces_docker_failure():
    from app.contexts.lab.docker_ops import compose_down

    completed = MagicMock(returncode=1, stdout="", stderr="boom")
    with patch("app.contexts.lab.docker_ops.subprocess.run", return_value=completed):
        with pytest.raises(subprocess.CalledProcessError):
            await compose_down("crucible-lab-abc")
