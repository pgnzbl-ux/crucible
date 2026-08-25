"""triage 分级收敛管线：携带 → 规则前置 → 快模型 → 族级传播。

级联的意义：1000+ 组只有少数不确定项走到全价 agent。各层判决必须
落审计行并标记 verdict_source，取消信号逐层穿透。
"""

import hashlib
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch

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


def _cascade_settings(**kw):
    base = dict(
        triage_hide_sast_conclusion=True,
        claude_agent_sdk_enabled=True,
        triage_high_confidence=0.8,
        triage_cascade_enabled=True,
        triage_carryover_enabled=True,
        triage_carryover_min_confidence=0.7,
        triage_rule_enabled=True,
        triage_rule_fp_rate_min=0.95,
        triage_rule_min_samples=20,
        triage_fast_model_enabled=True,
        triage_fast_confidence=0.75,
        triage_family_enabled=True,
        triage_propagate_min_confidence=0.6,
        triage_propagate_confidence_factor=0.85,
        triage_concurrency=2,
        triage_feedback_resolved_weight=3.0,
        triage_feedback_min_verified=10,
        triage_stream_dispatch_enabled=False,
    )
    base.update(kw)
    return MagicMock(**base)


def _agent_output(verdict="tp", confidence=0.9):
    out = {
        "verdict": verdict,
        "confidence": confidence,
        "why": ["x"],
        "evidence": [{"file": "app.py", "lines": "2-2"}],
        "need": [],
    }
    if verdict == "tp":
        out.update(attacker_controlled=True, reaches_sink=True, sanitizer="none")
    return out


async def _seed_env(
    factory,
    tmp_path,
    *,
    current: list[dict],
    prior_groups: list[dict] | None = None,
):
    """current/prior 每项：{rule, file_path, cwe, fingerprint?, verdict?, conf?, source?}。

    prior 落在历史任务(同项目、已判)里供 T0/T1 消费；current 是本次待审组。
    """
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding
    from app.contexts.identity.models import User
    from app.contexts.project.models import Project
    from app.contexts.task.models import Task, TaskRun

    repo = tmp_path / "repo"
    (repo / "module").mkdir(parents=True, exist_ok=True)
    (repo / "module" / "db.py").write_text(
        "def handler(q):\n    return 'SELECT ' + q\n",
        encoding="utf-8",
    )

    async def _add_task(status, groups_spec, *, adjudicated):
        task = Task(
            project_address="x",
            task_type="discovery",
            vulnerability_description=None,
            owner_id="u1",
            status=status,
            project_id="p1",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="completed" if adjudicated else "running")
        session.add(run)
        await session.flush()
        sr = ScanRun(
            task_id=task.id,
            run_id=run.id,
            node_run_id=f"nr-{task.id[:4]}",
            engine="semgrep",
            status="completed",
            config_summary={},
        )
        session.add(sr)
        await session.flush()
        for i, spec in enumerate(groups_spec):
            f = RawFinding(
                task_id=task.id,
                scan_run_id=sr.id,
                engine="semgrep",
                rule_id=spec["rule"],
                cwe=spec.get("cwe", "CWE-89"),
                severity="error",
                file_path=spec["file_path"],
                line_start=2,
                line_end=2,
                message="tainted query",
                fingerprint=spec.get("fingerprint")
                or hashlib.sha256(f"{spec['rule']}:{spec['file_path']}:{i}:{task.id[:4]}".encode()).hexdigest(),
                raw={},
            )
            session.add(f)
            await session.flush()
            group = AlertGroup(
                task_id=task.id,
                group_key=f"gk-{task.id[:4]}-{i}",
                cwe=spec.get("cwe", "CWE-89"),
                file_path=spec["file_path"],
                function_symbol="handler",
                line_span="1-3",
                member_count=1,
                representative_finding_id=f.id,
                engine_set=["semgrep"],
                status=spec.get("status", "adjudicated" if adjudicated else "clustered"),
                clue_grade=spec.get("grade", "B"),
                priority=spec.get("priority", "high"),
            )
            if adjudicated:
                group.ai_verdict = spec.get("verdict")
                group.ai_confidence = spec.get("conf", 0.9)
                group.verdict_source = spec.get("source")
            if spec.get("resolution"):
                # 验证真值回流形态：lead 终态回写的 resolved 组
                group.status = "resolved"
                group.resolution = spec["resolution"]
            session.add(group)
        await session.flush()
        return task

    async with factory() as session:
        session.add(User(id="u1", email="u1@x.test", password_hash="x", display_name="U1"))
        session.add(Project(id="p1", name="demo", git_url="https://a/b", owner_id="u1"))
        await session.flush()
        if prior_groups:
            await _add_task("completed", prior_groups, adjudicated=True)
        current_task = await _add_task("running", current, adjudicated=False)
        await session.commit()
        current_task_id = current_task.id

    async with factory() as session:
        ctx = NodeContext(
            task_id=current_task_id,
            run_id="r-cur",
            host_workdir=str(tmp_path),
            source_path=str(repo),
            vulnerability_description="",
            project_address="x",
            project_ref=None,
            project_id="p1",
            db_session=session,
            node_run_id="nr-triage",
            runner_env={"ANTHROPIC_API_KEY": "test"},
        )
        yield ctx, session


