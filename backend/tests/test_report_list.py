"""报告列表必须带判定，才能当结果管理用。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


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


@pytest.mark.asyncio
async def test_list_reports_includes_verdict(session):
    from app.contexts.report.models import Report
    from app.contexts.report.repository import ReportRepository
    from app.contexts.report.service import ReportService

    session.add(
        Report(
            task_id="t1",
            run_id="r1",
            owner_id="u1",
            status="generated",
            title="SQL 注入验证",
            verdict="confirmed",
            severity="High",
            report_data=json.dumps({"product_intro": "demo"}, ensure_ascii=False),
        )
    )
    await session.flush()

    svc = ReportService(ReportRepository(session))
    items, total = await svc.list_reports("u1")
    assert total == 1
    row = items[0]
    from app.contexts.report.schemas import ReportSummary

    assert isinstance(row, ReportSummary)
    assert row.verdict == "confirmed"
    assert row.severity == "High"
    assert row.title == "SQL 注入验证"
    assert row.task_id == "t1"


@pytest.mark.asyncio
async def test_attach_evidence_rejects_unknown_kind(session):
    """非法证据 kind 必须拒绝，不得静默改成 artifact。"""
    from app.contexts.report.models import Report
    from app.contexts.report.repository import ReportRepository
    from app.contexts.report.service import ReportService
    from app.shared.object_store import MemoryObjectStore, set_object_store_for_tests

    report = Report(
        task_id="t-ev",
        run_id="r-ev",
        owner_id="u1",
        status="generated",
        title="证据",
    )
    session.add(report)
    await session.flush()

    store = MemoryObjectStore()
    set_object_store_for_tests(store)
    try:
        evidence, err = await ReportService(ReportRepository(session)).attach_evidence(
            report_id=report.id,
            owner_id="u1",
            file_name="x.bin",
            content_type="application/octet-stream",
            data=b"abc",
            kind="malware",
        )
    finally:
        set_object_store_for_tests(None)

    assert evidence is None
    assert err is not None
    assert "非法" in err
    assert store._data == {}


@pytest.mark.asyncio
async def test_attach_evidence_writes_task_bucket_and_owner_key(session):
    from app.contexts.report.models import Report
    from app.contexts.report.repository import ReportRepository
    from app.contexts.report.service import ReportService
    from app.shared.object_store import MemoryObjectStore, set_object_store_for_tests

    report = Report(
        task_id="t-ev2",
        run_id="r-ev2",
        owner_id="u1",
        status="generated",
        title="证据",
    )
    session.add(report)
    await session.flush()

    store = MemoryObjectStore()
    set_object_store_for_tests(store)
    try:
        evidence, err = await ReportService(ReportRepository(session)).attach_evidence(
            report_id=report.id,
            owner_id="u1",
            file_name="shot.png",
            content_type="image/png",
            data=b"png",
            kind="screenshot",
        )
    finally:
        set_object_store_for_tests(None)

    assert err is None
    assert evidence is not None
    assert evidence.bucket == "crucible-task"
    assert evidence.object_key.startswith("evidence/u1/t-ev2/")
    assert evidence.object_key.endswith("/shot.png")
    assert evidence.download_url is not None


@pytest.mark.asyncio
async def test_attach_evidence_reuses_loaded_report(session):
    from app.contexts.report.models import Report
    from app.contexts.report.repository import ReportRepository
    from app.contexts.report.service import ReportService
    from app.shared.object_store import MemoryObjectStore, set_object_store_for_tests

    report = Report(
        task_id="t-ev3",
        run_id="r-ev3",
        owner_id="u1",
        status="generated",
        title="证据",
    )
    session.add(report)
    await session.flush()

    repo = ReportRepository(session)
    calls = {"n": 0}
    original = repo.get_by_id

    async def counted(*args, **kwargs):
        calls["n"] += 1
        return await original(*args, **kwargs)

    repo.get_by_id = counted  # type: ignore[method-assign]
    loaded = await original(report.id, "u1")
    store = MemoryObjectStore()
    set_object_store_for_tests(store)
    try:
        first, err1 = await ReportService(repo).attach_evidence(
            report_id=report.id,
            owner_id="u1",
            file_name="a.log",
            content_type="text/plain",
            data=b"a",
            kind="log",
            report=loaded,
        )
        second, err2 = await ReportService(repo).attach_evidence(
            report_id=report.id,
            owner_id="u1",
            file_name="b.log",
            content_type="text/plain",
            data=b"b",
            kind="log",
            report=loaded,
        )
    finally:
        set_object_store_for_tests(None)

    assert err1 is None and err2 is None
    assert first is not None and second is not None
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_list_reports_filters_by_verdict_and_query(session):
    from app.contexts.report.models import Report
    from app.contexts.report.repository import ReportRepository
    from app.contexts.report.service import ReportService

    session.add_all(
        [
            Report(
                task_id="task-sql",
                run_id="run-sql",
                owner_id="u1",
                status="generated",
                title="SQL injection report",
                verdict="confirmed",
            ),
            Report(
                task_id="task-xss",
                run_id="run-xss",
                owner_id="u1",
                status="generated",
                title="XSS report",
                verdict="false_positive",
            ),
        ]
    )
    await session.flush()

    svc = ReportService(ReportRepository(session))
    items, total = await svc.list_reports("u1", verdict="confirmed", query="sql")

    assert total == 1


@pytest.mark.asyncio
async def test_report_detail_and_evidence_require_owner(session):
    from app.contexts.report.models import Evidence, Report
    from app.contexts.report.repository import ReportRepository
    from app.contexts.report.service import ReportService

    report = Report(
        task_id="task-secret",
        run_id="run-secret",
        owner_id="u1",
        status="generated",
        title="secret",
    )
    session.add(report)
    await session.flush()
    session.add(
        Evidence(
            report_id=report.id,
            task_id=report.task_id,
            object_key="secret.log",
            file_name="secret.log",
        )
    )
    await session.flush()
    svc = ReportService(ReportRepository(session))

    assert await svc.get_report(report.id, "u2") is None
    assert await svc.get_report_by_task(report.task_id, "u2") is None
    assert await svc.list_evidence(report.id, "u2") is None


@pytest.mark.asyncio
async def test_get_report_by_task_returns_latest_run(session):
    """同一任务多次 run 各有一份报告时，按 task 读取必须拿最新 run，不能 500。"""
    from datetime import datetime, timedelta, timezone

    from app.contexts.report.models import Report
    from app.contexts.report.repository import ReportRepository
    from app.contexts.report.service import ReportService

    older = datetime(2026, 8, 1, tzinfo=timezone.utc)
    newer = older + timedelta(hours=2)
    session.add_all(
        [
            Report(
                task_id="t-multi",
                run_id="run-old",
                owner_id="u1",
                status="generated",
                title="第一次",
                verdict="false_positive",
                created_at=older,
            ),
            Report(
                task_id="t-multi",
                run_id="run-new",
                owner_id="u1",
                status="generated",
                title="第二次",
                verdict="not_reproduced",
                created_at=newer,
            ),
        ]
    )
    await session.flush()

    svc = ReportService(ReportRepository(session))
    got = await svc.get_report_by_task("t-multi", "u1")
    assert got is not None
    assert got.run_id == "run-new"
    assert got.title == "第二次"
