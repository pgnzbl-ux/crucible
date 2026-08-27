"""
审计回归测试 (v3.0.0 漏洞与状态机收敛):
- F-48: verify 任务失败/取消后, AlertGroup 自动由 dispatched 退回 needs_review
- F-51: 从 dispatch/lead_verify 重试时，清理 LeadRun 并重置滞留 dispatched 组
- F-52: 节点失败优雅收尾时触发未终认 leads 回收
- F-54: 报告字段超长截断钳制 (防 PG DataError 溢出)
- F-57: 限流 Lua 脚本原子性
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contexts.agent.tasks import report_columns_from_orch_result
from app.contexts.discovery.models import ScanRun
from app.contexts.finding.models import AlertGroup, LeadNodeRun, LeadRun, RawFinding
from app.contexts.finding.service import FindingService
from app.contexts.identity.models import User  # noqa: F401
from app.contexts.lab.models import Lab  # noqa: F401
from app.contexts.project.models import Project  # noqa: F401
from app.contexts.report.models import Report  # noqa: F401
from app.contexts.settings.models import LlmProvider  # noqa: F401
from app.contexts.task.models import AgentEvent, NodeRun, Task, TaskRun  # noqa: F401
from app.shared.base import Base
from app.shared.rate_limit import _RATE_LIMIT_LUA, _redis_check


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_f48_reconcile_from_failed_or_cancelled_task_resets_dispatched_group(session):
    svc = FindingService(session)
    task_id = "t-audit-f48"

    scan_run = ScanRun(task_id=task_id, run_id="r-f48", node_run_id="nr-f48", engine="semgrep", status="completed")
    session.add(scan_run)
    await session.flush()

    raw_finding = RawFinding(
        task_id=task_id,
        scan_run_id=scan_run.id,
        engine="semgrep",
        fingerprint="fp-f48",
        file_path="src/main.py",
        line_start=10,
        message="SQLi",
        rule_id="semgrep-sqli",
    )
    session.add(raw_finding)
    await session.flush()

    group = AlertGroup(
        task_id=task_id,
        group_key="gk-f48",
        file_path="src/main.py",
        representative_finding_id=raw_finding.id,
        status="dispatched",
    )
    session.add(group)
    await session.flush()

    # 1. verify 任务 failed -> 组退回 needs_review
    failed_task = SimpleNamespace(
        id="t-v-1",
        task_type="verify",
        source_alert_group_id=group.id,
        status="failed",
        verdict=None,
    )
    res = await svc.reconcile_from_task(failed_task)
    assert res is not None
    assert res.status == "needs_review"

    # 2. verify 任务 cancelled -> 组退回 needs_review
    group.status = "dispatched"
    await session.flush()

    cancelled_task = SimpleNamespace(
        id="t-v-2",
        task_type="verify",
        source_alert_group_id=group.id,
        status="cancelled",
        verdict=None,
    )
    res2 = await svc.reconcile_from_task(cancelled_task)
    assert res2 is not None
    assert res2.status == "needs_review"


@pytest.mark.asyncio
async def test_f51_purge_for_retry_resets_dispatched_alert_groups(session):
    svc = FindingService(session)
    task_id = "t-audit-f51"

    scan_run = ScanRun(task_id=task_id, run_id="r-f51", node_run_id="nr-f51", engine="semgrep", status="completed")
    session.add(scan_run)
    await session.flush()

    rf1 = RawFinding(
        task_id=task_id,
        scan_run_id=scan_run.id,
        engine="semgrep",
        fingerprint="fp-1",
        file_path="src/a.py",
        line_start=1,
        message="RCE",
        rule_id="r1",
    )
    rf2 = RawFinding(
        task_id=task_id,
        scan_run_id=scan_run.id,
        engine="semgrep",
        fingerprint="fp-2",
        file_path="src/b.py",
        line_start=1,
        message="SSRF",
        rule_id="r2",
    )
    session.add_all([rf1, rf2])
    await session.flush()

    g1 = AlertGroup(
        task_id=task_id,
        group_key="gk-1",
        file_path="src/a.py",
        representative_finding_id=rf1.id,
        status="dispatched",
    )
    g2 = AlertGroup(
        task_id=task_id,
        group_key="gk-2",
        file_path="src/b.py",
        representative_finding_id=rf2.id,
        status="needs_review",
    )
    session.add_all([g1, g2])
    await session.flush()

    # 重试清理 dispatch / lead_verify
    await svc.purge_for_retry(task_id, from_node="dispatch")

    await session.refresh(g1)
    await session.refresh(g2)
    assert g1.status == "needs_review"
    assert g2.status == "needs_review"


def test_f54_report_columns_clamps_long_fields():
    long_str_500 = "A" * 500
    long_str_2000 = "B" * 2000
    orch_result = {
        "report_data": {"document_kind": "code_audit_report"},
        "verdict": "confirmed",
        "title": long_str_500,
        "product_name": long_str_500,
        "affected_version": long_str_500,
        "vulnerable_file": long_str_2000,
        "cvss": {"base_score": 9.8, "severity": "CRITICAL" * 10},
    }

    cols = report_columns_from_orch_result(orch_result)
    assert len(cols["title"]) <= 255
    assert len(cols["product_name"]) <= 255
    assert len(cols["affected_version"]) <= 64
    assert len(cols["vulnerable_file"]) <= 1024
    assert len(cols["severity"]) <= 20


def test_f57_rate_limit_uses_atomic_lua_script(monkeypatch):
    mock_redis = MagicMock()
    mock_redis.eval.return_value = 1

    monkeypatch.setattr("app.shared.rate_limit._get_redis", lambda: mock_redis)
    res = _redis_check("test_key", limit=5, window_seconds=60)
    assert res is True
    mock_redis.eval.assert_called_once()
    args, kwargs = mock_redis.eval.call_args
    assert args[0] == _RATE_LIMIT_LUA
    assert args[1] == 1
    assert args[2] == "rl:test_key"
    assert args[3] == 60


@pytest.mark.asyncio
async def test_f52_finalize_node_failure_invokes_terminalize_leads(session, monkeypatch):
    from app.contexts.agent.contracts.handoff_store import HandoffStore
    from app.contexts.agent.contracts.registry import NodeSpec
    from app.contexts.agent.orchestrator import _finalize_node_failure

    task = Task(
        id="t-f52",
        owner_id="u1",
        status="running",
        project_address="https://example.com/repo.git",
    )
    run = TaskRun(
        id="r-f52",
        task_id=task.id,
        status="running",
    )
    session.add_all([task, run])
    await session.commit()

    spec = NodeSpec(key="lead_verify", index=5, requires=(), produces="lead_verdicts", failure_policy="abort_pipeline")
    store = HandoffStore()

    terminalize_mock = AsyncMock()
    monkeypatch.setattr(
        "app.contexts.agent.lead_worker.terminalize_task_leads",
        terminalize_mock,
    )

    try:
        raise RuntimeError("LLM worker crashed")
    except Exception as exc:
        err = exc

    await _finalize_node_failure(
        session=session,
        task=task,
        run=run,
        spec=spec,
        nr_id="nr-f52",
        host_workdir="/tmp",
        store=store,
        final_verdict=None,
        error=err,
        on_node_event=None,
    )

    assert task.status == "failed"
    assert run.status == "failed"
    terminalize_mock.assert_awaited_once()