async def _groups_of(session, task_id):
    from app.contexts.finding.models import AlertGroup

    return (
        (await session.execute(select(AlertGroup).where(AlertGroup.task_id == task_id).order_by(AlertGroup.group_key)))
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_t0_carryover_reuses_prior_verdict(factory, tmp_path):
    """同项目同代表指纹：历史 tp 判决直接携带，不起 agent、不调快模型。"""
    from app.contexts.agent.nodes.screen import ScreenNode

    gen = _seed_env(
        factory,
        tmp_path,
        prior_groups=[
            {
                "rule": "r.a",
                "file_path": "module/db.py",
                "verdict": "fp",
                "conf": 0.9,
                "fingerprint": "FP-SAME",
            }
        ],
        current=[{"rule": "r.a", "file_path": "module/db.py", "fingerprint": "FP-SAME"}],
    )
    ctx, session = await gen.__anext__()

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings()),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new_callable=AsyncMock) as agent,
        patch("app.core.llm_gateway.llm_complete", new_callable=AsyncMock) as fast,
    ):
        out = await ScreenNode().execute(ctx, None)

    assert out["carried_count"] == 1
    assert out["escalated_count"] == 0
    agent.assert_not_called()
    fast.assert_not_called()
    groups = await _groups_of(session, ctx.task_id)
    assert groups == []  # 携带误报已丢弃
    await gen.aclose()


@pytest.mark.asyncio
async def test_screen_cascade_off_escalates_queue_intact(factory, tmp_path):
    """triage_cascade_enabled=false：screen 仍跑 skip_llm，其余组原样升级 triage。"""
    from app.contexts.agent.nodes.screen import ScreenNode
    from app.contexts.agent.nodes.triage import TriageNode

    gen = _seed_env(
        factory,
        tmp_path,
        current=[
            {"rule": "r.a", "file_path": "module/db.py"},
            {"rule": "r.b", "file_path": "module/db2.py"},
        ],
    )
    ctx, session = await gen.__anext__()

    async def agent_side_effect(**kw):
        return _agent_output("tp", 0.9)

    with (
        patch(
            "app.core.config.get_settings",
            return_value=_cascade_settings(
                triage_cascade_enabled=False,
                triage_fast_model_enabled=True,
            ),
        ),
        patch("app.core.llm_gateway.llm_complete", new_callable=AsyncMock) as fast,
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
    ):
        screen_out = await ScreenNode().execute(ctx, None)
        ctx.previous_outputs = {"screen": screen_out}
        out = await TriageNode().execute(ctx, None)

    assert screen_out["escalated_count"] == 2
    assert screen_out["fast_model_count"] == 0
    fast.assert_not_called()
    assert out["adjudicated_count"] == 2
    await gen.aclose()


