"""WP4 · triage 走 Claude Agent SDK（agent-runner），不再用 llm_gateway。"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock, patch

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


def _settings(**kw):
    base = dict(
        triage_hide_sast_conclusion=True,
        triage_llm_concurrency=4,
        triage_high_confidence=0.8,
        triage_medium_confidence=0.5,
        claude_agent_sdk_enabled=True,
        # 本文件验证串行旧路径；级联管线见 test_triage_cascade.py
        triage_cascade_enabled=False,
        triage_stream_dispatch_enabled=False,
    )
    base.update(kw)
    return MagicMock(**base)


def _tp_output():
    return {
        "verdict": "tp",
        "confidence": 0.9,
        "why": ["x"],
        "evidence": [{"file": "app.py", "lines": "2-2"}],
        "need": [],
    }


# ---------- rubrics / prompt host 辅助 ----------

def test_rubrics_complete():
    from app.contexts.agent.nodes.triage.prompt import load_rubric

    for cwe in ("CWE-89", "CWE-78", "CWE-79", "CWE-22", "CWE-798",
                "CWE-502", "CWE-918", "CWE-611", "CWE-863", "CWE-601"):
        text = load_rubric(cwe)
        assert text and "伪消毒器" in text and "引导问题" in text, cwe
    assert load_rubric("CWE-999") is None


# ---------- 队列 ----------

def test_queue_ordering_and_skip():
    from app.contexts.agent.nodes.triage.queue import order_groups, should_skip_llm

    def g(cwe, priority, members=1, grade="B", *, file_path="app/db.py",
          engine_set=None, severity=""):
        return MagicMock(
            cwe=cwe, priority=priority, member_count=members, clue_grade=grade,
            file_path=file_path, engine_set=engine_set or ["semgrep"],
            rep_severity=severity, representative_finding_id="f",
        )

    ordered = order_groups([g("CWE-999", "medium"), g("CWE-89", "medium"), g("CWE-79", "high")])
    assert [x.cwe for x in ordered] == ["CWE-79", "CWE-89", "CWE-999"]

    by_sev = order_groups([
        g("CWE-89", "medium", severity="note"),
        g("CWE-78", "medium", severity="error"),
    ])
    assert [x.cwe for x in by_sev] == ["CWE-78", "CWE-89"]

    assert should_skip_llm(g("CWE-89", "low", file_path="tests/test_sqli.py")) is True
    assert should_skip_llm(g("CWE-89", "low", file_path="app/db.py")) is False
    assert should_skip_llm(g("CWE-89", "medium", grade="F")) is True
    assert should_skip_llm(g("CWE-89", "high")) is False


async def _seed_triage_env(session, tmp_path, groups_spec):
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.agent.contracts import ClusterInput, SourceHandoff
    import hashlib

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "app.py").write_text(
        "def handler(q):\n    return 'SELECT ' + q\n", encoding="utf-8",
    )
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_sqli.py").write_text(
        "def test_q(q):\n    return 'SELECT ' + q\n", encoding="utf-8",
    )
    task = Task(project_address="x", task_type="discovery",
                vulnerability_description=None, owner_id="u1", status="running")
    session.add(task)
    await session.flush()
    run = TaskRun(task_id=task.id, status="running")
    session.add(run)
    await session.flush()
    sr = ScanRun(task_id=task.id, run_id=run.id, node_run_id="nr-s", engine="semgrep",
                 status="completed", config_summary={})
    session.add(sr)
    await session.flush()

    for i, spec in enumerate(groups_spec):
        cwe, priority, grade, verdict = spec[:4]
        file_path = spec[4] if len(spec) > 4 else "app.py"
        fp = hashlib.sha256(f"f{i}".encode()).hexdigest()
        f = RawFinding(
            task_id=task.id, scan_run_id=sr.id, engine="semgrep", rule_id="python.sqli",
            cwe=cwe, severity="error", file_path=file_path, line_start=2, line_end=2,
            message="tainted query", source_to_sink=None, code_snippet=None,
            fingerprint=fp, raw={},
        )
        session.add(f)
        await session.flush()
        session.add(AlertGroup(
            task_id=task.id, group_key=f"gk{i}", cwe=cwe, file_path=file_path,
            function_symbol="handler", line_span="1-3", member_count=1,
            representative_finding_id=f.id, engine_set=["semgrep"],
            status="clustered", clue_grade=grade, priority=priority,
            **({"ai_verdict": verdict} if verdict else {}),
        ))
    await session.flush()

    ctx = NodeContext(
        task_id=task.id, run_id=run.id, host_workdir=str(tmp_path),
        source_path=str(repo), vulnerability_description="",
        project_address="x", project_ref=None, db_session=session,
        node_run_id="nr-triage", runner_env={"ANTHROPIC_API_KEY": "test"},
    )
    return ctx, task


@pytest.mark.asyncio
async def test_triage_processes_all_queued_groups(session_factory, tmp_path):
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.finding.models import AlertGroup

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(
            session, tmp_path, [("CWE-89", "high", "B", None)] * 3,
        )
        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch(
                 "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                 new_callable=AsyncMock, return_value=_tp_output(),
             ) as ai:
            out = await TriageNode().execute(ctx, None)
        assert out["adjudicated_count"] == 3
        assert out["tp_count"] == 3
        assert out["skipped_unaudited_count"] == 0
        assert ai.await_count == 3
        assert all(c.kwargs["node_key"] == "triage" for c in ai.await_args_list)
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        assert all(g.status == "adjudicated" and g.ai_verdict == "tp" for g in groups)


@pytest.mark.asyncio
async def test_triage_stops_promptly_when_cancelled(session_factory, tmp_path):
    """取消穿透：逐组自查库内状态，取消后立即停止——
    未审组保持 clustered 原状，不会被扫成 needs_review。"""
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.finding.models import AlertGroup

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(
            session, tmp_path, [("CWE-89", "high", "B", None)] * 3,
        )

        calls = 0

        async def adjudicate_once_then_cancel(**kw):
            # 第一组审完的瞬间，API 侧把任务置为 cancelled（另一 session 已提交）
            nonlocal calls
            calls += 1
            task.status = "cancelled"
            await session.commit()
            return _tp_output()

        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch(
                 "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                 new=adjudicate_once_then_cancel,
             ):
            out = await TriageNode().execute(ctx, None)

        assert out["status"] == "cancelled"
        assert out["adjudicated_count"] == 1
        assert calls == 1  # 剩余两组不再起容器烧 LLM
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        statuses = sorted(g.status for g in groups)
        assert statuses == ["adjudicated", "clustered", "clustered"]


@pytest.mark.asyncio
async def test_triage_emits_progress_events(session_factory, tmp_path):
    from app.contexts.agent.nodes.triage import TriageNode

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(
            session, tmp_path, [("CWE-89", "high", "B", None)] * 2,
        )
        events: list[dict] = []
        ctx.on_event = events.append
        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch(
                 "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                 new_callable=AsyncMock, return_value=_tp_output(),
             ):
            await TriageNode().execute(ctx, None)
        progress = [e for e in events if e.get("type") == "triage.progress"]
        assert progress and progress[-1]["adjudicated"] == 2
        assert progress[-1]["node_key"] == "triage"
        assert "message" in progress[-1]
        assert progress[-1]["message"].startswith("二审 ")
        phases = [e for e in events if e.get("type") == "phase.updated"]
        assert any("待审" in str(e.get("message")) for e in phases)
        assert any("二审" in str(e.get("message")) for e in phases)


@pytest.mark.asyncio
async def test_triage_downgraded_path_skips_llm_to_review(session_factory, tmp_path):
    from app.contexts.agent.nodes.screen import ScreenNode
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.finding.models import AlertGroup

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(
            session, tmp_path,
            [
                ("CWE-89", "low", "B", None, "tests/test_sqli.py"),
                ("CWE-79", "low", "B", None, "app.py"),
            ],
        )
        # screen 负责 skip_llm；升级组进 triage（本文件默认 cascade 关闭）
        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch(
                 "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                 new_callable=AsyncMock, return_value=_tp_output(),
             ) as ai:
            screen_out = await ScreenNode().execute(ctx, None)
            ctx.previous_outputs = {"screen": screen_out}
            out = await TriageNode().execute(ctx, None)
        assert screen_out["skipped_llm_count"] == 1
        assert out["adjudicated_count"] == 1
        assert out["skipped_llm_count"] == 1
        assert ai.await_count == 1
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        skipped = [g for g in groups if g.file_path.startswith("tests/")][0]
        assert skipped.status == "needs_review" and skipped.ai_verdict is None


@pytest.mark.asyncio
async def test_triage_runner_failure_degrades_to_review(session_factory, tmp_path):
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.finding.models import AlertGroup
    from app.core.agent_runner import AgentRunnerError

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(session, tmp_path, [("CWE-89", "high", "B", None)])

        async def boom(**kw):
            raise AgentRunnerError("容器失败")

        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch(
                 "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                 new=boom,
             ):
            out = await TriageNode().execute(ctx, None)
        assert out["adjudicated_count"] == 0
        group = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().one()
        assert group.status == "needs_review"
        assert group.ai_verdict is None


@pytest.mark.asyncio
async def test_triage_llm_balance_failure_aborts_node(session_factory, tmp_path):
    """余额不足等平台级 LLM 失败必须炸掉 triage，不得降级转人工后继续下游。"""
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.finding.models import AlertGroup
    from app.core.agent_runner import AgentRunnerError

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(
            session, tmp_path,
            [("CWE-89", "high", "A", None), ("CWE-79", "high", "A", None)],
        )

        async def boom(**kw):
            raise AgentRunnerError(
                'AI 节点 triage LLM 调用失败: HTTP 401: {"error":{"code":"1004","message":"余额不足"}}'
            )

        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch(
                 "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                 new=boom,
             ):
            with pytest.raises(AgentRunnerError, match="余额不足"):
                await TriageNode().execute(ctx, None)

        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        # 未伪装成 needs_review：保持 clustered，便于充值后重跑二审
        assert all(g.status == "clustered" for g in groups)
        assert all(g.ai_verdict is None for g in groups)


@pytest.mark.asyncio
async def test_triage_input_hides_engine_conclusion_by_default(session_factory, tmp_path):
    from app.contexts.agent.nodes.triage import TriageNode

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(session, tmp_path, [("CWE-89", "high", "B", None)])
        captured: dict = {}

        async def capture(**kw):
            captured.update(kw)
            return _tp_output()

        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch(
                 "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                 new=capture,
             ):
            await TriageNode().execute(ctx, None)
        assert "engine_conclusion" not in captured["input_json"]
        assert captured["input_json"]["closed_question"]
        assert "CWE-89" in (captured["input_json"].get("rubric") or "")


@pytest.mark.asyncio
async def test_triage_input_uses_container_source_path(session_factory, tmp_path):
    """triage input 的 source_path 必须是容器路径（/workspace/...），不得带宿主绝对路径。"""
    from app.contexts.agent.nodes.triage import TriageNode

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(session, tmp_path, [("CWE-89", "high", "B", None)])
        ctx.previous_outputs = {
            "source": {"repo_dirname": "repo", "commit_sha": "abc",
                       "project_path": str(tmp_path / "repo")},
        }
        captured: dict = {}

        async def capture(**kw):
            captured.update(kw)
            return _tp_output()

        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=capture):
            await TriageNode().execute(ctx, None)
        sp = captured["input_json"]["source_path"]
        assert sp == "/workspace/repo"
        assert str(tmp_path) not in json.dumps(captured["input_json"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_triage_records_real_prompt_and_usage(session_factory, tmp_path):
    """Adjudication 审计链落容器回传的真实 prompt/usage/model（spec §4.2）。"""
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.finding.models import Adjudication

    async with session_factory() as session:
        ctx, task = await _seed_triage_env(session, tmp_path, [("CWE-89", "high", "B", None)])

        async def capture(**kw):
            kw["meta_out"].update({
                "model": "test-model",
                "prompt": "按 system 完成本节点。完成后必须调用 submit_result。",
                "system_append": "# 代码审计二审员\n你只审当前这一组线索。",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "assistant_text": "先看切片，再下结论",
            })
            return _tp_output()

        with patch("app.core.config.get_settings", return_value=_settings()), \
             patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=capture):
            await TriageNode().execute(ctx, None)
        adj = (await session.execute(
            select(Adjudication).where(Adjudication.alert_group_id.isnot(None))
        )).scalars().one()
        assert adj.model == "test-model"
        assert adj.usage["prompt_tokens"] == 10
        assert adj.usage["completion_tokens"] == 5
        assert "[system]" in adj.prompt_text and "代码审计二审员" in adj.prompt_text
        assert "submit_result" in adj.prompt_text  # 真实 user prompt，非 input repr
        assert adj.response_text == "先看切片，再下结论"
