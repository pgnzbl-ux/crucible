"""硬删除级联：PG 外键无数据库级联，delete_hard 必须手动清空全部子表。

回归背景：旧实现漏删 reports/evidences/scan_runs/raw_findings/alert_groups/
adjudications/review_actions/lead_runs，PG 上 DELETE ?hard=true 必撞 FK 500。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed_full_chain(factory) -> None:
    """铺出任务的全套子表数据：run/node/event/failure + 扫描/发现/告警组/
    判决/复核/线索 + 报告/证据。"""
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import (
        Adjudication,
        AlertGroup,
        LeadRun,
        RawFinding,
        ReviewAction,
    )
    from app.contexts.identity.models import User
    from app.contexts.project.models import Project
    from app.contexts.report.models import Evidence, Report
    from app.contexts.task.models import (
        AgentEvent,
        AgentUsage,
        NodeRun,
        NodeRunFailure,
        Task,
        TaskRun,
    )

    async with factory() as s:
        s.add(User(id="u1", email="u1@x.test", password_hash="x", display_name="U1"))
        s.add(Project(id="p1", name="demo", git_url="https://github.com/a/b", owner_id="u1"))
        s.add(Task(
            id="t1", project_address="https://github.com/a/b",
            vulnerability_description="d", owner_id="u1", project_id="p1",
            status="completed",
        ))
        await s.flush()
        s.add(TaskRun(id="r1", task_id="t1", status="completed"))
        await s.flush()
        node = NodeRun(run_id="r1", task_id="t1", node_index=0, node_key="source", status="completed")
        s.add(node)
        await s.flush()
        s.add(AgentEvent(run_id="r1", task_id="t1", sequence=1, event_type="phase.updated"))
        s.add(NodeRunFailure(
            owner_id="u1", task_id="t1", run_id="r1", node_run_id=node.id,
            node_key="source", error_class="x", bundle_key="bundle/x",
        ))
        scan = ScanRun(
            task_id="t1", run_id="r1", node_run_id=node.id,
            engine="semgrep", status="completed", config_summary={},
        )
        s.add(scan)
        await s.flush()
        finding = RawFinding(
            task_id="t1", scan_run_id=scan.id, engine="semgrep", rule_id="r1",
            severity="error", file_path="a.py", line_start=1, line_end=1,
            message="m", fingerprint="fp1", raw={},
        )
        s.add(finding)
        await s.flush()
        group = AlertGroup(
            task_id="t1", group_key="gk1", file_path="a.py", line_span="1-1",
            member_count=1, representative_finding_id=finding.id,
            engine_set=["semgrep"], status="adjudicated",
        )
        s.add(group)
        await s.flush()
        s.add(Adjudication(
            alert_group_id=group.id, attempt=1, verdict="tp",
            why=[], evidence=[], need=[], context_log=[],
            prompt_text="p", response_text="r",
        ))
        s.add(ReviewAction(alert_group_id=group.id, user_id="u1", action="confirm"))
        s.add(LeadRun(
            task_id="t1", run_id="r1", alert_group_id=group.id,
            lead_description="lead", status="completed",
        ))
        s.add(AgentUsage(
            task_id="t1", run_id="r1", node_key="source", source="agent",
            prompt_tokens=1, completion_tokens=1,
        ))
        report = Report(task_id="t1", run_id="r1", owner_id="u1", status="generated")
        s.add(report)
        await s.flush()
        s.add(Evidence(
            report_id=report.id, task_id="t1", object_key="k", file_name="f",
        ))
        await s.commit()


@pytest.mark.asyncio
async def test_delete_hard_clears_every_child_table(factory):
    await _seed_full_chain(factory)

    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import (
        Adjudication,
        AlertGroup,
        LeadRun,
        RawFinding,
        ReviewAction,
    )
    from app.contexts.report.models import Evidence, Report
    from app.contexts.task.models import AgentEvent, AgentUsage, NodeRun, NodeRunFailure, Task, TaskRun
    from app.contexts.task.repository import TaskRepository

    async with factory() as s:
        repo = TaskRepository(s)
        task = await repo.get_by_id("t1")
        assert task is not None
        await repo.delete_hard(task)
        await s.commit()

    survivors = (
        Evidence, Report, Adjudication, ReviewAction, LeadRun, AlertGroup,
        RawFinding, ScanRun, NodeRunFailure, AgentEvent, NodeRun, TaskRun, Task, AgentUsage,
    )
    async with factory() as s:
        for model in survivors:
            rows = (await s.execute(select(model.id))).scalars().all()
            assert rows == [], f"{model.__tablename__} 残留 {len(rows)} 行"


@pytest.mark.asyncio
async def test_delete_task_hard_clears_queue_and_objects(factory):
    await _seed_full_chain(factory)

    from unittest.mock import patch

    from app.contexts.agent import lead_queue
    from app.contexts.discovery.models import ScanRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from app.shared.object_store import MemoryObjectStore, set_object_store_for_tests

    class _QueueRedis:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def delete(self, *keys):
            self.deleted.extend(keys)

    fake = _QueueRedis()
    lead_queue.set_redis_client(fake)
    store = MemoryObjectStore()
    store.put_at(
        "evidence", "evidence/u1/t1/e1/a.bin", b"x", content_type="application/octet-stream",
    )
    store.put_at(
        "node_run", "node_run/scan-sarif/orphan.json", b"{}", content_type="application/json",
    )
    set_object_store_for_tests(store)
    try:
        async with factory() as s:
            scan = (await s.execute(select(ScanRun))).scalar_one()
            scan.sarif_key = "node_run/scan-sarif/orphan.json"
            await s.flush()
            with patch(
                "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
                return_value=None,
            ):
                ok = await TaskService(TaskRepository(s)).delete_task("t1", "u1", hard=True)
            await s.commit()
        assert ok is True
        assert fake.deleted
        leftover = [key for (_bucket, key) in store._data]
        assert leftover == []
    finally:
        lead_queue.set_redis_client(None)
        set_object_store_for_tests(None)

