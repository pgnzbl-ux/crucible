"""任务级 token 预算：台账记录、聚合、软停与可视化。

预算语义：耗尽后不再开新 agent 会话（triage 代表/lead 领取软停），
已判决结果保留、未审组转人工、任务照常收尾——不是硬杀。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

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


async def _seed_task(factory, *, budget: int | None = None) -> str:
    from app.contexts.identity.models import User
    from app.contexts.project.models import Project
    from app.contexts.settings.models import PlatformSetting
    from app.contexts.task.models import Task

    async with factory() as s:
        s.add(User(id="u1", email="u1@x.test", password_hash="x", display_name="U1"))
        s.add(Project(id="p1", name="demo", git_url="https://a/b", owner_id="u1"))
        s.add(Task(
            id="t1", project_address="https://a/b",
            vulnerability_description="d", owner_id="u1",
            project_id="p1", status="running",
        ))
        if budget is not None:
            s.add(PlatformSetting(
                singleton_key="default", max_concurrent_tasks=1,
                max_concurrent_agent_runners=4, lead_verify_per_task=2,
                reproduce_per_lab=1, task_token_budget=budget,
            ))
        await s.commit()
    return "t1"


@pytest.mark.asyncio
async def test_record_and_summary(factory):
    from app.contexts.agent.usage_ledger import (
        record_usage,
        task_usage_summary,
    )

    await _seed_task(factory)
    async with factory() as s:
        await record_usage(
            s, task_id="t1", run_id="r1", node_key="triage",
            usage={"prompt_tokens": 100, "completion_tokens": 50}, source="agent",
        )
        await record_usage(
            s, task_id="t1", run_id=None, node_key="triage",
            usage={"prompt_tokens": 10, "completion_tokens": 5}, source="fast_model",
        )
        await s.commit()

    async with factory() as s:
        summary = await task_usage_summary(s, "t1")
    assert summary == {
        "prompt_tokens": 110, "completion_tokens": 55,
        "total_tokens": 165, "sessions": 2,
    }


@pytest.mark.asyncio
async def test_budget_state_unlimited_and_exhausted(factory):
    from app.contexts.agent.usage_ledger import budget_state, record_usage

    await _seed_task(factory)  # 无 PlatformSetting → 不限
    async with factory() as s:
        await record_usage(
            s, task_id="t1", run_id=None, node_key="triage",
            usage={"prompt_tokens": 999999, "completion_tokens": 0}, source="agent",
        )
        await s.commit()
        exhausted, spent, budget = await budget_state(s, "t1")
    # 未配置预算：零成本短路，不聚合消耗
    assert (exhausted, spent, budget) == (False, 0, 0)

    async with factory() as s:
        from app.contexts.settings.models import PlatformSetting

        s.add(PlatformSetting(
            singleton_key="default", max_concurrent_tasks=1,
            max_concurrent_agent_runners=4, lead_verify_per_task=2,
            reproduce_per_lab=1, task_token_budget=100,
        ))
        await s.commit()
        exhausted, spent, budget = await budget_state(s, "t1")
    assert (exhausted, spent, budget) == (True, 999999, 100)


@pytest.mark.asyncio
async def test_triage_soft_stops_on_budget(factory, tmp_path):
    """预算耗尽：不再起 agent，未审组经兜底转 needs_review，任务照常收尾。"""
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding
    from app.contexts.task.models import TaskRun
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.agent.usage_ledger import record_usage

    await _seed_task(factory, budget=100)
    repo = tmp_path / "repo"
    (repo / "module").mkdir(parents=True, exist_ok=True)
    (repo / "module" / "db.py").write_text(
        "def handler(q):\n    return 'SELECT ' + q\n", encoding="utf-8",
    )
    async with factory() as s:
        s.add(TaskRun(id="r1", task_id="t1", status="running"))
        await s.flush()
        sr = ScanRun(
            task_id="t1", run_id="r1", node_run_id="nr", engine="semgrep",
            status="completed", config_summary={},
        )
        s.add(sr)
        await s.flush()
        f = RawFinding(
            task_id="t1", scan_run_id=sr.id, engine="semgrep", rule_id="r.a",
            cwe="CWE-89", severity="error", file_path="module/db.py",
            line_start=2, line_end=2, message="m", fingerprint="fp1", raw={},
        )
        s.add(f)
        await s.flush()
        s.add(AlertGroup(
            task_id="t1", group_key="gk1", cwe="CWE-89", file_path="module/db.py",
            line_span="1-3", member_count=1, representative_finding_id=f.id,
            engine_set=["semgrep"], status="clustered", clue_grade="B",
        ))
        await record_usage(  # 已烧超预算
            s, task_id="t1", run_id="r1", node_key="env_ready",
            usage={"prompt_tokens": 200, "completion_tokens": 10}, source="agent",
        )
        await s.commit()

    async with factory() as s:
        ctx = NodeContext(
            task_id="t1", run_id="r1", host_workdir=str(tmp_path),
            source_path=str(repo), vulnerability_description="",
            project_address="x", project_ref=None, project_id="p1",
            db_session=s, node_run_id="nr-triage",
            runner_env={"ANTHROPIC_API_KEY": "test"},
        )
        with patch("app.core.config.get_settings") as mock_settings:
            from tests.test_triage_cascade import _cascade_settings

            mock_settings.return_value = _cascade_settings(
                triage_fast_model_enabled=False,
            )
            with patch(
                "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                new_callable=AsyncMock,
            ) as agent:
                out = await TriageNode().execute(ctx, None)

        assert agent.await_count == 0
        assert out["budget_exhausted"] is True
        group = (await s.execute(select(AlertGroup))).scalars().one()
        assert group.status == "needs_review"


@pytest.mark.asyncio
async def test_get_task_includes_usage(factory):
    from app.contexts.agent.usage_ledger import record_usage
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    await _seed_task(factory)
    async with factory() as s:
        await record_usage(
            s, task_id="t1", run_id=None, node_key="triage",
            usage={"prompt_tokens": 7, "completion_tokens": 3}, source="fast_model",
        )
        await s.commit()
        detail = await TaskService(TaskRepository(s)).get_task("t1", "u1")
    assert detail is not None
    assert detail.usage == {
        "prompt_tokens": 7, "completion_tokens": 3,
        "total_tokens": 10, "sessions": 1,
    }
