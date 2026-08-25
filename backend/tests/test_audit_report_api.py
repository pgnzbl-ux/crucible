"""审计报告 Tab API（discovery-spec §9.3）。"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest_asyncio.fixture
async def client_env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from app.main import create_app
    from app.core.database import get_db_session

    app = create_app()

    async def _override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as c:
        yield c, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(factory):
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding
    from app.contexts.identity.models import User
    from app.contexts.report.models import Report
    from app.contexts.task.models import Task, TaskRun

    async with factory() as s:
        s.add(User(id="u1", email="u1@x.test", password_hash="x", role="analyst", display_name="U1"))
        task = Task(
            project_address="https://github.com/a/b.git",
            project_ref="main",
            task_type="discovery",
            vulnerability_description=None,
            owner_id="u1",
            status="completed",
        )
        s.add(task)
        await s.flush()
        run = TaskRun(task_id=task.id, status="completed")
        s.add(run)
        await s.flush()
        sr = ScanRun(
            task_id=task.id, run_id=run.id, node_run_id="nr", engine="semgrep",
            status="completed", config_summary={},
        )
        s.add(sr)
        await s.flush()
        f = RawFinding(
            task_id=task.id, scan_run_id=sr.id, engine="semgrep", rule_id="python.sqli",
            cwe="CWE-89", severity="error", file_path="app/db.py", line_start=1,
            line_end=2, message="sqli", fingerprint=hashlib.sha256(b"audit").hexdigest(),
            raw={},
        )
        s.add(f)
        await s.flush()
        g = AlertGroup(
            task_id=task.id, group_key="gk1", cwe="CWE-89", file_path="app/db.py",
            member_count=1, representative_finding_id=f.id, engine_set=["semgrep"],
            status="resolved", resolution="confirmed", verification_basis="code_path",
            vuln_report={
                "schema_version": 1,
                "summary": "SQL 注入确认",
                "reasoning": "入口可达",
                "final_verdict": "confirmed",
                "verification_basis": "code_path",
                "engines": ["semgrep"],
                "remediation": "参数化",
                "locus": {"file_path": "app/db.py"},
            },
        )
        s.add(g)
        s.add(Report(
            task_id=task.id, run_id=run.id, owner_id="u1", status="generated",
            title="审计摘要", conclusion="exists",
        ))
        await s.commit()
        return task.id, g.id


def _auth() -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token('u1', 'u1@x.test')}"}


def test_list_audit_tasks_and_vuln_detail(client_env):
    client, factory = client_env
    task_id, group_id = asyncio.run(_seed(factory))
    headers = _auth()

    r = client.get("/api/v1/reports/audits", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert any(i["task_id"] == task_id for i in body["items"])
    item = next(i for i in body["items"] if i["task_id"] == task_id)
    assert item["confirmed_count"] == 1
    assert item["vuln_report_count"] == 1

    r2 = client.get(f"/api/v1/reports/audits/{task_id}/vulns", headers=headers)
    assert r2.status_code == 200
    vulns = r2.json()
    assert vulns["total"] == 1
    assert vulns["items"][0]["summary"] == "SQL 注入确认"

    r3 = client.get(f"/api/v1/reports/audits/{task_id}/vulns/{group_id}", headers=headers)
    assert r3.status_code == 200
    assert r3.json()["verification_basis"] == "code_path"

    r4 = client.get(
        f"/api/v1/findings/groups/{group_id}/report/export?format=md",
        headers=headers,
    )
    assert r4.status_code == 200
    assert "SQL 注入确认" in r4.text


def test_list_audit_tasks_keeps_latest_report_after_retry(client_env):
    """同一 discovery 任务多份 Report 时列表只出现一行（取最新）。"""
    client, factory = client_env
    task_id, _group_id = asyncio.run(_seed(factory))

    async def _extra_report():
        from app.contexts.report.models import Report
        from app.contexts.task.models import TaskRun

        async with factory() as s:
            run = TaskRun(task_id=task_id, status="completed")
            s.add(run)
            await s.flush()
            s.add(Report(
                task_id=task_id, run_id=run.id, owner_id="u1", status="generated",
                title="重试后报告",
            ))
            await s.commit()

    asyncio.run(_extra_report())
    body = client.get("/api/v1/reports/audits", headers=_auth()).json()
    matched = [i for i in body["items"] if i["task_id"] == task_id]
    assert len(matched) == 1
    assert body["total"] == 1
