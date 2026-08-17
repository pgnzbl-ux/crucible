"""节点列表 API 必须带回 output，前端才能观测每步结果。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.project.models import Project  # noqa: F401
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_run_nodes_returns_parsed_output(session_factory):
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="https://github.com/siteboon/claudecodeui.git",
            vulnerability_description="xss in search",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="running")
        session.add(run)
        await session.flush()
        session.add(
            NodeRun(
                run_id=run.id,
                task_id=task.id,
                node_index=0,
                node_key="source",
                status="completed",
                output_json=json.dumps(
                    {
                        "origin": "minio",
                        "repo_dirname": "claudecodeui",
                        "project_key": "siteboon/claudecodeui",
                        "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    },
                    ensure_ascii=False,
                ),
            )
        )
        session.add(
            NodeRun(
                run_id=run.id,
                task_id=task.id,
                node_index=1,
                node_key="profile",
                status="running",
                output_json="{}",
            )
        )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        nodes = await svc.get_run_nodes(task.id, run.id, "u1")

    assert len(nodes) == 2
    source = nodes[0]
    assert source["node_key"] == "source"
    assert source["status"] == "completed"
    assert source["output"]["origin"] == "minio"
    assert source["output"]["repo_dirname"] == "claudecodeui"
    assert source["output"]["project_key"] == "siteboon/claudecodeui"
    assert nodes[1]["output"] == {}


@pytest.mark.asyncio
async def test_get_run_nodes_invalid_output_json_becomes_empty_dict(session_factory):
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d" * 10,
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="failed")
        session.add(run)
        await session.flush()
        session.add(
            NodeRun(
                run_id=run.id,
                task_id=task.id,
                node_index=0,
                node_key="source",
                status="failed",
                error_message="源码克隆失败: 网络错误",
                output_json="not-json",
            )
        )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        nodes = await svc.get_run_nodes(task.id, run.id, "u1")

    assert nodes[0]["output"] == {}
    assert nodes[0]["error_message"] == "源码克隆失败: 网络错误"
