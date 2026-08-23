"""并发撞码恢复：重投双跑/并发提交时，upsert 路径靠 savepoint + 复查兜底。

用「隐藏首次存在性查询」的代理模拟并发窗口——本事务看不见另一执行
已插入的行，INSERT 撞唯一约束后必须恢复而非抛错。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []

    def scalar_one_or_none(self):
        return None


class _HideSelectOnce:
    """把首次命中 marker 的 SELECT 变成空结果，模拟并发插入不可见窗口。"""

    def __init__(self, session: AsyncSession, marker: str, fail_first_flush: bool = False):
        self._session = session
        self._marker = marker
        self._hidden = False
        self._fail_first_flush = fail_first_flush
        self._flushed = False

    async def execute(self, stmt):
        if not self._hidden and self._marker in str(stmt):
            self._hidden = True
            return _EmptyResult()
        return await self._session.execute(stmt)

    async def flush(self):
        # 注入首次 flush 失败：模拟批量插入撞唯一约束（sqlite 的 savepoint
        # 失败语义与 PG 不同，直接注入以验证恢复逻辑本身）
        if self._fail_first_flush and not self._flushed:
            self._flushed = True
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("INSERT", {}, Exception("simulated unique conflict"))
        return await self._session.flush()

    def __getattr__(self, name):
        return getattr(self._session, name)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_groups_recovers_from_unique_conflict(factory):
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.service import FindingService
    from app.contexts.identity.models import User
    from app.contexts.project.models import Project
    from app.contexts.task.models import Task

    async with factory() as s:
        s.add(User(id="u1", email="u1@x.test", password_hash="x", display_name="U1"))
        s.add(Project(id="p1", name="demo", git_url="https://a/b", owner_id="u1"))
        s.add(Task(
            id="t1", project_address="https://a/b", vulnerability_description="d",
            owner_id="u1", project_id="p1", status="running",
        ))
        await s.flush()
        s.add(AlertGroup(
            task_id="t1", group_key="gk1", file_path="a.py", line_span="1-1",
            member_count=3, representative_finding_id="whatever",
            engine_set=["semgrep"], status="clustered",
        ))
        await s.commit()

    async with factory() as s:
        proxy = _HideSelectOnce(s, "alert_groups")
        created = await FindingService(proxy).upsert_groups(
            task_id="t1",
            finding_by_id={},
            groups=[{
                "group_key": "gk1", "cwe": "CWE-89", "file_path": "a.py",
                "member_count": 5, "representative_finding_id": "whatever",
                "engine_set": ["semgrep", "osv"],
            }],
        )
        await s.commit()

    assert created == 0
    async with factory() as s:
        rows = (await s.execute(select(AlertGroup))).scalars().all()
        assert len(rows) == 1
        assert rows[0].member_count == 5
        assert rows[0].engine_set == ["osv", "semgrep"]


@pytest.mark.asyncio
async def test_upsert_source_artifact_recovers_from_unique_conflict(factory):
    from app.contexts.identity.models import User
    from app.contexts.project.models import SourceArtifact
    from app.contexts.project.repository import ProjectRepository

    async with factory() as s:
        s.add(User(id="u1", email="u1@x.test", password_hash="x", display_name="U1"))
        s.add(SourceArtifact(
            owner_id="u1", git_host="github.com", project_key="a/b",
            git_url="https://github.com/a/b", repo_dirname="b",
            ref_type="branch", ref_name="main", commit_sha="old",
            bucket="crucible-durable", object_key="src/old.tar",
            object_url="http://minio/src/old.tar",
        ))
        await s.commit()

    async with factory() as s:
        proxy = _HideSelectOnce(s, "source_artifacts")
        row = await ProjectRepository(proxy).upsert_source_artifact({
            "owner_id": "u1", "git_host": "github.com", "project_key": "a/b",
            "git_url": "https://github.com/a/b", "repo_dirname": "b",
            "ref_type": "branch", "ref_name": "main", "commit_sha": "new",
            "bucket": "crucible-durable", "object_key": "src/new.tar",
            "object_url": "http://minio/src/new.tar",
        })
        await s.commit()
        assert row.commit_sha == "new"

    async with factory() as s:
        rows = (await s.execute(select(SourceArtifact))).scalars().all()
        assert len(rows) == 1
        assert rows[0].commit_sha == "new"


@pytest.mark.asyncio
async def test_append_events_retries_on_sequence_conflict(factory):
    from app.contexts.task.models import AgentEvent, Task, TaskRun

    async with factory() as s:
        s.add(Task(
            id="t1", project_address="https://a/b", vulnerability_description="d",
            owner_id="u1", project_id=None, status="running",
        ))
        await s.flush()
        s.add(TaskRun(id="r1", task_id="t1", status="running"))
        await s.flush()
        s.add(AgentEvent(run_id="r1", task_id="t1", sequence=1, event_type="phase.updated"))
        await s.commit()

    from app.contexts.agent.tasks import _append_events

    async with factory() as s:
        run = await s.get(TaskRun, "r1")
        proxy = _HideSelectOnce(s, "agent_events")
        await _append_events(proxy, run, [
            {"type": "phase.updated", "message": "a"},
            {"type": "phase.updated", "message": "b"},
        ])
        await s.commit()

    async with factory() as s:
        seqs = (await s.execute(
            select(AgentEvent.sequence).where(AgentEvent.run_id == "r1")
        )).scalars().all()
        assert sorted(seqs) == [1, 2, 3]


@pytest.mark.asyncio
async def test_upsert_raw_findings_conflict_keeps_outer_transaction(factory):
    """撞 uq_raw_findings_task_fp 时只回滚本批插入，不丢外层未提交状态。"""
    from app.contexts.discovery.models import ScanRun
    from app.contexts.discovery.service import DiscoveryService
    from app.contexts.finding.models import RawFinding
    from app.contexts.task.models import AgentEvent, Task, TaskRun

    async with factory() as s:
        s.add(Task(
            id="t1", project_address="https://a/b", vulnerability_description="d",
            owner_id="u1", project_id=None, status="running",
        ))
        await s.flush()
        s.add(TaskRun(id="r1", task_id="t1", status="running"))
        await s.flush()
        s.add(ScanRun(
            id="sc1", task_id="t1", run_id="r1", node_run_id="n1",
            engine="semgrep", status="running", config_summary={},
        ))
        s.add(RawFinding(
            task_id="t1", scan_run_id="sc1", engine="semgrep", rule_id="r",
            severity="error", file_path="a.py", line_start=1, line_end=1,
            message="m", fingerprint="dup", raw={},
        ))
        await s.commit()

    async with factory() as s:
        # 外层未提交状态：一条事件行
        s.add(AgentEvent(run_id="r1", task_id="t1", sequence=1, event_type="phase.updated"))
        await s.flush()
        proxy = _HideSelectOnce(s, "raw_findings", fail_first_flush=True)
        svc = DiscoveryService(proxy)
        inserted = await svc.upsert_raw_findings(
            task_id="t1", scan_run_id="sc1",
            findings=[
                {"engine": "semgrep", "rule_id": "r", "file_path": "a.py",
                 "fingerprint": "dup", "message": "m"},
                {"engine": "semgrep", "rule_id": "r2", "file_path": "b.py",
                 "fingerprint": "fresh", "message": "m2"},
            ],
        )
        await s.commit()
        assert inserted == 1  # 撞码的跳过，新指纹落库

    async with factory() as s:
        from app.contexts.finding.models import RawFinding

        fps = (await s.execute(
            select(RawFinding.fingerprint).where(RawFinding.task_id == "t1")
        )).scalars().all()
        assert sorted(fps) == ["dup", "fresh"]
        events = (await s.execute(
            select(AgentEvent.sequence).where(AgentEvent.run_id == "r1")
        )).scalars().all()
        assert events == [1]  # 外层未提交的事件行没有被回滚丢掉