@pytest.mark.asyncio
async def test_t1_rule_fp_rate_preverdict(factory, tmp_path):
    """规则历史 agent 亲审 FP 率达标 → 新命中直接判 fp。"""
    from app.contexts.agent.nodes.screen import ScreenNode
    from app.contexts.agent.nodes.triage import TriageNode

    noisy = [{"rule": "r.noisy", "file_path": f"module/f{i}.py", "verdict": "fp", "conf": 0.9} for i in range(20)]
    gen = _seed_env(
        factory,
        tmp_path,
        prior_groups=noisy,
        current=[
            {"rule": "r.noisy", "file_path": "module/new1.py"},
            {"rule": "r.fresh", "file_path": "module/new2.py"},
        ],
    )
    ctx, session = await gen.__anext__()

    async def agent_side_effect(**kw):
        return _agent_output("tp", 0.9)

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings(triage_fast_model_enabled=False)),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
    ):
        screen_out = await ScreenNode().execute(ctx, None)
        ctx.previous_outputs = {"screen": screen_out}
        out = await TriageNode().execute(ctx, None)

    assert screen_out["rule_count"] == 1
    groups = {g.file_path: g for g in await _groups_of(session, ctx.task_id)}
    assert "module/new1.py" not in groups  # 规则误报已丢弃
    # 非热规则不受影响，走 agent 亲审
    assert groups["module/new2.py"].verdict_source == "agent"
    assert out["adjudicated_count"] == 1
    await gen.aclose()


@pytest.mark.asyncio
async def test_t2_fast_model_fp_decides_tp_escalates(factory, tmp_path):
    """快审高置信误报定案；快审 tp 必须升级 T3，禁止入队。"""
    from app.contexts.agent.nodes.screen import ScreenNode
    from app.contexts.agent.nodes.triage import TriageNode

    gen = _seed_env(
        factory,
        tmp_path,
        current=[
            {"rule": "r.a", "file_path": "module/db.py"},
            {"rule": "r.b", "file_path": "module/db.py"},
        ],
    )
    ctx, session = await gen.__anext__()

    async def fast_side_effect(*, role, system, user, **kw):
        calls = fast_side_effect._n = getattr(fast_side_effect, "_n", 0) + 1
        text = (
            json.dumps({"verdict": "fp", "confidence": 0.9, "why": ["测试代码"], "need": []})
            if calls == 1
            else json.dumps({"verdict": "tp", "confidence": 0.9, "why": ["切片证实"], "need": []})
        )
        return SimpleNamespace(
            text=text,
            model="fast-1",
            provider_id="pv",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def agent_side_effect(**kw):
        return _agent_output("fp", 0.85)

    async def fake_provider(session, role):
        return SimpleNamespace(
            id="pv",
            model="fast-1",
            base_url="http://llm.test",
            api_key_encrypted="",
            timeout_ms=None,
        )

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings()),
        patch("app.core.llm_gateway._resolve_provider", new=fake_provider),
        patch("app.core.llm_gateway.llm_complete", new=fast_side_effect),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
    ):
        screen_out = await ScreenNode().execute(ctx, None)
        ctx.previous_outputs = {"screen": screen_out}
        out = await TriageNode().execute(ctx, None)

    assert screen_out["fast_model_count"] == 1  # 只有误报定案
    assert out["adjudicated_count"] == 1  # 快审 tp 升级后由 agent 亲审
    groups = await _groups_of(session, ctx.task_id)
    # 误报组已丢弃，不占线索台
    assert all(g.verdict_source != "fast_model" or g.ai_verdict != "fp" for g in groups)
    from app.contexts.task.models import AgentUsage
    rows = (await session.execute(select(AgentUsage).where(AgentUsage.task_id == ctx.task_id))).scalars().all()
    assert any(r.source == "fast_model" and r.prompt_tokens > 0 for r in rows)
    await gen.aclose()


def test_fast_provider_snapshot_keeps_request_settings():
    from app.contexts.agent.nodes.triage.cascade import _snapshot_provider

    provider = SimpleNamespace(
        id="pv",
        model="fast-1",
        base_url="http://llm.test",
        api_key_encrypted="sk-test",
        timeout_ms=42_000,
        temperature=0.7,
        effort="low",
    )

    snapshot = _snapshot_provider(provider)

    assert snapshot is not provider
    assert snapshot.temperature == 0.7
    assert snapshot.effort == "low"
    assert snapshot.timeout_ms == 42_000


