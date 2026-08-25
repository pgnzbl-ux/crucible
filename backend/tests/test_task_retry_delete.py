"""任务 retry（从节点 0 整条重跑）测试。"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.project.models import Project  # noqa: F401
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        from app.contexts.discovery.models import ScanRun  # noqa: F401
        from app.contexts.finding.models import AlertGroup, RawFinding  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def open_session(*, seed_llm: bool = True):
        async with factory() as session:
            if seed_llm:
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
            yield session

    yield open_session
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_creates_new_run_without_reusing_nodes(session_factory):
    """retry 新建 run，不拷贝上一 run 的 NodeRun，从源码获取整条重跑。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        old_run = TaskRun(task_id=task.id, status="failed")
        session.add(old_run)
        await session.flush()
        for i, (key, st) in enumerate(
            [("source", "completed"), ("profile", "completed"), ("env_ready", "failed")]
        ):
            session.add(
                NodeRun(
                    run_id=old_run.id,
                    task_id=task.id,
                    node_index=i,
                    node_key=key,
                    status=st,
                    output_json='{"x":1}' if st == "completed" else "{}",
                )
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.send_task"):
            new_run_id = await svc.retry_task(task.id, "u1")

        from sqlalchemy import select
        new_nodes = (
            await session.execute(
                select(NodeRun).where(NodeRun.run_id == new_run_id).order_by(NodeRun.node_index)
            )
        ).scalars().all()
        assert new_run_id != old_run.id
        assert new_nodes == []
        old_nodes = (
            await session.execute(select(NodeRun).where(NodeRun.run_id == old_run.id))
        ).scalars().all()
        assert len(old_nodes) == 3


@pytest.mark.asyncio
async def test_retry_from_triage_copies_screen_without_rerun(session_factory):
    """P0：screen=completed + triage=failed → from_node=triage 拷贝 screen，不重跑轻量快审。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch
    from sqlalchemy import select

    async with session_factory() as session:
        task = Task(
            project_address="x",
            task_type="discovery",
            vulnerability_description=None,
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        old_run = TaskRun(task_id=task.id, status="failed")
        session.add(old_run)
        await session.flush()
        # 用 registry 真索引；拷贝时会按 key 重写
        prior = [
            ("source", "completed", '{"source_path":"/p"}'),
            ("profile", "completed", '{"is_web":true}'),
            ("scan_gitleaks", "completed", "{}"),
            ("scan_osv", "completed", "{}"),
            ("scan_semgrep", "completed", "{}"),
            ("api_inventory", "completed", "{}"),
            ("api_hunt", "completed", "{}"),
            ("env_ready", "completed", "{}"),
            ("cluster", "completed", "{}"),
            ("screen", "completed", '{"escalated_count":2,"fast_model_count":3}'),
            ("triage", "failed", "{}"),
        ]
        for i, (key, st, out) in enumerate(prior):
            session.add(
                NodeRun(
                    run_id=old_run.id,
                    task_id=task.id,
                    node_index=i,
                    node_key=key,
                    status=st,
                    output_json=out,
                )
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.send_task"):
            new_run_id = await svc.retry_task(task.id, "u1", from_node="triage")

        new_nodes = (
            await session.execute(
                select(NodeRun).where(NodeRun.run_id == new_run_id).order_by(NodeRun.node_index)
            )
        ).scalars().all()
        by_key = {n.node_key: n for n in new_nodes}
        assert "triage" not in by_key
        assert "dispatch" not in by_key
        assert by_key["screen"].status == "completed"
        assert by_key["screen"].output_json == '{"escalated_count":2,"fast_model_count":3}'
        assert by_key["cluster"].status == "completed"


@pytest.mark.asyncio
async def test_retry_from_triage_rejects_missing_screen(session_factory):
    """旧 run 仅有 triage、无 screen completed → 不得伪造，须拒绝续跑。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            task_type="discovery",
            vulnerability_description=None,
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        old_run = TaskRun(task_id=task.id, status="failed")
        session.add(old_run)
        await session.flush()
        # 模拟拆分前旧拓扑：有 triage，无 screen
        for i, (key, st) in enumerate(
            [
                ("source", "completed"),
                ("profile", "completed"),
                ("scan_gitleaks", "completed"),
                ("scan_osv", "completed"),
                ("scan_semgrep", "completed"),
                ("api_inventory", "completed"),
                ("api_hunt", "completed"),
                ("env_ready", "completed"),
                ("cluster", "completed"),
                ("triage", "failed"),
            ]
        ):
            session.add(
                NodeRun(
                    run_id=old_run.id,
                    task_id=task.id,
                    node_index=i,
                    node_key=key,
                    status=st,
                    output_json="{}",
                )
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with pytest.raises(ValueError, match="缺 screen"):
            await svc.retry_task(task.id, "u1", from_node="triage")


@pytest.mark.asyncio
async def test_retry_from_node_copies_prior_completed_nodes(session_factory):
    """从 reproduce 重试:拷贝上一 run 里 index<4 的已完成节点进新 run,不重跑 clone/画像。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch
    from sqlalchemy import select

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        old_run = TaskRun(task_id=task.id, status="failed")
        session.add(old_run)
        await session.flush()
        for i, (key, st) in enumerate(
            [
                ("source", "completed"),
                ("profile", "completed"),
                ("env_ready", "completed"),
                ("audit", "completed"),
                ("reproduce", "failed"),
            ]
        ):
            session.add(
                NodeRun(
                    run_id=old_run.id,
                    task_id=task.id,
                    node_index=i,
                    node_key=key,
                    status=st,
                    output_json=f'{{"k":"{key}"}}' if st == "completed" else "{}",
                )
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.send_task"):
            new_run_id = await svc.retry_task(task.id, "u1", from_node="reproduce")

        new_nodes = (
            await session.execute(
                select(NodeRun).where(NodeRun.run_id == new_run_id).order_by(NodeRun.node_index)
            )
        ).scalars().all()
        assert new_run_id != old_run.id
        # 只拷贝 index<4 的 4 个已完成节点；reproduce/report 不建，交编排器重跑
        assert [n.node_key for n in new_nodes] == ["source", "profile", "env_ready", "audit"]
        assert all(n.status == "completed" for n in new_nodes)
        assert {n.output_json for n in new_nodes} == {
            '{"k":"source"}', '{"k":"profile"}', '{"k":"env_ready"}', '{"k":"audit"}'
        }


@pytest.mark.asyncio
async def test_retry_from_node_copies_skipped_prereq(session_factory):
    """前置节点是 skipped(如 gate_fail 后的 reproduce)也照拷,保持分支语义。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch
    from sqlalchemy import select

    async with session_factory() as session:
        task = Task(project_address="x", vulnerability_description="d", owner_id="u1", status="completed")
        session.add(task)
        await session.flush()
        old_run = TaskRun(task_id=task.id, status="completed")
        session.add(old_run)
        await session.flush()
        for i, (key, st) in enumerate(
            [
                ("source", "completed"),
                ("profile", "completed"),
                ("env_ready", "completed"),
                ("audit", "completed"),
                ("reproduce", "skipped"),
                ("report", "completed"),
            ]
        ):
            session.add(
                NodeRun(run_id=old_run.id, task_id=task.id, node_index=i, node_key=key, status=st, output_json="{}")
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.send_task"):
            new_run_id = await svc.retry_task(task.id, "u1", from_node="report")

        new_nodes = (
            await session.execute(
                select(NodeRun).where(NodeRun.run_id == new_run_id).order_by(NodeRun.node_index)
            )
        ).scalars().all()
        by_key = {n.node_key: n.status for n in new_nodes}
        assert by_key == {
            "source": "completed", "profile": "completed", "env_ready": "completed",
            "audit": "completed", "reproduce": "skipped",
        }


@pytest.mark.asyncio
async def test_retry_from_node_rejects_incomplete_prereq(session_factory):
    """前置节点未完成(env_ready failed)不能从 reproduce 重试。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(project_address="x", vulnerability_description="d", owner_id="u1", status="failed")
        session.add(task)
        await session.flush()
        old_run = TaskRun(task_id=task.id, status="failed")
        session.add(old_run)
        await session.flush()
        for i, (key, st) in enumerate(
            [("source", "completed"), ("profile", "completed"), ("env_ready", "failed")]
        ):
            session.add(
                NodeRun(run_id=old_run.id, task_id=task.id, node_index=i, node_key=key, status=st, output_json="{}")
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with pytest.raises(ValueError, match="前置节点未完成"):
            await svc.retry_task(task.id, "u1", from_node="reproduce")


@pytest.mark.asyncio
async def test_retry_from_node_rejects_unknown_node(session_factory):
    """未知/不可重试的起点节点直接拒绝。"""
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(project_address="x", vulnerability_description="d", owner_id="u1", status="failed")
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="failed"))
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with pytest.raises(ValueError, match="不支持的重试起点"):
            await svc.retry_task(task.id, "u1", from_node="source")
        with pytest.raises(ValueError, match="不支持的重试起点"):
            await svc.retry_task(task.id, "u1", from_node="banana")


@pytest.mark.asyncio
async def test_retry_marks_task_queued_until_worker_starts(session_factory):
    """重试后任务应排队，不能提前标 running，否则列表误显「分析中」。"""
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch
    from sqlalchemy import select

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="failed"))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.send_task"):
            new_run_id = await svc.retry_task(task.id, "u1")

        await session.refresh(task)
        new_run = (
            await session.execute(select(TaskRun).where(TaskRun.id == new_run_id))
        ).scalar_one()
        assert task.status == "queued"
        assert new_run.status == "pending"


@pytest.mark.asyncio
async def test_retry_rejects_running_task(session_factory):
    """running 中的任务不能 retry(应先 cancel 或等完成)。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with pytest.raises(ValueError, match="不能重试"):
            await svc.retry_task(task.id, "u1")


@pytest.mark.asyncio
async def test_retry_cas_rejects_second_call_after_queued(session_factory):
    """第一次 retry 已把任务标 queued 后，第二次不能再插一个 TaskRun。"""
    from unittest.mock import patch

    from sqlalchemy import select

    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="failed"))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.send_task"):
            first = await svc.retry_task(task.id, "u1")
            with pytest.raises(ValueError, match="不能重试"):
                await svc.retry_task(task.id, "u1")

        runs = (await session.execute(select(TaskRun).where(TaskRun.task_id == task.id))).scalars().all()
        assert len(runs) == 2
        assert first in {r.id for r in runs}
        await session.refresh(task)
        assert task.status == "queued"


@pytest.mark.asyncio
async def test_task_operations_require_owner(session_factory):
    """已知 UUID 也不能读取或重试其他用户的任务。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="secret",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))

        assert await svc.get_task(task.id, "u2") is None
        from app.shared.exceptions import NotFoundError

        with pytest.raises(NotFoundError, match="任务不存在"):
            await svc.retry_task(task.id, "u2")


@pytest.mark.asyncio
async def test_retry_rejects_without_default_llm_provider(session_factory):
    from unittest.mock import patch

    from sqlalchemy import select

    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory(seed_llm=False) as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="failed"))
        await session.flush()

        with patch("app.core.celery_app.celery_app.send_task") as send:
            with pytest.raises(ValueError, match="LLM Provider"):
                await TaskService(TaskRepository(session)).retry_task(task.id, "u1")

        await session.refresh(task)
        assert task.status == "failed"
        runs = (await session.execute(select(TaskRun).where(TaskRun.task_id == task.id))).scalars().all()
        assert len(runs) == 1
        send.assert_not_called()


@pytest.mark.asyncio
async def test_retry_rejects_without_runner_image(session_factory, monkeypatch):
    from unittest.mock import patch

    from sqlalchemy import select

    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    monkeypatch.setattr(
        "app.core.agent_runner.agent_runner_manager.image_exists",
        lambda *args, **kwargs: False,
    )

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="failed"))
        await session.flush()

        with patch("app.core.celery_app.celery_app.send_task") as send:
            with pytest.raises(ValueError, match="agent-runner 镜像"):
                await TaskService(TaskRepository(session)).retry_task(task.id, "u1")

        await session.refresh(task)
        assert task.status == "failed"
        runs = (await session.execute(select(TaskRun).where(TaskRun.task_id == task.id))).scalars().all()
        assert len(runs) == 1
        send.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_marks_running_and_pending_nodes_cancelled(session_factory):
    """取消任务后，正在跑/未开始的节点必须离开 running，进度条不能继续转圈。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock
    from sqlalchemy import select

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="running")
        session.add(run)
        await session.flush()
        for i, (key, st) in enumerate(
            [
                ("source", "completed"),
                ("profile", "completed"),
                ("env_ready", "running"),
                ("audit", "pending"),
            ]
        ):
            session.add(
                NodeRun(
                    run_id=run.id,
                    task_id=task.id,
                    node_index=i,
                    node_key=key,
                    status=st,
                    output_json="{}",
                )
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.control.revoke"), patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ):
            detail = await svc.cancel_task(task.id, "u1")
            await asyncio.sleep(0)

        assert detail is not None
        assert detail.status == "cancelled"
        nodes = (
            await session.execute(select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index))
        ).scalars().all()
        by_key = {n.node_key: n for n in nodes}
        assert by_key["source"].status == "completed"
        assert by_key["profile"].status == "completed"
        assert by_key["env_ready"].status == "cancelled"
        assert by_key["env_ready"].finished_at is not None
        assert by_key["audit"].status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_tears_down_lab_and_runner(session_factory):
    """取消不能只改库：必须拆靶场 compose 和该任务的 agent-runner。"""
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="running"))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.control.revoke"), patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ) as mock_teardown:
            await svc.cancel_task(task.id, "u1")
            await asyncio.sleep(0)

        mock_teardown.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_cancel_returns_before_teardown_finishes(session_factory):
    """取消 HTTP 不能等 compose down：库标 cancelled 后立刻返回，拆容器后台做。"""
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch

    hang = asyncio.Event()

    async def never_finishes(_task_id: str) -> None:
        await hang.wait()

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="running"))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.control.revoke"), patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            never_finishes,
        ):
            detail = await asyncio.wait_for(svc.cancel_task(task.id, "u1"), timeout=0.5)

        assert detail is not None
        assert detail.status == "cancelled"
        hang.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_delete_tears_down_lab_and_runner(session_factory):
    """删除已结束任务时也要拆残留靶场，避免历史容器堆积。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="completed",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ) as mock_teardown:
            ok = await svc.delete_task(task.id, "u1")
        assert ok is True
        mock_teardown.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_soft_delete_archives(session_factory):
    """软删把 status 改 archived,不删行。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="completed",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ) as mock_teardown:
            ok = await svc.delete_task(task.id, "u1")
        assert ok is True
        assert task.status == "archived"
        mock_teardown.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_delete_queued_revokes_celery(session_factory):
    """排队中的任务删除必须 revoke，否则 worker 稍后会把 archived 改回 running。"""
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="queued",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="pending")
        session.add(run)
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.control.revoke") as mock_revoke, patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ):
            ok = await svc.delete_task(task.id, "u1")
        assert ok is True
        assert task.status == "archived"
        mock_revoke.assert_called()
        assert mock_revoke.call_args.args[0] == run.id


