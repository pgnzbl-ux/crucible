"""编排器 — 节点循环 + 分支出口 + 断点续跑测试。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import patch, AsyncMock

from app.shared.base import Base


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.project.models import Project  # noqa: F401
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_task_run(session, is_web=True, status="running"):
    """建一个 task + run,返回 (task, run)。"""
    from app.contexts.task.models import Task, TaskRun

    task = Task(
        project_address="https://github.com/a/b.git",
        vulnerability_description="SQL injection in login",
        owner_id="u1", status=status,
    )
    session.add(task)
    await session.flush()
    run = TaskRun(task_id=task.id, status="running")
    session.add(run)
    await session.flush()
    return task, run


@pytest.mark.asyncio
async def test_non_web_exits_after_profile(session_factory):
    """节点 1 判 is_web=False → 节点 2-5 skipped,task completed 无 verdict。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)

        # mock:节点 0/1 真跑,节点 1 返回 is_web=False
        real_nodes = orch.NODE_ORDER

        async def fake_source(ctx):
            return {"source_path": ctx.source_path}

        async def fake_profile(ctx):
            return {"is_web": False, "language": "python"}

        with patch.object(real_nodes[0], "execute", fake_source), \
             patch.object(real_nodes[1], "execute", fake_profile):
            # 节点 2-5 即使被调也不应执行(分支跳过);mock 它们以便检测
            for n in real_nodes[2:]:
                patch.object(n, "execute", AsyncMock(side_effect=AssertionError("不该执行"))).__enter__()

            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "completed"
        assert result["non_web"] is True
        assert result["verdict"] is None

        # 节点 2-5 应 skipped
        nodes = (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index)
        )).scalars().all()
        statuses = [n.status for n in nodes]
        assert statuses[0] == "completed"  # source
        assert statuses[1] == "completed"  # profile
        for s in statuses[2:]:
            assert s == "skipped"


@pytest.mark.asyncio
async def test_missing_is_web_does_not_enter_env_ready(session_factory):
    """画像未给出显式 is_web=True 时 fail-closed，不得当 web 继续搭靶场。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)
        real_nodes = orch.NODE_ORDER

        async def fake_source(ctx):
            return {"source_path": ctx.source_path}

        async def fake_profile(ctx):
            return {"language": "python"}

        with patch.object(real_nodes[0], "execute", fake_source), \
             patch.object(real_nodes[1], "execute", fake_profile):
            for n in real_nodes[2:]:
                patch.object(n, "execute", AsyncMock(side_effect=AssertionError("不该执行"))).__enter__()

            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "completed"
        assert result["non_web"] is True
        nodes = (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index)
        )).scalars().all()
        for n in nodes[2:]:
            assert n.status == "skipped"


@pytest.mark.asyncio
async def test_gate_fail_skips_reproduce_and_sets_false_positive(session_factory):
    """节点 3 gate_verdict=fail → 节点 4 skipped,verdict=false_positive。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)

        async def fake_source(ctx):
            return {"source_path": ctx.source_path}

        async def fake_profile(ctx):
            return {"is_web": True, "language": "python", "framework": "flask", "port": 5000}

        async def fake_env(ctx):
            return {"target_url": "http://localhost:5000", "compose_path": "x.yml"}

        async def fake_audit(ctx):
            return {"gate_verdict": "fail", "gate_reason": "链路不通"}

        # 节点 4 reproduce 不该执行;节点 5 report 在 gate_fail 后仍跑(产 final_verdict)
        async def fake_report(ctx):
            return {"report_data": {"x": 1}, "final_verdict": "false_positive"}

        real_nodes = orch.NODE_ORDER
        patches = [
            patch.object(real_nodes[0], "execute", fake_source),
            patch.object(real_nodes[1], "execute", fake_profile),
            patch.object(real_nodes[2], "execute", fake_env),
            patch.object(real_nodes[3], "execute", fake_audit),
            patch.object(real_nodes[4], "execute", AsyncMock(side_effect=AssertionError("gate fail 不该执行 reproduce"))),
            patch.object(real_nodes[5], "execute", fake_report),
        ]
        for p in patches:
            p.__enter__()

        result = await orch.run_orchestration(
            task_id=task.id, run_id=run.id, session=session,
            host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
        )

        assert result["status"] == "completed"
        assert result["verdict"] == "false_positive"

        nodes = (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index)
        )).scalars().all()
        statuses = [n.status for n in nodes]
        assert statuses[0] == "completed"  # source
        assert statuses[1] == "completed"  # profile
        assert statuses[2] == "completed"  # env_ready
        assert statuses[3] == "completed"  # audit
        assert statuses[4] == "skipped"  # reproduce(gate fail 跳过)
        assert statuses[5] == "completed"  # report


