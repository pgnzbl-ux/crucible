"""Lab 管理 API、占用保护与 Docker 操作。"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
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


async def ready_lab(session, *, task_status="completed"):
    from app.contexts.lab.service import LabService
    from app.contexts.task.models import Task

    await seed(session, status=task_status)
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
    return svc, result, await session.get(Task, "t1")


@pytest.mark.asyncio
async def test_stop_rejected_when_task_running(session):
    from app.contexts.lab.errors import LabBusyError
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1", status="running")
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
    with patch(
        "app.contexts.lab.docker_ops.compose_stop", new_callable=AsyncMock
    ) as stop:
        with pytest.raises(LabBusyError) as exc_info:
            await svc.stop_lab(result.lab_id, owner_id="u1")

    assert "t1" in exc_info.value.task_ids
    stop.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "method_name", "docker_operation"),
    [
        ("failed", "stop_lab", "compose_stop"),
        ("creating", "destroy_lab", "compose_down"),
        ("ready", "start_lab", "compose_start"),
    ],
)
async def test_management_rejects_invalid_state_before_docker(
    session, status, method_name, docker_operation
):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = status
    await session.commit()

    with patch(
        f"app.contexts.lab.docker_ops.{docker_operation}",
        new_callable=AsyncMock,
    ) as docker:
        with pytest.raises(ValueError, match=status):
            await getattr(svc, method_name)(result.lab_id, owner_id="u1")

    docker.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_rechecks_live_tasks_immediately_before_docker(session):
    from app.contexts.lab.errors import LabBusyError

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    await session.commit()

    with patch.object(
        svc,
        "live_task_ids",
        new_callable=AsyncMock,
        side_effect=[[], ["t1"]],
    ), patch(
        "app.contexts.lab.docker_ops.compose_stop", new_callable=AsyncMock
    ) as stop:
        with pytest.raises(LabBusyError) as exc_info:
            await svc.stop_lab(result.lab_id, owner_id="u1")

    assert exc_info.value.task_ids == ["t1"]
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_restores_status_when_task_binds_after_creating_commit(
    session, tmp_path
):
    from app.contexts.lab.errors import LabBusyError
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.workdir = str(tmp_path).replace("\\", "/")
    compose_file = tmp_path / ".vuln-env" / "docker-compose.yml"
    compose_file.parent.mkdir()
    compose_file.write_text("services: {}", encoding="utf-8")
    await session.commit()

    with patch.object(
        svc,
        "live_task_ids",
        new_callable=AsyncMock,
        side_effect=[[], [], ["t1"]],
    ), patch(
        "app.contexts.lab.docker_ops.compose_up_build", new_callable=AsyncMock
    ) as rebuild:
        with pytest.raises(LabBusyError) as exc_info:
            await svc.rebuild_lab(result.lab_id, owner_id="u1")

    assert exc_info.value.task_ids == ["t1"]
    assert lab.status == "ready"
    rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_container_action_rejects_creating_lab_before_docker(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "creating"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.container_restart", new_callable=AsyncMock
    ) as restart:
        with pytest.raises(ValueError, match="creating"):
            await svc.container_action(
                result.lab_id, "web", action="restart", owner_id="u1"
            )

    restart.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_grouped_by_project(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

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
    lab = await session.get(Lab, result.lab_id)
    lab.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=[],
    ):
        grouped = await svc.list_grouped("u1")

    assert grouped[0]["project_id"] == "p1"
    assert grouped[0]["project_name"] == "demo"
    assert grouped[0]["labs"][0]["id"] == result.lab_id
    assert grouped[0]["labs"][0]["containers"] == []
    assert grouped[0]["labs"][0]["live_task_count"] >= 1
    assert 3580 <= grouped[0]["labs"][0]["ttl_remaining_seconds"] <= 3590


@pytest.mark.asyncio
async def test_other_owner_lab_is_not_found(session):
    from app.contexts.lab.errors import LabNotFoundError

    svc, result, _ = await ready_lab(session)
    with pytest.raises(LabNotFoundError):
        await svc.get_detail(result.lab_id, owner_id="u2")


@pytest.mark.asyncio
async def test_management_writes_update_state_and_touch(session, tmp_path):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.workdir = str(tmp_path).replace("\\", "/")
    lab.last_seen_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    compose_file = tmp_path / ".vuln-env" / "docker-compose.yml"
    compose_file.parent.mkdir()
    compose_file.write_text("services: {}", encoding="utf-8")
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_stop", new_callable=AsyncMock
    ) as stop, patch(
        "app.contexts.lab.docker_ops.compose_start",
        new_callable=AsyncMock,
        return_value=True,
    ) as start, patch(
        "app.contexts.lab.docker_ops.compose_up_build", new_callable=AsyncMock
    ) as rebuild, patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        await svc.stop_lab(result.lab_id, owner_id="u1")
        assert lab.status == "stopped"
        await svc.start_lab(result.lab_id, owner_id="u1")
        assert lab.status == "ready"
        await svc.rebuild_lab(result.lab_id, owner_id="u1")
        assert lab.status == "ready"
        await svc.destroy_lab(result.lab_id, owner_id="u1")

    assert lab.status == "destroyed"
    assert lab.last_seen_at.year > 2000
    stop.assert_awaited_once_with(result.compose_project)
    start.assert_awaited_once_with(result.compose_project)
    rebuild.assert_awaited_once_with(
        result.compose_project,
        str(compose_file).replace("\\", "/"),
        str(tmp_path).replace("\\", "/"),
    )
    down.assert_awaited_once_with(result.compose_project)


@pytest.mark.asyncio
async def test_rebuild_without_recipe_has_exact_message(session):
    svc, result, task = await ready_lab(session)
    task.status = "completed"
    await session.commit()

    with pytest.raises(ValueError, match="^缺少配方，请从验证任务重新创建$"):
        await svc.rebuild_lab(result.lab_id, owner_id="u1")


@pytest.mark.asyncio
async def test_container_action_checks_owner_and_touches(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.last_seen_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.container_restart", new_callable=AsyncMock
    ) as restart:
        await svc.container_action(
            result.lab_id, "web", action="restart", owner_id="u1"
        )

    restart.assert_awaited_once_with("web", result.compose_project)
    assert lab.last_seen_at.year > 2000


@pytest.mark.asyncio
async def test_docker_container_listing_and_membership():
    from app.contexts.lab.docker_ops import (
        assert_container_in_project,
        list_containers,
    )

    output = "web\tUp 2 minutes\t0.0.0.0:3001->3000/tcp\tnginx:latest\n"
    completed = MagicMock(returncode=0, stdout=output, stderr="")
    with patch("app.contexts.lab.docker_ops.subprocess.run", return_value=completed):
        containers = await list_containers("crucible-lab-abc")
        await assert_container_in_project("web", "crucible-lab-abc")

    assert containers == [
        {
            "name": "web",
            "status": "Up 2 minutes",
            "ports": "0.0.0.0:3001->3000/tcp",
            "image": "nginx:latest",
        }
    ]


@pytest.mark.asyncio
async def test_container_command_surfaces_docker_failure():
    from app.contexts.lab.docker_ops import container_stop

    listed = [{"name": "web", "status": "Up", "ports": "", "image": "nginx"}]
    failed = MagicMock(returncode=1, stdout="", stderr="boom")
    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=listed,
    ), patch("app.contexts.lab.docker_ops.subprocess.run", return_value=failed):
        with pytest.raises(subprocess.CalledProcessError):
            await container_stop("web", "crucible-lab-abc")


def test_lab_router_busy_returns_409():
    from app.contexts.lab.api import get_lab_service
    from app.contexts.lab.errors import LabBusyError
    from app.main import create_app
    from app.shared.deps import get_current_user_id

    service = MagicMock()
    service.stop_lab = AsyncMock(side_effect=LabBusyError(["t1"]))
    app = create_app()
    app.dependency_overrides[get_lab_service] = lambda: service
    app.dependency_overrides[get_current_user_id] = lambda: "u1"

    with TestClient(app) as client:
        response = client.post("/api/v1/labs/lab-1/actions/stop")

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "LAB_IN_USE",
            "message": "靶场正被运行中的任务占用",
            "task_ids": ["t1"],
        }
    }