@pytest.mark.asyncio
async def test_fast_screen_balance_failure_aborts_triage(factory, tmp_path):
    """快审遇到余额不足必须中止 screen，不得静默升级 agent 继续烧。"""
    from app.contexts.agent.nodes.screen import ScreenNode
    from app.core.agent_runner import AgentRunnerError
    from app.core.llm_gateway import LlmGatewayConfigError

    gen = _seed_env(
        factory,
        tmp_path,
        current=[
            {"rule": "r.a", "file_path": "module/db.py", "fingerprint": "A"},
            {"rule": "r.b", "file_path": "module/db.py", "fingerprint": "B"},
        ],
    )
    ctx, session = await gen.__anext__()

    async def boom(*, role, system, user, **kw):
        raise LlmGatewayConfigError(
            'LLM 网关调用失败(screening): HTTP 401: {"error":{"code":"1004","message":"余额不足"}}'
        )

    async def fake_provider(session, role):
        return SimpleNamespace(
            id="pv",
            model="fast-1",
            base_url="http://llm.test",
            api_key_encrypted="",
            timeout_ms=None,
        )

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings()),
        patch("app.core.llm_gateway._resolve_provider", new=fake_provider),
        patch("app.core.llm_gateway.llm_complete", new=boom),
        patch(
            "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
            new_callable=AsyncMock,
        ) as agent,
    ):
        with pytest.raises(AgentRunnerError, match="余额不足"):
            await ScreenNode().execute(ctx, None)

    agent.assert_not_called()
    await gen.aclose()


@pytest.mark.asyncio
async def test_t3_family_representative_and_propagation(factory, tmp_path):
    """同根因族只审代表；成员判决传播并打折置信度。"""
    from app.contexts.agent.nodes.triage import TriageNode

    gen = _seed_env(
        factory,
        tmp_path,
        current=[
            {"rule": "r.a", "file_path": "module/db.py"},
            {"rule": "r.a", "file_path": "module/db2.py"},
            {"rule": "r.a", "file_path": "module/db3.py"},
        ],
    )
    ctx, session = await gen.__anext__()

    calls = []

    async def agent_side_effect(**kw):
        calls.append(1)
        return _agent_output("tp", 0.9)

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings(triage_fast_model_enabled=False)),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
    ):
        out = await TriageNode().execute(ctx, None)

    assert len(calls) == 1  # 只审族代表
    assert out["adjudicated_count"] == 1
    assert out["propagated_count"] == 2
    assert out["family_count"] == 1
    groups = await _groups_of(session, ctx.task_id)
    assert all(g.ai_verdict == "tp" for g in groups)
    propagated = [g for g in groups if g.verdict_source == "propagated"]
    assert len(propagated) == 2
    assert all(abs(g.ai_confidence - 0.9 * 0.85) < 1e-6 for g in propagated)
    assert all(g.family_key for g in groups)
    await gen.aclose()


@pytest.mark.asyncio
async def test_t3_low_confidence_rep_goes_review(factory, tmp_path):
    """代表置信度不足：成员转 needs_review，宁人工不错传。"""
    from app.contexts.agent.nodes.triage import TriageNode

    gen = _seed_env(
        factory,
        tmp_path,
        current=[
            {"rule": "r.a", "file_path": "module/db.py"},
            {"rule": "r.a", "file_path": "module/db2.py"},
        ],
    )
    ctx, session = await gen.__anext__()

    async def agent_side_effect(**kw):
        return _agent_output("tp", 0.5)  # 低于 triage_propagate_min_confidence

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings(triage_fast_model_enabled=False)),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
    ):
        out = await TriageNode().execute(ctx, None)

    assert out["propagated_count"] == 0
    assert out["propagated_review_count"] == 1
    groups = await _groups_of(session, ctx.task_id)
    statuses = sorted(g.status for g in groups)
    assert statuses == ["adjudicated", "needs_review"]
    await gen.aclose()


@pytest.mark.asyncio
async def test_cascade_cancel_penetrates_between_tiers(factory, tmp_path):
    from app.contexts.agent.nodes.screen import ScreenNode
    from app.contexts.task.models import Task

    gen = _seed_env(
        factory,
        tmp_path,
        current=[{"rule": "r.a", "file_path": "module/db.py"}],
    )
    ctx, session = await gen.__anext__()

    async def carryover_then_cancel(*args, **kwargs):
        task = await session.get(Task, ctx.task_id)
        task.status = "cancelled"
        await session.commit()
        return [], 1

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings()),
        patch(
            "app.contexts.agent.nodes.triage.cascade.apply_carryover",
            new=carryover_then_cancel,
        ),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new_callable=AsyncMock) as agent,
    ):
        out = await ScreenNode().execute(ctx, None)

    assert out["status"] == "cancelled"
    agent.assert_not_called()
    await gen.aclose()


