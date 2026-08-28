"""triage 批量子代理模式：整节点一次容器、family 判决逐族落库、契约分支。"""
import hashlib
import sys
import os
from types import SimpleNamespace
from unittest.mock import patch

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


def _batch_settings(**kw):
    base = dict(
        triage_hide_sast_conclusion=True,
        claude_agent_sdk_enabled=True,
        triage_cascade_enabled=True,
        triage_carryover_enabled=True,
        triage_rule_enabled=True,
        triage_fast_model_enabled=True,
        triage_family_enabled=True,
        # 批量子代理路径的关键开关：必须是字面 True 才路由进批量
        triage_subagent_mode=True,
        triage_propagate_min_confidence=0.6,
        triage_propagate_confidence_factor=0.85,
        triage_concurrency=2,
        triage_llm_transient_retries=1,
        triage_llm_transient_fatal_streak=3,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def _seed_two_families(factory, tmp_path):
    """两个根因族（各 2 组），ctx 带 session_factory（批量路径必需）。"""
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding
    from app.contexts.task.models import Task, TaskRun

    repo = tmp_path / "repo"
    (repo / "module").mkdir(parents=True, exist_ok=True)
    (repo / "module" / "db.py").write_text(
        "def handler(q):\n    return 'SELECT ' + q\n", encoding="utf-8",
    )

    async with factory() as session:
        task = Task(
            project_address="x", task_type="discovery",
            vulnerability_description=None, owner_id="u1",
            status="running", project_id=None,
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="running")
        session.add(run)
        await session.flush()
        sr = ScanRun(
            task_id=task.id, run_id=run.id, node_run_id="nr-x",
            engine="semgrep", status="completed", config_summary={},
        )
        session.add(sr)
        await session.flush()

        gids = []
        for i, spec in enumerate([
            {"rule": "r.a", "file_path": "module/db.py"},
            {"rule": "r.a", "file_path": "module/db2.py"},
            {"rule": "r.b", "file_path": "module/db3.py"},
            {"rule": "r.b", "file_path": "module/db4.py"},
        ]):
            f = RawFinding(
                task_id=task.id, scan_run_id=sr.id, engine="semgrep",
                rule_id=spec["rule"], cwe="CWE-89", severity="error",
                file_path=spec["file_path"], line_start=2, line_end=2,
                message="tainted query",
                fingerprint=hashlib.sha256(
                    f"{spec['rule']}:{spec['file_path']}:{i}".encode()
                ).hexdigest(),
                raw={},
            )
            session.add(f)
            await session.flush()
            group = AlertGroup(
                task_id=task.id,
                group_key=f"gk-{i}",
                cwe="CWE-89", file_path=spec["file_path"],
                function_symbol="handler", line_span="1-3", member_count=1,
                representative_finding_id=f.id, engine_set=["semgrep"],
                status="clustered", clue_grade="B", priority="high",
            )
            session.add(group)
            gids.append(group)
        await session.commit()
        task_id, run_id = task.id, run.id

    async with factory() as session:
        ctx = NodeContext(
            task_id=task_id, run_id=run_id,
            host_workdir=str(tmp_path), source_path=str(repo),
            vulnerability_description="", project_address="x",
            project_ref=None, db_session=session, node_run_id="nr-triage",
            runner_env={"ANTHROPIC_API_KEY": "test"},
            session_factory=factory,
        )
        return ctx, session


def _batch_verdicts_output(gid_verdicts):
    items = []
    for gid, verdict, conf in gid_verdicts:
        item = {
            "group_id": gid, "verdict": verdict, "confidence": conf,
            "why": ["x"], "summary": "批量简述", "reasoning": "批量推理",
            "need": [],
        }
        if verdict == "tp":
            item.update(
                evidence=[{"file": "module/db.py", "lines": "2-2"}],
                attacker_controlled=True, reaches_sink=True, sanitizer="none",
            )
        items.append(item)
    return {"verdicts": items}


@pytest.mark.asyncio
async def test_batch_adjudicates_all_families_in_one_container(factory, tmp_path):
    """一次容器调用覆盖全部代表；成员传播链路不受影响。"""
    from app.contexts.agent.ai_runner import validate_output
    from app.contexts.finding.models import AlertGroup

    calls = []
    captured = {}

    async def agent_side_effect(**kw):
        calls.append(kw)
        captured.update(kw)
        assert kw.get("skill_override") == "triage_batch"
        assert kw["input_json"]["mode"] == "batch"
        assert len(kw["input_json"]["families"]) == 2
        # 代表由级联规则选出，不预设具体文件：第一个 tp，第二个 fp
        gids = [e["group_id"] for e in kw["input_json"]["families"]]
        return _batch_verdicts_output([(gids[0], "tp", 0.9), (gids[1], "fp", 0.9)])

    ctx, session = await _seed_two_families(factory, tmp_path)

    async def agent_entry(**kw):
        return await agent_side_effect(**kw)

    with patch(
        "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
        new=agent_entry,
    ):
        outcome = await _run_triage(ctx)

    assert len(calls) == 1, "整个二审只允许一次容器执行"
    session.expire_all()  # 身份映射缓存的是判定前快照，断言前先失效
    groups = (await session.execute(
        select(AlertGroup).where(AlertGroup.task_id == ctx.task_id).order_by(AlertGroup.group_key)
    )).scalars().all()
    # 二审判 fp 的组在节点收尾被 discard_task_false_positives 清出：
    # 库里只剩 tp 代表（agent）与其传播成员
    assert len(calls) == 1, "整个二审只允许一次容器执行"
    agents = [g for g in groups if g.verdict_source == "agent"]
    propagated = [g for g in groups if g.verdict_source == "propagated"]
    assert len(propagated) == 1
    assert propagated[0].ai_confidence == pytest.approx(0.9 * 0.85)
    assert outcome["adjudicated_count"] == 2
    # 校验器接受该形态
    ok, err = validate_output("triage", _batch_verdicts_output([("g1", "tp", 0.9)]))
    assert ok, err


@pytest.mark.asyncio
async def test_batch_mock_sdk_disabled_skips_container(factory, tmp_path):
    """SDK 未启用：不起容器也按批量形状产出判决。"""
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.finding.models import AlertGroup

    ctx, session = await _seed_two_families(factory, tmp_path)
    st = _batch_settings(claude_agent_sdk_enabled=False)

    def explode(**kw):
        raise AssertionError("mock 路径不得起容器")

    with (
        patch("app.core.config.get_settings", return_value=st),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=explode),
    ):
        out = await TriageNode().execute(ctx, None)

    assert out["adjudicated_count"] == 2
    rows = (await session.execute(
        select(AlertGroup).where(AlertGroup.task_id == ctx.task_id)
    )).scalars().all()
    by_file = {g.file_path: g for g in rows}
    assert by_file["module/db.py"].ai_verdict == "tp"
    assert by_file["module/db3.py"].ai_verdict == "tp"


