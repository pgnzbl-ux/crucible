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
