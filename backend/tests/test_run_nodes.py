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
        from app.shared.models import register_models

        register_models()
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


@pytest.mark.asyncio
async def test_get_run_nodes_serializes_started_at_as_utc(session_factory):
    """SQLite 读回 naive datetime；不补 +00:00 前端会按本地时间解析。"""
    from datetime import datetime

    from app.contexts.task.models import NodeRun, Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d" * 10,
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
                status="running",
                started_at=datetime(2026, 8, 18, 9, 18, 33),
            )
        )
        await session.flush()

        nodes = await TaskService(TaskRepository(session)).get_run_nodes(
            task.id, run.id, "u1"
        )

    assert nodes[0]["started_at"] == "2026-08-18T09:18:33+00:00"
    assert nodes[0]["finished_at"] is None


@pytest.mark.asyncio
async def test_discovery_run_nodes_include_real_lead_phase_progress(session_factory):
    """discovery 终认状态来自 LeadRun/LeadNodeRun，不得靠 report 是否启动反推。"""
    from app.contexts.finding.models import LeadNodeRun, LeadRun
    from app.contexts.task.models import NodeRun, Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x", task_type="discovery", vulnerability_description=None,
            owner_id="u1", status="running",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="running")
        session.add(run)
        await session.flush()
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=11, node_key="dispatch",
            status="completed", output_json='{"has_lead":true,"queued_count":1}',
        ))
        lead = LeadRun(
            task_id=task.id, run_id=run.id, alert_group_id="group-1",
            queue_position=0, lead_description="lead", status="running",
        )
        session.add(lead)
        await session.flush()
        audit = LeadNodeRun(
            lead_run_id=lead.id, task_id=task.id, run_id=run.id,
            node_key="audit", status="completed", attempt=1,
            input_json={}, output_json={"gate_verdict": "pass"},
        )
        reproduce = LeadNodeRun(
            lead_run_id=lead.id, task_id=task.id, run_id=run.id,
            node_key="reproduce", status="running", attempt=1, input_json={},
        )
        session.add_all([audit, reproduce])
        await session.flush()

        svc = TaskService(TaskRepository(session))
        nodes = await svc.get_run_nodes(task.id, run.id, "u1")
        lead_node = next(node for node in nodes if node["node_key"] == "lead_verify")
        assert lead_node["status"] == "running"
        assert lead_node["output"]["lead_status_counts"] == {"running": 1}
        assert lead_node["output"]["phase_status_counts"] == {
            "audit": {"completed": 1}, "reproduce": {"running": 1},
        }

        lead.status = "completed"
        reproduce.status = "completed"
        reproduce.output_json = {"verdict": "confirmed"}
        await session.flush()
        nodes = await svc.get_run_nodes(task.id, run.id, "u1")
        lead_node = next(node for node in nodes if node["node_key"] == "lead_verify")
        assert lead_node["status"] == "completed"
