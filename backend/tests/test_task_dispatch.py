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
        from app.shared.models import register_models

        register_models()
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
async def test_create_discovery_task_returns_detail(session):
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.schemas import TaskCreateRequest
    from app.contexts.task.service import TaskService

    await _seed_default_llm(session)
    req = TaskCreateRequest(
        project_address="https://github.com/acme/demo",
        task_type="discovery",
    )
    with patch("app.core.celery_app.celery_app.send_task"):
        detail = await TaskService(TaskRepository(session)).create_task(req, "u1")
    assert detail.task_type == "discovery"
    assert detail.vulnerability_description == ""
    assert detail.status == "queued"


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
async def test_create_rejects_local_upload_without_artifact(session):
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.schemas import TaskCreateRequest
    from app.contexts.task.service import TaskService

    req = TaskCreateRequest(
        project_address="upload://local/missing-aaaaaaaaaaaa",
        source_type="local_upload",
        vulnerability_description="demonstration vulnerability",
    )
    with pytest.raises(ValueError, match="上传源码"):
        await TaskService(TaskRepository(session)).create_task(req, "u1")


@pytest.mark.asyncio
async def test_create_task_from_upload_ingests_and_dispatches(session):
    import io
    import zipfile

    from sqlalchemy import select

    from app.contexts.project.models import Project, SourceArtifact
    from app.contexts.project.source_cache import MemorySourceStore
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo/app.py", "print(1)\n")
    data = buf.getvalue()
    store = MemorySourceStore()
    await _seed_default_llm(session)
    with (
        patch("app.core.celery_app.celery_app.send_task"),
        patch(
            "app.contexts.project.service.MinioSourceStore",
            return_value=store,
        ),
    ):
        detail = await TaskService(TaskRepository(session)).create_task_from_upload(
            owner_id="u1",
            filename="demo.zip",
            data=data,
            vulnerability_description="demonstration vulnerability",
        )
    assert detail.source_type == "local_upload"
    assert detail.project_address.startswith("upload://local/")
    task = (await session.execute(select(Task))).scalar_one()
    assert task.source_type == "local_upload"
    project = (await session.execute(select(Project))).scalar_one()
    assert project.source_type == "local_upload"
    artifact = (await session.execute(select(SourceArtifact))).scalar_one()
    assert artifact.ref_type == "upload"
    assert artifact.git_host == "upload"
    assert artifact.object_key.endswith("/original.tar.gz")
    assert project.id in artifact.object_key
    assert project.git_url == f"upload://local/{project.id}"
    assert store.get_bytes(artifact.object_key)


@pytest.mark.asyncio
async def test_create_discovery_task_from_upload_without_vulnerability_description(session):
    import io
    import zipfile

    from app.contexts.project.source_cache import MemorySourceStore
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo/app.py", "print(1)\n")
    await _seed_default_llm(session)
    with (
        patch("app.core.celery_app.celery_app.send_task"),
        patch("app.contexts.project.service.MinioSourceStore", return_value=MemorySourceStore()),
    ):
        detail = await TaskService(TaskRepository(session)).create_task_from_upload(
            owner_id="u1",
            filename="demo.zip",
            data=buf.getvalue(),
            task_type="discovery",
            vulnerability_description=None,
        )
    assert detail.task_type == "discovery"
    assert detail.vulnerability_description == ""


@pytest.mark.asyncio
async def test_list_tasks_applies_task_type_before_pagination(session):
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    session.add_all([
        Task(project_address="verify", task_type="verify", vulnerability_description="known issue description", owner_id="u1"),
        Task(project_address="audit-one", task_type="discovery", vulnerability_description=None, owner_id="u1"),
        Task(project_address="audit-two", task_type="discovery", vulnerability_description=None, owner_id="u1"),
    ])
    await session.commit()

    result = await TaskService(TaskRepository(session)).list_tasks(
        "u1", task_type="discovery", limit=1, offset=0,
    )
    assert result.total == 2
    assert len(result.items) == 1
    assert result.items[0].task_type == "discovery"


@pytest.mark.asyncio
async def test_list_tasks_includes_finding_and_report_summary(session):
    from app.contexts.finding.models import AlertGroup
    from app.contexts.report.models import Report
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    task = Task(
        project_address="https://github.com/acme/shop.git",
        project_ref="main",
        project_ref_type="branch",
        task_type="discovery",
        owner_id="u1",
    )
    session.add(task)
    await session.flush()
    session.add_all([
        AlertGroup(
            task_id=task.id, group_key="g-review", cwe="CWE-89", file_path="a.py",
            representative_finding_id="f1", status="needs_review", member_count=2,
        ),
        AlertGroup(
            task_id=task.id, group_key="g-confirmed", cwe="CWE-78", file_path="b.py",
            representative_finding_id="f2", status="resolved", resolution="confirmed",
        ),
        Report(task_id=task.id, run_id="run-summary", owner_id="u1", status="generated"),
    ])
    await session.commit()

    result = await TaskService(TaskRepository(session)).list_tasks("u1")
    row = result.items[0]
    assert row.project_ref == "main"
    assert row.finding_count == 2
    assert row.pending_review_count == 1
    assert row.confirmed_count == 1
    assert row.report_status == "generated"


@pytest.mark.asyncio
async def test_create_task_from_upload_rejects_duplicate_name(session):
    import io
    import zipfile

    from app.contexts.project.source_cache import MemorySourceStore
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from app.shared.exceptions import ConflictError

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo/app.py", "print(1)\n")
    data = buf.getvalue()
    store = MemorySourceStore()
    await _seed_default_llm(session)
    svc = TaskService(TaskRepository(session))
    with (
        patch("app.core.celery_app.celery_app.send_task"),
        patch("app.contexts.project.service.MinioSourceStore", return_value=store),
    ):
        await svc.create_task_from_upload(
            owner_id="u1",
            filename="demo.zip",
            data=data,
            name="same-app",
            vulnerability_description="demonstration vulnerability",
        )
        with pytest.raises(ConflictError, match="项目名称已存在"):
            await svc.create_task_from_upload(
                owner_id="u1",
                filename="demo.zip",
                data=data,
                name="same-app",
                vulnerability_description="demonstration vulnerability",
            )


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