@pytest.mark.asyncio
async def test_breakpoint_resume_skips_completed_nodes(session_factory):
    """断点续跑:已有 completed 节点(含 output_json)的不重算。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)
        # 预建 source(0)+ profile(1) 为 completed,带 output
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=0, node_key="source",
            status="completed", output_json='{"source_path": "/precomputed"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=1, node_key="profile",
            status="completed", output_json='{"is_web": true, "language": "go", "port": 8080}',
        ))
        await session.flush()

        call_count = {"source": 0, "profile": 0}

        async def counting_source(ctx):
            call_count["source"] += 1
            return {}

        async def counting_profile(ctx):
            call_count["profile"] += 1
            return {}

        real_nodes = orch.NODE_ORDER
        # 后续节点 mock(避免真起容器)
        async def fake_env(ctx):
            return {"target_url": "http://x:8080", "compose_path": "y.yml"}

        async def fake_audit(ctx):
            return {"gate_verdict": "pass"}

        async def fake_reproduce(ctx):
            return {"verdict": "confirmed", "reproduced": True}

        async def fake_report(ctx):
            return {"report_data": {"x": 1}, "final_verdict": "confirmed"}

        with patch.object(real_nodes[0], "execute", counting_source), \
             patch.object(real_nodes[1], "execute", counting_profile), \
             patch.object(real_nodes[2], "execute", fake_env), \
             patch.object(real_nodes[3], "execute", fake_audit), \
             patch.object(real_nodes[4], "execute", fake_reproduce), \
             patch.object(real_nodes[5], "execute", fake_report):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        # source/profile 不应被重算
        assert call_count == {"source": 0, "profile": 0}
        assert result["status"] == "completed"
        assert result["verdict"] == "confirmed"


@pytest.mark.asyncio
async def test_resume_reruns_source_when_workdir_has_no_repo(session_factory, tmp_path):
    """重试会拷贝 completed source，但工作区可能已被清掉；没有仓库目录就必须重拉。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun

    async with session_factory() as session:
        task, run = await _seed_task_run(session)
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=0, node_key="source",
            status="completed",
            output_json='{"repo_dirname":"claudecodeui","workspace_path":"/workspace/claudecodeui"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=1, node_key="profile",
            status="completed", output_json='{"is_web": true, "language": "nodejs"}',
        ))
        await session.flush()

        calls = {"source": 0}

        async def counting_source(ctx):
            calls["source"] += 1
            dest = tmp_path / "claudecodeui"
            dest.mkdir(exist_ok=True)
            return {
                "repo_dirname": "claudecodeui",
                "project_path": str(dest),
                "workspace_path": "/workspace/claudecodeui",
            }

        async def fake_env(ctx):
            return {"target_url": "http://x:8080", "compose_path": "y.yml"}

        async def fake_audit(ctx):
            return {"gate_verdict": "pass"}

        async def fake_reproduce(ctx):
            return {"verdict": "confirmed", "reproduced": True}

        async def fake_report(ctx):
            return {"report_data": {"x": 1}, "final_verdict": "confirmed"}

        real_nodes = orch.NODE_ORDER
        with patch.object(real_nodes[0], "execute", counting_source), \
             patch.object(real_nodes[2], "execute", fake_env), \
             patch.object(real_nodes[3], "execute", fake_audit), \
             patch.object(real_nodes[4], "execute", fake_reproduce), \
             patch.object(real_nodes[5], "execute", fake_report):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir=str(tmp_path), source_path=str(tmp_path), runner_env={},
            )

        assert calls["source"] == 1
        assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_resume_reuses_audit_gate_fail_skips_reproduce(session_factory):
    """断点续跑:已 completed 的 audit=fail 仍须跳过 reproduce,不得重跑 HTTP。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=0, node_key="source",
            status="completed", output_json='{"source_path": "/p"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=1, node_key="profile",
            status="completed", output_json='{"is_web": true, "language": "python"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=2, node_key="env_ready",
            status="completed", output_json='{"target_url": "http://localhost:5000", "compose_path": "x.yml"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=3, node_key="audit",
            status="completed", output_json='{"gate_verdict": "fail", "gate_reason": "链路不通"}',
        ))
        await session.flush()

        async def fake_report(ctx):
            return {"report_data": {"x": 1}, "final_verdict": "false_positive"}

        real_nodes = orch.NODE_ORDER
        with patch.object(real_nodes[4], "execute", AsyncMock(side_effect=AssertionError("续跑不得执行 reproduce"))), \
             patch.object(real_nodes[5], "execute", fake_report):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "completed"
        assert result["verdict"] == "false_positive"
        nodes = (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index)
        )).scalars().all()
        statuses = {n.node_key: n.status for n in nodes}
        assert statuses["reproduce"] == "skipped"
        assert statuses["report"] == "completed"


@pytest.mark.asyncio
async def test_orchestration_stops_after_cancel(session_factory):
    """取消后不得继续跑后续节点，也不能把 task 写成 completed。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)
        profile_calls = {"n": 0}

        async def fake_source(ctx):
            t = await ctx.db_session.get(type(task), ctx.task_id)
            r = await ctx.db_session.get(type(run), ctx.run_id)
            t.status = "cancelled"
            r.status = "cancelled"
            await ctx.db_session.commit()
            return {"source_path": ctx.source_path}

        async def fake_profile(ctx):
            profile_calls["n"] += 1
            return {"is_web": True}

        real_nodes = orch.NODE_ORDER
        with patch.object(real_nodes[0], "execute", fake_source), \
             patch.object(real_nodes[1], "execute", fake_profile):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "cancelled"
        assert profile_calls["n"] == 0
        await session.refresh(task)
        assert task.status == "cancelled"
        nodes = (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index)
        )).scalars().all()
        by_key = {n.node_key: n.status for n in nodes}
        assert by_key.get("profile") in (None, "pending", "cancelled")