@pytest.mark.asyncio
async def test_feedback_resolution_truth_weights_rule_prior(factory, tmp_path):
    """验证真值回流规则先验：7 条已验证 fp(×3 权重=21 样本)即越过
    min_samples=20——真值比 agent 亲审更快把噪声规则顶过阈值。"""
    from app.contexts.agent.nodes.screen import ScreenNode

    verified_fp = [
        {
            "rule": "r.noisy",
            "file_path": f"module/f{i}.py",
            "verdict": "tp",
            "conf": 0.9,
            "resolution": "false_positive",
        }
        for i in range(7)
    ]
    gen = _seed_env(
        factory,
        tmp_path,
        prior_groups=verified_fp,
        current=[{"rule": "r.noisy", "file_path": "module/new1.py"}],
    )
    ctx, session = await gen.__anext__()

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings(triage_fast_model_enabled=False)),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new_callable=AsyncMock) as agent,
    ):
        out = await ScreenNode().execute(ctx, None)

    # agent 当时全判 tp，但验证真值全部翻案 → 真值优先，规则判 fp
    assert out["rule_count"] == 1
    groups = await _groups_of(session, ctx.task_id)
    assert groups == []  # 规则误报已丢弃
    agent.assert_not_called()
    await gen.aclose()


@pytest.mark.asyncio
async def test_feedback_calibrates_propagation_discount(factory, tmp_path):
    """验证一致率自校准传播折扣：历史 agent-tp 全被验证翻案 → 一致率 0 →
    折扣夹到 0.3；传播置信度 = 代表置信 × 0.3。"""
    from app.contexts.agent.nodes.triage import TriageNode

    disagreeing = [
        {
            "rule": f"r.d{i}",
            "file_path": f"module/d{i}.py",
            "verdict": "tp",
            "conf": 0.9,
            "resolution": "false_positive",
        }
        for i in range(10)
    ]
    gen = _seed_env(
        factory,
        tmp_path,
        prior_groups=disagreeing,
        current=[
            {"rule": "r.a", "file_path": "module/db.py"},
            {"rule": "r.a", "file_path": "module/db2.py"},
        ],
    )
    ctx, session = await gen.__anext__()

    async def agent_side_effect(**kw):
        return _agent_output("tp", 0.9)

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings(triage_fast_model_enabled=False)),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
    ):
        out = await TriageNode().execute(ctx, None)

    assert out["propagated_count"] == 1
    groups = await _groups_of(session, ctx.task_id)
    propagated = [g for g in groups if g.verdict_source == "propagated"][0]
    assert abs(propagated.ai_confidence - 0.9 * 0.3) < 1e-6
    await gen.aclose()


@pytest.mark.asyncio
async def test_feedback_low_sample_keeps_default_discount(factory, tmp_path):
    """验证样本不足时不校准：默认折扣 0.85 生效。"""
    from app.contexts.agent.nodes.triage import TriageNode

    gen = _seed_env(
        factory,
        tmp_path,
        prior_groups=[
            {
                "rule": "r.d0",
                "file_path": "module/d0.py",
                "verdict": "tp",
                "conf": 0.9,
                "resolution": "false_positive",
            }
        ],
        current=[
            {"rule": "r.a", "file_path": "module/db.py"},
            {"rule": "r.a", "file_path": "module/db2.py"},
        ],
    )
    ctx, session = await gen.__anext__()

    async def agent_side_effect(**kw):
        return _agent_output("tp", 0.9)

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings(triage_fast_model_enabled=False)),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
    ):
        await TriageNode().execute(ctx, None)

    groups = await _groups_of(session, ctx.task_id)
    propagated = [g for g in groups if g.verdict_source == "propagated"][0]
    assert abs(propagated.ai_confidence - 0.9 * 0.85) < 1e-6
    await gen.aclose()


