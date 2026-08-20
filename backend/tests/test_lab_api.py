"""Lab 管理 API、占用保护与 Docker 操作。"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base

SHA = "a" * 40


def _posix_lab_workdir(lab_id: str) -> str:
    return f"/tmp/crucible/audit/labs/{lab_id}"


def _patch_lab_workdir_tmp(tmp_path: Path):
    return patch(
        "app.core.agent_runner.normalize_host_workdir",
        lambda _path: str(tmp_path),
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
        ("creating", "start_lab", "compose_start"),
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
async def test_list_grouped_creating_lab_has_no_ttl(session):
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1")
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )

    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=[],
    ):
        grouped = await svc.list_grouped("u1")

    lab = grouped[0]["labs"][0]
    assert lab["id"] == result.lab_id
    assert lab["status"] == "creating"
    assert lab["ttl_remaining_seconds"] is None


@pytest.mark.asyncio
async def test_get_detail_does_not_refresh_ttl_while_creating(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1")
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    lab = await session.get(Lab, result.lab_id)
    frozen = datetime(2000, 1, 1, tzinfo=timezone.utc)
    lab.last_seen_at = frozen
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=[],
    ):
        detail = await svc.get_detail(result.lab_id, owner_id="u1")

    await session.refresh(lab)
    assert lab.last_seen_at.year == 2000
    assert detail["ttl_remaining_seconds"] is None
    assert detail["status"] == "creating"


@pytest.mark.asyncio
async def test_destroy_creating_lab_without_live_task(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "creating"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        status = await svc.destroy_lab(result.lab_id, owner_id="u1")

    assert status == "destroyed"
    down.assert_awaited_once_with(result.compose_project)
    assert (await session.get(Lab, result.lab_id)).status == "destroyed"


@pytest.mark.asyncio
async def test_destroy_creating_lab_cancels_live_tasks(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService

    await seed(session, task_id="t1", status="running")
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    lab = await session.get(Lab, result.lab_id)
    assert lab.status == "creating"

    with patch(
        "app.contexts.task.service.TaskService.cancel_task",
        new_callable=AsyncMock,
    ) as cancel, patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        status = await svc.destroy_lab(result.lab_id, owner_id="u1")

    assert status == "destroyed"
    cancel.assert_awaited_once_with("t1", "u1")
    down.assert_awaited_once_with(result.compose_project)


@pytest.mark.asyncio
async def test_destroy_rebuilding_lab(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "rebuilding"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        status = await svc.destroy_lab(result.lab_id, owner_id="u1")

    assert status == "destroyed"
    down.assert_awaited_once_with(result.compose_project)
    assert (await session.get(Lab, result.lab_id)).status == "destroyed"


@pytest.mark.asyncio
async def test_rebuild_creating_lab_when_idle(session, tmp_path):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "creating"
    lab.workdir = _posix_lab_workdir(result.lab_id)
    compose_file = tmp_path / ".vuln-env" / "docker-compose.yml"
    compose_file.parent.mkdir()
    compose_file.write_text("services: {}", encoding="utf-8")
    await session.commit()

    with _patch_lab_workdir_tmp(tmp_path), patch(
        "app.contexts.lab.docker_ops.compose_up_build", new_callable=AsyncMock
    ) as rebuild:
        status = await svc.rebuild_lab(result.lab_id, owner_id="u1")

    assert status == "ready"
    rebuild.assert_awaited_once()


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
    lab.workdir = _posix_lab_workdir(result.lab_id)
    compose_file = tmp_path / ".vuln-env" / "docker-compose.yml"
    compose_file.parent.mkdir()
    compose_file.write_text("services: {}", encoding="utf-8")
    await session.commit()

    with patch.object(
        svc,
        "live_task_ids",
        new_callable=AsyncMock,
        side_effect=[[], [], ["t1"]],
    ), _patch_lab_workdir_tmp(tmp_path), patch(
        "app.contexts.lab.docker_ops.compose_up_build", new_callable=AsyncMock
    ) as rebuild:
        with pytest.raises(LabBusyError) as exc_info:
            await svc.rebuild_lab(result.lab_id, owner_id="u1")

    assert exc_info.value.task_ids == ["t1"]
    assert lab.status == "ready"
    rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_uses_commit_sha_when_cloning(session, tmp_path):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.workdir = _posix_lab_workdir(result.lab_id)
    await session.commit()

    async def fake_download(**kwargs):
        dest = kwargs["dest_workdir"]
        os.makedirs(os.path.join(dest, ".vuln-env"), exist_ok=True)
        path = os.path.join(dest, ".vuln-env", "docker-compose.yml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("services: {}\n")
        return {"compose_path": ".vuln-env/docker-compose.yml"}

    svc.download_recipe = fake_download
    with _patch_lab_workdir_tmp(tmp_path), patch(
        "app.contexts.lab.docker_ops.compose_up_build", new_callable=AsyncMock
    ), patch(
        "app.core.agent_runner.git_clone_to_workdir", return_value=(True, "")
    ) as clone:
        await svc.rebuild_lab(result.lab_id, owner_id="u1")

    clone.assert_called_once()
    assert clone.call_args.args[2] == SHA
    assert clone.call_args.kwargs["ref_type"] == "commit"
    assert os.path.isdir(clone.call_args.args[0])


@pytest.mark.asyncio
async def test_rebuild_upload_project_uses_minio_not_git_clone(session, tmp_path):
    import io
    import zipfile

    from app.contexts.identity.models import User
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService
    from app.contexts.project.models import Project
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_acquire import SourceAcquireResult
    from app.contexts.project.source_cache import MemorySourceStore
    from app.contexts.task.models import Task

    upload_sha = "c" * 64
    session.add(
        User(id="u1", email="u1@x.test", password_hash="x", display_name="u1")
    )
    store = MemorySourceStore()
    proj_svc = ProjectService(ProjectRepository(session))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo/app.py", "print(1)\n")
    project, _artifact = await proj_svc.ingest_uploaded_source(
        owner_id="u1",
        filename="demo.zip",
        data=buf.getvalue(),
        name="upload-demo",
        store=store,
    )
    session.add(
        Task(
            id="t-up",
            project_address=project.git_url,
            source_type="local_upload",
            vulnerability_description="demonstration vulnerability",
            owner_id="u1",
            project_id=project.id,
            status="completed",
        )
    )
    lab_svc = LabService(session)
    acquired = await lab_svc.acquire(
        owner_id="u1",
        project_id=project.id,
        commit_sha=upload_sha,
        task_id="t-up",
    )
    await lab_svc.mark_ready(
        acquired.lab_id,
        target_url="http://127.0.0.1:8080",
        compose_path="demo/.vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={},
    )
    lab = await session.get(Lab, acquired.lab_id)
    lab.workdir = _posix_lab_workdir(acquired.lab_id)
    await session.commit()

    ok_result = SourceAcquireResult(
        ok=True,
        origin="upload",
        git_url_normalized=project.git_url,
        git_host="upload",
        project_key=f"local/{project.id}",
        repo_dirname="demo",
        ref_type="upload",
        ref_name="local",
        commit_sha=upload_sha,
        project_path=str(tmp_path / "demo"),
    )

    with _patch_lab_workdir_tmp(tmp_path), patch(
        "app.contexts.project.source_acquire.acquire_uploaded_source",
        return_value=ok_result,
    ) as acquire_upload, patch(
        "app.core.agent_runner.git_clone_to_workdir",
    ) as clone:
        repo_dirname, err = await lab_svc._ensure_rebuild_source(lab, str(tmp_path))

    assert err is None
    assert repo_dirname == "demo"
    acquire_upload.assert_called_once()
    clone.assert_not_called()


@pytest.mark.asyncio
async def test_rebuild_accepts_posix_workdir_path(session, tmp_path, monkeypatch):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    resolved = tmp_path / "labs" / result.lab_id
    lab.workdir = f"/tmp/crucible/audit/labs/{result.lab_id}"
    await session.commit()

    monkeypatch.setattr(
        "app.core.agent_runner.normalize_host_workdir",
        lambda _path: str(resolved),
    )

    async def fake_download(**kwargs):
        dest = kwargs["dest_workdir"]
        os.makedirs(os.path.join(dest, ".vuln-env"), exist_ok=True)
        path = os.path.join(dest, ".vuln-env", "docker-compose.yml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("services: {}\n")
        return {"compose_path": ".vuln-env/docker-compose.yml"}

    svc.download_recipe = fake_download
    with patch("app.contexts.lab.docker_ops.compose_up_build", new_callable=AsyncMock) as up, patch(
        "app.core.agent_runner.git_clone_to_workdir", return_value=(True, "")
    ) as clone:
        status = await svc.rebuild_lab(result.lab_id, owner_id="u1")

    assert status == "ready"
    assert resolved.is_dir()
    clone.assert_called_once()
    assert clone.call_args.args[0] == str(resolved)
    up.assert_awaited_once()
    assert up.await_args.args[2] == str(resolved)


def test_normalize_host_workdir_rejects_non_posix():
    from app.core.agent_runner import normalize_host_workdir

    with pytest.raises(ValueError, match="POSIX"):
        normalize_host_workdir("D:/tmp/crucible/audit")
    with pytest.raises(ValueError, match="POSIX"):
        normalize_host_workdir("relative/workdir")


def test_normalize_host_workdir_returns_absolute_path():
    from app.core.agent_runner import normalize_host_workdir

    resolved = normalize_host_workdir("/tmp/crucible/audit/labs/lab-1")
    assert os.path.isabs(resolved)
    assert resolved.replace("\\", "/").endswith("tmp/crucible/audit/labs/lab-1")


@pytest.mark.asyncio
async def test_fail_stale_rebuilding_skips_recent_manual_rebuild(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService, _MANUAL_REBUILD_STALE_SECONDS

    await seed(session)
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    lab = await session.get(Lab, result.lab_id)
    lab.status = "rebuilding"
    lab.last_seen_at = datetime.now(timezone.utc)
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        failed = await svc.fail_stale_rebuilding()

    assert failed == []
    down.assert_not_awaited()
    assert (await session.get(Lab, result.lab_id)).status == "rebuilding"


@pytest.mark.asyncio
async def test_fail_stale_rebuilding_marks_timeout_lab_failed(session):
    from app.contexts.lab.models import Lab
    from app.contexts.lab.service import LabService, _MANUAL_REBUILD_STALE_SECONDS

    await seed(session)
    svc = LabService(session)
    result = await svc.acquire(
        owner_id="u1", project_id="p1", commit_sha=SHA, task_id="t1"
    )
    lab = await session.get(Lab, result.lab_id)
    lab.status = "rebuilding"
    lab.last_seen_at = datetime.now(timezone.utc) - timedelta(
        seconds=_MANUAL_REBUILD_STALE_SECONDS + 60
    )
    (await session.get(__import__("app.contexts.task.models", fromlist=["Task"]).Task, "t1")).status = "completed"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.compose_down", new_callable=AsyncMock
    ) as down:
        failed = await svc.fail_stale_rebuilding()

    assert failed == [result.lab_id]
    down.assert_awaited_once()
    refreshed = await session.get(Lab, result.lab_id)
    assert refreshed.status == "failed"
    assert refreshed.error_message == "手动重建超时"


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

    running = [
        {
            "name": "web",
            "status": "running",
            "state": "running",
            "ports": "",
            "image": "x",
        }
    ]
    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=running,
    ):
        grouped = await svc.list_grouped("u1")

    assert grouped[0]["project_id"] == "p1"
    assert grouped[0]["project_name"] == "demo"
    assert grouped[0]["labs"][0]["id"] == result.lab_id
    assert grouped[0]["labs"][0]["status"] == "ready"
    assert grouped[0]["labs"][0]["containers"] == running
    assert grouped[0]["labs"][0]["live_task_count"] >= 1
    assert 3580 <= grouped[0]["labs"][0]["ttl_remaining_seconds"] <= 3590


@pytest.mark.parametrize(
    ("db_status", "runtime", "live", "expected"),
    [
        ("expired", "running", 0, "ready"),
        ("expired", "running", 1, "ready"),
        ("stopped", "running", 0, "ready"),
        ("ready", "exited", 0, "stopped"),
        ("ready", "none", 0, "expired"),
        ("stopped", "none", 0, "expired"),
        ("expired", "exited", 0, "stopped"),
        ("ready", "none", 1, None),
        ("ready", "exited", 1, None),
        ("creating", "running", 1, None),
        ("rebuilding", "running", 0, None),
        ("failed", "running", 0, None),
        ("destroyed", "running", 0, None),
        ("expired", "none", 0, None),
        ("ready", "running", 0, None),
    ],
)
def test_next_aligned_lab_status(db_status, runtime, live, expected):
    from app.contexts.lab.service import next_aligned_lab_status

    assert next_aligned_lab_status(db_status, runtime, live_task_count=live) == expected


def test_container_runtime_kind_prefers_docker_state():
    from app.contexts.lab.service import container_runtime_kind

    assert (
        container_runtime_kind(
            [{"name": "web", "state": "running", "status": "Up 2 minutes"}]
        )
        == "running"
    )
    assert (
        container_runtime_kind(
            [{"name": "web", "state": "exited", "status": "Exited (0) 3 minutes ago"}]
        )
        == "exited"
    )
    assert container_runtime_kind([]) == "none"


@pytest.mark.asyncio
async def test_list_grouped_promotes_expired_when_containers_running(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "expired"
    await session.commit()

    running = [
        {
            "name": "web",
            "status": "running",
            "state": "running",
            "ports": "3001->3000",
            "image": "x",
        }
    ]
    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=running,
    ):
        grouped = await svc.list_grouped("u1")

    assert grouped[0]["labs"][0]["status"] == "ready"
    await session.refresh(lab)
    assert lab.status == "ready"


@pytest.mark.asyncio
async def test_container_start_promotes_stopped_lab_to_ready(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "stopped"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.container_start", new_callable=AsyncMock
    ), patch(
        "app.contexts.lab.docker_ops.assert_container_in_project",
        new_callable=AsyncMock,
    ), patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=[
            {
                "name": "web",
                "status": "running",
                "state": "running",
                "ports": "",
                "image": "x",
            }
        ],
    ):
        status = await svc.container_action(
            result.lab_id, "web", action="start", owner_id="u1"
        )

    assert status == "ready"
    await session.refresh(lab)
    assert lab.status == "ready"


@pytest.mark.asyncio
async def test_start_lab_starts_exited_containers_when_db_says_expired(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "expired"
    await session.commit()

    exited = [
        {
            "name": "web",
            "status": "exited",
            "state": "exited",
            "ports": "",
            "image": "x",
        }
    ]
    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=exited,
    ), patch(
        "app.contexts.lab.docker_ops.compose_start",
        new_callable=AsyncMock,
        return_value=True,
    ) as start:
        status = await svc.start_lab(result.lab_id, owner_id="u1")

    assert status == "ready"
    start.assert_awaited_once_with(result.compose_project)
    await session.refresh(lab)
    assert lab.status == "ready"


@pytest.mark.asyncio
async def test_start_lab_is_noop_when_containers_already_running(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "expired"
    await session.commit()

    running = [
        {
            "name": "web",
            "status": "running",
            "state": "running",
            "ports": "",
            "image": "x",
        }
    ]
    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=running,
    ), patch(
        "app.contexts.lab.docker_ops.compose_start",
        new_callable=AsyncMock,
        return_value=True,
    ) as start:
        status = await svc.start_lab(result.lab_id, owner_id="u1")

    assert status == "ready"
    start.assert_not_awaited()
    await session.refresh(lab)
    assert lab.status == "ready"


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
    lab.workdir = _posix_lab_workdir(result.lab_id)
    lab.last_seen_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    compose_file = tmp_path / ".vuln-env" / "docker-compose.yml"
    compose_file.parent.mkdir()
    compose_file.write_text("services: {}", encoding="utf-8")
    await session.commit()

    with _patch_lab_workdir_tmp(tmp_path), patch(
        "app.contexts.lab.docker_ops.compose_stop", new_callable=AsyncMock
    ) as stop, patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=[
            {
                "name": "web",
                "status": "exited",
                "state": "exited",
                "ports": "",
                "image": "x",
            }
        ],
    ), patch(
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
        str(compose_file.resolve()),
        str(Path(tmp_path).resolve()),
    )
    down.assert_awaited_once_with(result.compose_project)


@pytest.mark.asyncio
async def test_start_lab_without_containers_marks_expired(session):
    from app.contexts.lab.models import Lab

    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(Lab, result.lab_id)
    lab.status = "stopped"
    await session.commit()

    with patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=[],
    ) as listed, patch(
        "app.contexts.lab.docker_ops.compose_start",
        new_callable=AsyncMock,
        return_value=False,
    ) as start:
        with pytest.raises(ValueError, match="靶场容器已不存在，请重建"):
            await svc.start_lab(result.lab_id, owner_id="u1")

    listed.assert_awaited_once_with(result.compose_project)
    start.assert_not_awaited()
    await session.refresh(lab)
    assert lab.status == "expired"


@pytest.mark.asyncio
async def test_rebuild_without_recipe_has_exact_message(session, tmp_path):
    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(__import__("app.contexts.lab.models", fromlist=["Lab"]).Lab, result.lab_id)
    lab.workdir = _posix_lab_workdir(result.lab_id)
    await session.commit()
    svc.download_recipe = AsyncMock(return_value=None)

    with _patch_lab_workdir_tmp(tmp_path), patch(
        "app.core.agent_runner.git_clone_to_workdir",
        return_value=(True, ""),
    ) as clone:
        with pytest.raises(ValueError, match="^缺少配方，请从验证任务重新创建$"):
            await svc.rebuild_lab(result.lab_id, owner_id="u1")
    clone.assert_called_once()


@pytest.mark.asyncio
async def test_rebuild_downloads_recipe_when_file_missing(session, tmp_path):
    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(__import__("app.contexts.lab.models", fromlist=["Lab"]).Lab, result.lab_id)
    lab.workdir = _posix_lab_workdir(result.lab_id)
    await session.commit()

    def fake_clone(workdir, git_url, ref, dest_dirname, **kwargs):
        dest = os.path.join(workdir, dest_dirname)
        os.makedirs(dest, exist_ok=True)
        (Path(dest) / "pom.xml").write_text("<project/>", encoding="utf-8")
        return True, ""

    async def fake_download(**kwargs):
        dest = kwargs["dest_workdir"]
        os.makedirs(os.path.join(dest, ".vuln-env"), exist_ok=True)
        path = os.path.join(dest, ".vuln-env", "docker-compose.yml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("services: {}\n")
        return {"compose_path": ".vuln-env/docker-compose.yml"}

    svc.download_recipe = fake_download
    with _patch_lab_workdir_tmp(tmp_path), patch(
        "app.contexts.lab.docker_ops.compose_up_build", new_callable=AsyncMock
    ) as up, patch(
        "app.core.agent_runner.git_clone_to_workdir", side_effect=fake_clone
    ):
        status = await svc.rebuild_lab(result.lab_id, owner_id="u1")
    assert status == "ready"
    up.assert_awaited_once()
    up_file = Path(up.await_args.args[1])
    workdir_root = Path(up.await_args.args[2])
    assert up_file == workdir_root / "b" / ".vuln-env" / "docker-compose.yml"
    assert workdir_root == Path(tmp_path).resolve()


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
    ) as restart, patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=[
            {
                "name": "web",
                "status": "Up 2 minutes",
                "ports": "0.0.0.0:3001->3000/tcp",
                "image": "nginx:latest",
            }
        ],
    ):
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

    listed = [
        {
            "name": "web",
            "status": "running",
            "state": "running",
            "ports": "",
            "image": "nginx",
        }
    ]
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
    body = response.json()
    assert body["detail"] == {
        "code": "LAB_IN_USE",
        "message": "靶场正被运行中的任务占用",
        "task_ids": ["t1"],
    }
    assert body["error"]["code"] == "LAB_IN_USE"
    assert body["error"]["message"] == "靶场正被运行中的任务占用"
    assert body["error"]["details"]["task_ids"] == ["t1"]
