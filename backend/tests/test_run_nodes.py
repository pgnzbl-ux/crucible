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


@pytest.mark.asyncio
async def test_failed_run_real_lead_verify_wins_over_stale_leads(session_factory):
    """真实 NodeRun 已终态：残留 queued/running 线索不得把节点显示成执行中。"""
    from app.contexts.finding.models import LeadRun
    from app.contexts.task.models import NodeRun, Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x", task_type="discovery", vulnerability_description=None,
            owner_id="u1", status="failed",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="failed")
        session.add(run)
        await session.flush()
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=11, node_key="dispatch",
            status="completed", output_json='{"has_lead":true,"queued_count":3}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=14, node_key="lead_verify",
            status="failed", error_message="任务超过最大执行时长",
        ))
        for pos in range(3):
            session.add(LeadRun(
                task_id=task.id, run_id=run.id, alert_group_id=f"g{pos}",
                queue_position=pos, lead_description=f"lead-{pos}",
                status="running" if pos == 0 else "queued",
            ))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        nodes = await svc.get_run_nodes(task.id, run.id, "u1")
        lead_node = next(node for node in nodes if node["node_key"] == "lead_verify")
        assert lead_node["status"] == "failed"
        assert lead_node["error_message"] == "任务超过最大执行时长"
        # 聚合信息仍保留（详情面板可见 3 条残留）
        assert lead_node["output"]["lead_count"] == 3


@pytest.mark.asyncio
async def test_legacy_failed_run_synthesized_lead_not_running(session_factory):
    """旧响应缺真实 lead_verify 行：run 已失败时合成行也不得是 running。"""
    from app.contexts.finding.models import LeadRun
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x", task_type="discovery", vulnerability_description=None,
            owner_id="u1", status="failed",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="failed")
        session.add(run)
        await session.flush()
        session.add(LeadRun(
            task_id=task.id, run_id=run.id, alert_group_id="g1",
            queue_position=0, lead_description="lead-0", status="queued",
        ))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        nodes = await svc.get_run_nodes(task.id, run.id, "u1")
        assert [n for n in nodes if n["node_key"] == "lead_verify"] == [] or (
            next(n for n in nodes if n["node_key"] == "lead_verify")["status"] == "failed"
        )


@pytest.mark.asyncio
async def test_terminalize_task_leads_closes_orphans(session_factory):
    """失败收尾：queued/running 线索转 skipped+复核，孤儿阶段行闭合。"""
    from sqlalchemy import select

    from app.contexts.agent.lead_worker import terminalize_task_leads
    from app.contexts.finding.models import AlertGroup, LeadNodeRun, LeadRun

    from app.contexts.task.models import Task  # noqa: F811

    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import RawFinding
    from app.contexts.task.models import TaskRun  # 本测试局部种子

    async with session_factory() as session:
        task = Task(project_address="x", vulnerability_description=None,
                    owner_id="u1", status="failed")
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="failed")
        session.add(run)
        await session.flush()
        sr = ScanRun(
            task_id=task.id, run_id=run.id, node_run_id="nr-x",
            engine="semgrep", status="completed", config_summary={},
        )
        session.add(sr)
        await session.flush()
        for pos in range(2):
            finding = RawFinding(
                task_id=task.id, scan_run_id=sr.id, engine="semgrep",
                rule_id=f"r.{pos}", cwe="CWE-89", severity="error",
                file_path="a.py", line_start=1, line_end=1,
                message="m", fingerprint=f"fp-{pos}", raw={},
            )
            session.add(finding)
            await session.flush()
            group = AlertGroup(
                task_id=task.id, group_key=f"gk{pos}", cwe="CWE-89",
                file_path="a.py", member_count=1,
                representative_finding_id=finding.id, engine_set=["semgrep"],
                status="adjudicated" if pos else "dispatched",
            )
            session.add(group)
            await session.flush()
            status = "queued" if pos == 0 else "running"
            lead = LeadRun(
                task_id=task.id, run_id=run.id, alert_group_id=group.id,
                queue_position=pos, lead_description=f"lead-{pos}",
                status=status,
            )
            session.add(lead)
            await session.flush()
            if pos == 1:
                session.add(LeadNodeRun(
                    lead_run_id=lead.id, task_id=task.id, run_id="r1",
                    node_key="audit", status="running", attempt=1, input_json={},
                ))
        await session.flush()

        n = await terminalize_task_leads(session, task_id=task.id, reason="超时收尾")
        await session.commit()

        assert n == 2
        rows = (await session.execute(select(LeadRun))).scalars().all()
        assert all(lead.status == "skipped" and "超时" in (lead.error or "") for lead in rows)
        phases = (await session.execute(select(LeadNodeRun))).scalars().all()
        open_left = [p for p in phases if p.status == "running"]
        assert not open_left