@pytest.mark.asyncio
async def test_soft_delete_rejects_already_archived(session_factory):
    """已归档任务不能再软删（避免前端反复点删除都提示成功）。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="archived",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with pytest.raises(ValueError, match="已归档"):
            await svc.delete_task(task.id, "u1")


@pytest.mark.asyncio
async def test_retry_full_purges_discovery_findings(session_factory):
    """整轮 retry 清掉 findings/groups，避免 fingerprint unique 撞车。"""
    from unittest.mock import patch

    from sqlalchemy import select

    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            task_type="discovery",
            vulnerability_description=None,
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="failed")
        session.add(run)
        await session.flush()
        scan = ScanRun(
            task_id=task.id, run_id=run.id, node_run_id="nr", engine="semgrep",
            status="completed", config_summary={},
        )
        session.add(scan)
        await session.flush()
        finding = RawFinding(
            task_id=task.id, scan_run_id=scan.id, engine="semgrep", rule_id="r",
            severity="error", file_path="a.py", line_start=1, line_end=1,
            message="m", fingerprint="fp-retry", raw={},
        )
        session.add(finding)
        await session.flush()
        session.add(AlertGroup(
            task_id=task.id, group_key="gk", file_path="a.py",
            member_count=1, representative_finding_id=finding.id,
            engine_set=["semgrep"], status="clustered",
        ))
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.send_task"):
            await svc.retry_task(task.id, "u1")
        assert (await session.execute(select(RawFinding))).scalars().all() == []
        assert (await session.execute(select(AlertGroup))).scalars().all() == []
        assert (await session.execute(select(ScanRun))).scalars().all() == []