# ---------- helpers ----------


async def _run_triage(ctx):
    from app.contexts.agent.nodes.triage import TriageNode

    return await TriageNode().execute(ctx, None)


def _two_families(session):
    from app.contexts.agent.nodes.triage import cascade

    rows = (session.execute(
        select(AlertGroupWhereAll())
    )) if False else None
    raise NotImplementedError


class AlertGroupWhereAll:
    pass


def test_triage_output_validator_rejects_bad_batch_items():
    from app.contexts.agent.ai_runner import validate_output

    ok, _ = validate_output("triage", {"verdicts": []})
    assert not ok
    ok, err = validate_output("triage", {"verdicts": [{"verdict": "tp"}]})
    assert not ok and "group_id" in err
    ok, err = validate_output("triage", {
        "verdicts": [{"group_id": "g1", "verdict": "need_more_context",
                      "confidence": 0.5, "why": ["w"], "summary": "s", "reasoning": "r"}],
    })
    assert ok, err


def test_node_schema_declares_triage_batch():
    """submit 契约必须包含批量形态（backend 单一来源；经 AgentSpec 下发）。"""
    import json as _json

    from app.contexts.agent.contracts.node_input_schemas import NODE_INPUT_SCHEMAS

    schema = NODE_INPUT_SCHEMAS["triage_batch"]
    items = schema["properties"]["verdicts"]["items"]
    assert set(["group_id", "verdict", "confidence", "why", "summary", "reasoning"]) \
        <= set(items["required"])
    assert _json.dumps(schema)  # 可序列化子集


@pytest.mark.asyncio
async def test_batch_context_length_error_falls_back_to_single_group(factory, tmp_path):
    """当批量子代理模式因 Prompt 过长报错时，自动降级逐族单组审议，避免任务失败。"""
    from app.core.agent_runner import AgentRunnerError
    from app.contexts.finding.models import AlertGroup

    ctx, session = await _seed_two_families(factory, tmp_path)
    single_group_calls = []

    async def mock_batch_fails_with_prompt_too_long(**kw):
        if kw.get("skill_override") == "triage_batch":
            raise AgentRunnerError("AI 节点 triage LLM 调用失败: 400 Prompt is too long")
        # 降级单组路径
        single_group_calls.append(kw)
        return {
            "verdict": "tp",
            "confidence": 0.9,
            "why": ["fallback why"],
            "summary": "fallback summary",
            "reasoning": "fallback reasoning",
            "evidence": [{"file": "module/db.py", "lines": "2-2"}],
            "attacker_controlled": True,
            "reaches_sink": True,
            "sanitizer": "none",
        }

    with patch(
        "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
        new=mock_batch_fails_with_prompt_too_long,
    ):
        outcome = await _run_triage(ctx)

    assert outcome["adjudicated_count"] == 2
    assert len(single_group_calls) == 2, "遇到 Prompt is too long 应逐族单组完成审议"

