"""api_hunt 直出合格门与 dispatch conf 阈值对齐。"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

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


def _hunt_raw(*, confidence_score: float) -> dict:
    return {
        "confidence_score": confidence_score,
        "confidence": "HIGH" if confidence_score >= 0.8 else "MEDIUM",
        "endpoint_id": "GET /items/{id}",
        "why": ["缺少对象归属校验"],
        "evidence": [{"file": "app.py", "lines": "10-20"}],
        "qualify": {
            "attacker_controlled": True,
            "reaches_sink": True,
            "sanitizer": "none",
        },
    }


async def _seed_hunt_group(session, *, confidence_score: float):
    from app.contexts.discovery.service import DiscoveryService
    from app.contexts.finding.models import AlertGroup, RawFinding
    from app.contexts.finding.sarif import fingerprint
    from app.contexts.task.models import Task, TaskRun

    task = Task(
        project_address="x",
        task_type="discovery",
        vulnerability_description=None,
        owner_id="u1",
        status="running",
    )
    session.add(task)
    await session.flush()
    run = TaskRun(task_id=task.id, status="running")
    session.add(run)
    await session.flush()
    disc = DiscoveryService(session)
    scan_run = await disc.start_scan_run(
        task_id=task.id,
        run_id=run.id,
        node_run_id="nr-hunt",
        engine="api_hunt",
        config_summary={},
    )
    await disc.finish_scan_run(scan_run, status="completed", finding_count=1)
    fp = fingerprint("api_hunt", "missing|ep", "app.py", 10, "CWE-639")
    finding = RawFinding(
        task_id=task.id,
        scan_run_id=scan_run.id,
        engine="api_hunt",
        rule_id="missing_ownership_check",
        cwe="CWE-639",
        severity="warning",
        file_path="app.py",
        line_start=10,
        line_end=10,
        message="hunt",
        fingerprint=fp,
        raw=_hunt_raw(confidence_score=confidence_score),
    )
    session.add(finding)
    await session.flush()
    group = AlertGroup(
        task_id=task.id,
        group_key=f"hunt-{confidence_score}",
        cwe="CWE-639",
        file_path="app.py",
        function_symbol="get_item",
        member_count=1,
        representative_finding_id=finding.id,
        engine_set=["api_hunt"],
        status="clustered",
        clue_grade="B",
        priority="medium",
    )
    session.add(group)
    await session.commit()
    return task, group


@pytest.mark.asyncio
async def test_adjudicate_hunt_requires_high_confidence(session_factory):
    from app.contexts.agent.nodes.api_hunt import _adjudicate_hunt_groups
    from app.contexts.finding.service import FindingService

    async with session_factory() as session:
        task, group = await _seed_hunt_group(session, confidence_score=0.75)
        svc = FindingService(session)
        n = await _adjudicate_hunt_groups(svc, task_id=task.id, high_confidence=0.8)
        assert n == 0
        await session.refresh(group)
        assert group.status == "clustered"
        assert group.ai_verdict is None


@pytest.mark.asyncio
async def test_adjudicate_hunt_qualifies_high_confidence(session_factory):
    from app.contexts.agent.nodes.api_hunt import _adjudicate_hunt_groups
    from app.contexts.finding.service import FindingService

    async with session_factory() as session:
        task, group = await _seed_hunt_group(session, confidence_score=0.9)
        svc = FindingService(session)
        n = await _adjudicate_hunt_groups(svc, task_id=task.id, high_confidence=0.8)
        assert n == 1
        await session.refresh(group)
        assert group.status == "adjudicated"
        assert group.ai_verdict == "tp"
        assert group.verdict_source == "agent"
        assert float(group.ai_confidence) >= 0.8


@pytest.mark.asyncio
async def test_hunt_batch_records_usage(session_factory, tmp_path):
    """api_hunt 每批 Docker 会话必须入台账，否则任务 token 总计漏猎洞。"""
    from sqlalchemy import select

    from app.contexts.agent.nodes.api_hunt import ApiHuntNode
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.task.models import AgentUsage, Task

    async with session_factory() as session:
        task = Task(
            project_address="x",
            task_type="discovery",
            vulnerability_description=None,
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        ctx = NodeContext(
            task_id=task.id,
            run_id="r1",
            host_workdir=str(tmp_path),
            source_path=str(tmp_path),
            vulnerability_description="",
            project_address="x",
            project_ref=None,
            db_session=session,
        )
        batch = [{
            "endpoint_id": "GET /items/{id}",
            "method": "GET",
            "path_template": "/items/{id}",
            "handler_file": "app.py",
            "handler_symbol": "get_item",
            "line_start": 10,
            "id_params": ["id"],
            "auth_observed": [],
            "resource_key": "item",
            "has_object_id": True,
        }]

        async def fake_run(**kwargs):
            meta = kwargs.get("meta_out")
            assert meta is not None
            meta.update({"usage": {"prompt_tokens": 40, "completion_tokens": 8}})
            return {"suspects": [], "reviewed_count": 1}

        with patch("app.contexts.agent.ai_runner.run_ai_node", new=fake_run):
            out = await ApiHuntNode()._hunt_batch(ctx, batch, object())

        assert out["reviewed_count"] == 1
        rows = (await session.execute(select(AgentUsage))).scalars().all()
        assert len(rows) == 1
        assert rows[0].node_key == "api_hunt"
        assert rows[0].prompt_tokens == 40
        assert rows[0].completion_tokens == 8
        assert rows[0].run_id == "r1"