@pytest.mark.asyncio
async def test_concurrent_triage_progress_uses_done_not_started(factory, tmp_path):
    """并发代表审议：进度文案用完成后的 done/total，开始只发「开始审议」流水。"""
    from app.contexts.agent.nodes.triage import TriageNode

    gen = _seed_env(
        factory,
        tmp_path,
        current=[
            {"rule": "r.a", "file_path": "module/a.py", "cwe": "CWE-89"},
            {"rule": "r.b", "file_path": "module/b.py", "cwe": "CWE-79"},
            {"rule": "r.c", "file_path": "module/c.py", "cwe": "CWE-22"},
        ],
    )
    ctx, session = await gen.__anext__()
    ctx.session_factory = factory
    events: list[dict] = []
    ctx.on_event = events.append

    async def agent_side_effect(**kw):
        return _agent_output("tp", 0.9)

    with (
        patch(
            "app.core.config.get_settings",
            return_value=_cascade_settings(triage_fast_model_enabled=False),
        ),
        patch(
            "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
            new=agent_side_effect,
        ),
    ):
        await TriageNode().execute(ctx, None)

    phases = [
        e.get("message", "")
        for e in events
        if e.get("type") == "phase.updated"
    ]
    starts = [m for m in phases if str(m).startswith("开始审议：")]
    dones = [m for m in phases if str(m).startswith("二审 ")]
    assert starts, "并发开始应留下事件流痕迹"
    assert dones, "完成后应更新二审 N/M 进度"
    assert not any("二审 " in str(m) and "开始" in str(m) for m in starts)
    progress = [e for e in events if e.get("type") == "triage.progress"]
    assert progress
    assert all(e.get("node_key") == "triage" for e in progress)
    assert all("message" in e and e["message"].startswith("二审 ") for e in progress)
    last = progress[-1]
    assert last["done"] == last["total"]
    await gen.aclose()


@pytest.mark.asyncio
async def test_streaming_dispatch_during_triage(factory, tmp_path):
    """流式派单：族代表判完即建 LeadRun 入队并启动后台排空，
    不等 triage 全部跑完；传播成员置信度打折后不达门槛不入队。"""
    from app.contexts.agent.nodes.triage import TriageNode
    from app.contexts.finding.models import LeadRun

    gen = _seed_env(
        factory,
        tmp_path,
        current=[
            {"rule": "r.a", "file_path": "module/db.py", "grade": "A"},
            {"rule": "r.a", "file_path": "module/db2.py", "grade": "A"},
        ],
    )
    ctx, session = await gen.__anext__()
    ctx.previous_outputs = {"profile": {"is_web": True}}
    ctx.session_factory = factory

    async def agent_side_effect(**kw):
        return _agent_output("tp", 0.9)

    drain_calls: list[dict] = []

    async def fake_drain(**kw):
        drain_calls.append(kw)

    async def fake_enqueue(task_id, items):
        return len(items)

    async def fake_runtime(self):
        return SimpleNamespace(lead_verify_per_task=2)

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings(
            triage_fast_model_enabled=False, triage_stream_dispatch_enabled=True,
        )),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
        patch("app.contexts.agent.lead_worker.drain_lead_queue", new=fake_drain),
        patch("app.contexts.agent.lead_queue.enqueue_leads", new=fake_enqueue),
        patch(
            "app.contexts.settings.service.SettingsService.get_runtime_settings",
            new=fake_runtime,
        ),
    ):
        out = await TriageNode().execute(ctx, None)

    assert out["adjudicated_count"] == 1
    # 代表置信 0.9 ≥ 0.8 达门槛入队；传播成员 0.9×0.85=0.765 不达门槛
    leads = (await session.execute(select(LeadRun).where(LeadRun.task_id == ctx.task_id))).scalars().all()
    assert len(leads) == 1
    assert drain_calls, "后台排空应已启动"
    assert drain_calls[0]["task_id"] == ctx.task_id
    groups = await _groups_of(session, ctx.task_id)
    dispatched = [g for g in groups if g.status == "dispatched"]
    assert len(dispatched) == 1
    await gen.aclose()


@pytest.mark.asyncio
async def test_t0_rejects_non_agent_provenance(factory, tmp_path):
    """防自举回归：rule/fast/propagated 来源的历史判决不得被携带——
    否则前置层输出入库即成"永久真值"，跨任务复利。"""
    from app.contexts.agent.nodes.screen import ScreenNode
    from app.contexts.agent.nodes.triage import TriageNode

    gen = _seed_env(
        factory,
        tmp_path,
        prior_groups=[
            {
                "rule": "r.a",
                "file_path": "module/db.py",
                "verdict": "fp",
                "conf": 0.99,
                "source": "rule",
                "fingerprint": "FP-RULE",
            }
        ],
        current=[{"rule": "r.a", "file_path": "module/db.py", "fingerprint": "FP-RULE"}],
    )
    ctx, session = await gen.__anext__()

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings(triage_fast_model_enabled=False)),
        patch(
            "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
            new_callable=AsyncMock,
            return_value=_agent_output("tp", 0.9),
        ) as agent,
    ):
        screen_out = await ScreenNode().execute(ctx, None)
        ctx.previous_outputs = {"screen": screen_out}
        out = await TriageNode().execute(ctx, None)

    assert screen_out["carried_count"] == 0
    assert agent.await_count >= 1  # 落回 agent 亲审
    await gen.aclose()