@pytest.mark.asyncio
async def test_orchestration_killed_node_does_not_overwrite_cancelled(session_factory):
    """容器被拆掉后 execute 抛错，不得把已取消任务改成 failed。"""
    from app.contexts.agent import orchestrator as orch

    async with session_factory() as session:
        task, run = await _seed_task_run(session)

        async def fake_source(ctx):
            t = await ctx.db_session.get(type(task), ctx.task_id)
            r = await ctx.db_session.get(type(run), ctx.run_id)
            t.status = "cancelled"
            r.status = "cancelled"
            await ctx.db_session.commit()
            raise RuntimeError("container killed")

        real_nodes = orch.NODE_ORDER
        with patch.object(real_nodes[0], "execute", fake_source):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "cancelled"
        await session.refresh(task)
        await session.refresh(run)
        assert task.status == "cancelled"
        assert run.status == "cancelled"


@pytest.mark.asyncio
async def test_gate_uncertain_skips_reproduce_but_runs_report(session_factory):
    """uncertain → skip reproduce 但 report 仍撰写 needs_review 验证记录。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun, Task
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)

        async def fake_source(ctx):
            return {"source_path": ctx.source_path}

        async def fake_profile(ctx):
            return {"is_web": True, "language": "python"}

        async def fake_env(ctx):
            return {"target_url": "http://localhost:5000", "compose_path": "x.yml"}

        async def fake_audit(ctx):
            return {"gate_verdict": "uncertain", "gate_reason": "描述对不上"}

        async def fake_report(ctx):
            return {
                "report_data": {"document_kind": "verification_record", "product_intro": "待复核说明"},
                "final_verdict": "needs_review",
                "authored_by": "reporter",
            }

        real_nodes = orch.NODE_ORDER
        patches = [
            patch.object(real_nodes[0], "execute", fake_source),
            patch.object(real_nodes[1], "execute", fake_profile),
            patch.object(real_nodes[2], "execute", fake_env),
            patch.object(real_nodes[3], "execute", fake_audit),
            patch.object(real_nodes[4], "execute", AsyncMock(side_effect=AssertionError("uncertain 不该 reproduce"))),
            patch.object(real_nodes[5], "execute", fake_report),
        ]
        for p in patches:
            p.__enter__()

        result = await orch.run_orchestration(
            task_id=task.id, run_id=run.id, session=session,
            host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
        )

        assert result["status"] == "needs_review"
        # 任务级 verdict 仍为空（未确认漏洞），但补出了验证记录文档
        refreshed = await session.get(Task, task.id)
        assert refreshed.status == "needs_review"
        assert refreshed.verdict is None
        assert result["report_data"] is not None
        assert result["report_data"]["document_kind"] == "verification_record"

        nodes = (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index)
        )).scalars().all()
        by_key = {n.node_key: n for n in nodes}
        assert by_key["audit"].status == "completed"
        assert "描述对不上" in (by_key["audit"].output_json or "")
        assert by_key["reproduce"].status == "skipped"
        assert by_key["report"].status == "completed"


@pytest.mark.asyncio
async def test_resume_reuses_audit_uncertain_skips_reproduce_but_runs_report(session_factory):
    """续跑：已 completed 的 uncertain 仍跳过 reproduce，但 report 补出验证记录。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun, Task
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=0, node_key="source",
            status="completed", output_json='{"source_path": "/p"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=1, node_key="profile",
            status="completed", output_json='{"is_web": true}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=2, node_key="env_ready",
            status="completed",
            output_json='{"target_url": "http://localhost:5000", "compose_path": "x.yml"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=3, node_key="audit",
            status="completed",
            output_json='{"gate_verdict": "uncertain", "gate_reason": "对不上"}',
        ))
        await session.flush()

        async def fake_report(ctx):
            return {
                "report_data": {"document_kind": "verification_record", "product_intro": "待复核"},
                "final_verdict": "needs_review",
                "authored_by": "reporter",
            }

        real_nodes = orch.NODE_ORDER
        with patch.object(real_nodes[4], "execute", AsyncMock(side_effect=AssertionError("续跑不得 reproduce"))), \
             patch.object(real_nodes[5], "execute", fake_report):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "needs_review"
        refreshed = await session.get(Task, task.id)
        assert refreshed.status == "needs_review"
        statuses = {n.node_key: n.status for n in (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id)
        )).scalars().all()}
        assert statuses["reproduce"] == "skipped"
        assert statuses["report"] == "completed"