@pytest.mark.asyncio
async def test_enqueue_leads_dedupes_by_lead_run_id():
    """同一 lead 重复入队只保留一份（重投双跑防双消费）。"""
    from app.contexts.agent import lead_queue as lq

    class _MemRedis:
        def __init__(self):
            self.lists: dict[str, list] = {}
            self.sets: dict[str, set] = {}

        async def smembers(self, key):
            return set(self.sets.get(key) or set())

        async def sadd(self, key, *members):
            self.sets.setdefault(key, set()).update(members)

        async def lpush(self, key, *payloads):
            self.lists.setdefault(key, []).extend(payloads)

    mem = _MemRedis()
    lq.set_redis_client(mem)
    try:
        items = [{"lead_run_id": "lr1", "group_id": "g1", "run_id": "r1"}]
        first = await lq.enqueue_leads("t1", items)
        again = await lq.enqueue_leads(
            "t1",
            items
            + [
                {"lead_run_id": "lr2", "group_id": "g2", "run_id": "r1"},
            ],
        )
        assert (first, again) == (1, 1)  # 第二次只有 lr2 是新的
        queued = [json.loads(p)["lead_run_id"] for p in mem.lists["crucible:lead_verify:t1"]]
        assert sorted(queued) == ["lr1", "lr2"]  # lr1 只有一份
    finally:
        lq.set_redis_client(None)


def test_runtime_update_accepts_token_budget():
    """并发硬顶校验不得误杀 token 预算（量纲不同）。"""
    from app.contexts.settings.schemas import RuntimeSettingsUpdateRequest

    ok = RuntimeSettingsUpdateRequest(task_token_budget=500_000_000)
    assert ok.task_token_budget == 500_000_000
    import pytest as _pytest

    with _pytest.raises(ValueError):
        RuntimeSettingsUpdateRequest(task_token_budget=3_000_000_000)


@pytest.mark.asyncio
async def test_t2_empty_slices_escalate_without_fast_call(factory, tmp_path):
    """切片为空（文件读不到）不送快审：零上下文的单次补全等于盲猜，直接升级 agent。"""
    from app.contexts.agent.nodes.screen import ScreenNode
    from app.contexts.agent.nodes.triage import TriageNode

    gen = _seed_env(
        factory,
        tmp_path,
        current=[
            {"rule": "r.a", "file_path": "module/db.py"},
            {"rule": "r.c", "file_path": "module/missing.py"},
        ],
    )
    ctx, session = await gen.__anext__()

    fast_users: list[str] = []

    async def fast_side_effect(*, role, system, user, **kw):
        fast_users.append(user)
        return SimpleNamespace(
            text=json.dumps({"verdict": "fp", "confidence": 0.9, "why": [], "need": []}),
            model="fast-1",
            provider_id="pv",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def agent_side_effect(**kw):
        return _agent_output("fp", 0.85)

    async def fake_provider(session, role):
        return SimpleNamespace(
            id="pv",
            model="fast-1",
            base_url="http://llm.test",
            api_key_encrypted="",
            timeout_ms=None,
        )

    with (
        patch("app.core.config.get_settings", return_value=_cascade_settings()),
        patch("app.core.llm_gateway._resolve_provider", new=fake_provider),
        patch("app.core.llm_gateway.llm_complete", new=fast_side_effect),
        patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new=agent_side_effect),
    ):
        screen_out = await ScreenNode().execute(ctx, None)
        ctx.previous_outputs = {"screen": screen_out}
        out = await TriageNode().execute(ctx, None)

    assert len(fast_users) == 1  # 只有能切出代码的组进了快审
    groups = {g.file_path: g for g in await _groups_of(session, ctx.task_id)}
    assert "module/db.py" not in groups  # 快审误报已丢弃
    assert "module/missing.py" not in groups  # agent 误报已丢弃
    assert screen_out["fast_model_count"] == 1
    assert out["adjudicated_count"] == 1
    await gen.aclose()
