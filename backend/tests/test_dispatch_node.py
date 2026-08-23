"""WP5 · dispatch 节点 + 判决回流测试(discovery-spec §6.4 / §4.4 / WP5 DoD)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import MagicMock, patch

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
    base = dict(triage_high_confidence=0.8, triage_medium_confidence=0.5)
    base.update(kw)
    return MagicMock(**base)


async def _seed(session, tmp_path, groups_spec, *, is_web=True):
    """groups_spec: [(cwe, grade, verdict, confidence, priority)]。"""
    import hashlib

    from app.contexts.agent.contracts import DispatchInput, SourceHandoff, TriageHandoff
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import Adjudication, AlertGroup, RawFinding
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.agent.contracts.outputs import ProfileHandoff

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
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

    for i, (cwe, grade, verdict, conf, priority) in enumerate(groups_spec):
        fp = hashlib.sha256(f"wp5-{i}".encode()).hexdigest()
        f = RawFinding(
            task_id=task.id, scan_run_id=sr.id, engine="semgrep", rule_id="python.sqli",
            cwe=cwe, severity="error", file_path=f"app/mod{i}.py", line_start=2,
            line_end=2, message="secret-rule-msg", source_to_sink=["a.py:1 (x)"],
            code_snippet="2\tq = 'SELECT ' + user", fingerprint=fp, raw={},
        )
        session.add(f)
        await session.flush()
        g = AlertGroup(
            task_id=task.id, group_key=f"gk-{i:03d}", cwe=cwe, file_path=f"app/mod{i}.py",
            function_symbol="handler", line_span="1-3", member_count=1,
            representative_finding_id=f.id, engine_set=["semgrep"],
            status="adjudicated", clue_grade=grade, ai_verdict=verdict,
            ai_confidence=conf, priority=priority,
        )
        session.add(g)
        await session.flush()
        if verdict in ("tp", "need_more_context"):
            session.add(Adjudication(
                alert_group_id=g.id, attempt=1, verdict=verdict, confidence=conf,
                why=["拼接注入"], evidence=[{"file": f"app/mod{i}.py", "lines": "2-2"}],
                need=[], context_log=[], prompt_text="p", response_text="r", usage={},
            ))
    await session.flush()

    ctx = NodeContext(
        task_id=task.id, run_id=run.id, host_workdir=str(tmp_path),
        source_path=str(repo), vulnerability_description="",
        project_address="x", project_ref=None, db_session=session,
        node_run_id="nr-dispatch",
    )
    inp = DispatchInput(
        source=SourceHandoff(project_path=str(repo)),
        host_workdir=str(tmp_path), source_path=str(repo),
        triage=TriageHandoff(), profile=ProfileHandoff(is_web=is_web),
    )
    return ctx, task, inp


@pytest.mark.asyncio
async def test_dispatch_enqueues_all_a_grade_leads(session_factory, tmp_path):
    """多条合格 A 级 → 全部入队；代表组自指；不再落选进复核。"""
    from app.contexts.agent.lead_queue import queue_depth, set_redis_client
    from app.contexts.agent.nodes.dispatch import DispatchNode
    from app.contexts.finding.models import AlertGroup, LeadRun

    class _MemRedis:
        def __init__(self):
            self.lists: dict[str, list] = {}
            self.sets: dict[str, set] = {}

        async def lpush(self, key, *values):
            self.lists.setdefault(key, [])
            for v in reversed(values):
                self.lists[key].insert(0, v)

        async def rpop(self, key):
            lst = self.lists.get(key) or []
            return lst.pop() if lst else None

        async def sadd(self, key, *members):
            self.sets.setdefault(key, set()).update(members)

        async def srem(self, key, *members):
            s = self.sets.get(key) or set()
            for m in members:
                s.discard(m)

        async def llen(self, key):
            return len(self.lists.get(key) or [])
        async def lrange(self, key, start, end):
            lst = self.lists.get(key) or []
            if end == -1:
                return lst[start:] if start else list(lst)
            return lst[start:end + 1]

        async def smembers(self, key):
            return set(self.sets.get(key) or set())


        async def scard(self, key):
            return len(self.sets.get(key) or set())

        async def delete(self, *keys):
            for k in keys:
                self.lists.pop(k, None)
                self.sets.pop(k, None)

    set_redis_client(_MemRedis())
    try:
        async with session_factory() as session:
            ctx, task, inp = await _seed(session, tmp_path, [
                ("CWE-89", "A", "tp", 0.9, "high"),
                ("CWE-79", "A", "tp", 0.95, "high"),
            ])
            with patch("app.core.config.get_settings", return_value=_settings()):
                out = await DispatchNode().execute(ctx, inp)
            assert out["has_lead"] is True
            assert out["queued_count"] == 2
            assert len(out["queued_group_ids"]) == 2
            groups = (await session.execute(
                select(AlertGroup).where(AlertGroup.task_id == task.id)
            )).scalars().all()
            assert all(g.status == "dispatched" for g in groups)
            leads = (await session.execute(
                select(LeadRun).where(LeadRun.task_id == task.id)
            )).scalars().all()
            assert len(leads) == 2
            await session.refresh(task)
            assert task.source_alert_group_id == out["lead_group_id"]
            assert await queue_depth(task.id) == 2
            desc = out["lead_description"]
            assert "python.sqli" not in desc and "secret-rule-msg" not in desc
    finally:
        set_redis_client(None)


@pytest.mark.asyncio
async def test_dispatch_emits_phase_events(session_factory, tmp_path):
    """调度必须写过程事件：入队 / 无合格主线索。"""
    from app.contexts.agent.lead_queue import set_redis_client
    from app.contexts.agent.nodes.dispatch import DispatchNode

    class _MemRedis:
        def __init__(self):
            self.lists: dict[str, list] = {}
            self.sets: dict[str, set] = {}

        async def lpush(self, key, *values):
            self.lists.setdefault(key, [])
            for v in reversed(values):
                self.lists[key].insert(0, v)

        async def rpop(self, key):
            lst = self.lists.get(key) or []
            return lst.pop() if lst else None

        async def sadd(self, key, *members):
            self.sets.setdefault(key, set()).update(members)

        async def srem(self, key, *members):
            pass

        async def llen(self, key):
            return len(self.lists.get(key) or [])
        async def lrange(self, key, start, end):
            lst = self.lists.get(key) or []
            if end == -1:
                return lst[start:] if start else list(lst)
            return lst[start:end + 1]

        async def smembers(self, key):
            return set(self.sets.get(key) or set())


        async def scard(self, key):
            return 0

        async def delete(self, *keys):
            pass

    set_redis_client(_MemRedis())
    try:
        async with session_factory() as session:
            ctx, task, inp = await _seed(session, tmp_path, [
                ("CWE-89", "A", "tp", 0.9, "high"),
            ])
            events: list[dict] = []
            ctx.on_event = events.append
            with patch("app.core.config.get_settings", return_value=_settings()):
                out = await DispatchNode().execute(ctx, inp)
            assert out["has_lead"] is True
            assert any(e.get("type") == "phase.updated" and "入队" in str(e.get("message")) for e in events)
            assert events[0]["phase"] == "dispatch"

        async with session_factory() as session:
            ctx, task, inp = await _seed(session, tmp_path, [
                ("CWE-89", "B", "fp", 0.9, "high"),
            ])
            events = []
            ctx.on_event = events.append
            with patch("app.core.config.get_settings", return_value=_settings()):
                out = await DispatchNode().execute(ctx, inp)
            assert out["has_lead"] is False
            assert any("无合格主线索" in str(e.get("message")) for e in events)
    finally:
        set_redis_client(None)


@pytest.mark.asyncio
async def test_dispatch_b_grade_never_auto_dispatched(session_factory, tmp_path):
    from app.contexts.agent.nodes.dispatch import DispatchNode
    from app.contexts.finding.models import AlertGroup

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [
            ("CWE-89", "B", "tp", 0.95, "high"),   # 高置信但无数据流
            ("CWE-79", "A", "tp", 0.6, "high"),    # 中置信
        ])
        with patch("app.core.config.get_settings", return_value=_settings()):
            out = await DispatchNode().execute(ctx, inp)
        assert out["has_lead"] is False  # B 级禁止自动终认；中置信也不当线索
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        assert all(g.status == "needs_review" for g in groups)


@pytest.mark.asyncio
async def test_dispatch_non_web_never_picks_lead(session_factory, tmp_path):
    from app.contexts.agent.nodes.dispatch import DispatchNode

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [
            ("CWE-89", "A", "tp", 0.95, "high"),
        ], is_web=False)
        with patch("app.core.config.get_settings", return_value=_settings()):
            out = await DispatchNode().execute(ctx, inp)
        assert out["has_lead"] is False
        await session.refresh(task)
        assert task.source_alert_group_id is None


@pytest.mark.asyncio
async def test_dispatch_no_candidates_still_completes(session_factory, tmp_path):
    """无合格组(fp/未审/bypass) → has_lead=False、节点 completed、不 fail。"""
    from app.contexts.agent.nodes.dispatch import DispatchNode

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [
            ("CWE-89", "B", "fp", 0.9, "high"),
            ("CWE-89", "B", None, None, "medium"),
        ])
        with patch("app.core.config.get_settings", return_value=_settings()):
            out = await DispatchNode().execute(ctx, inp)
        assert out["has_lead"] is False
        assert out["archived_count"] == 1
        assert out["skipped_unaudited_count"] == 1


# ---------- 判决回流 ----------

@pytest.mark.asyncio
async def test_reconcile_six_verdict_mapping(session_factory, tmp_path):
    """六档映射：confirmed/partial→resolved(confirmed)；fp→resolved(fp)；
    code_reachable/code_smell/not_reproduced→退回 needs_review。"""
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.service import FindingService

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [("CWE-89", "A", "tp", 0.9, "high")])
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        group = groups[0]
        svc = FindingService(session)

        task.source_alert_group_id = group.id
        group.status = "dispatched"

        for verdict, expect in [
            ("confirmed", ("resolved", "confirmed")),
            ("partial", ("resolved", "confirmed")),
            ("false_positive", ("resolved", "false_positive")),
        ]:
            group.status = "dispatched"; group.resolution = None
            task.status, task.verdict = "completed", verdict
            await svc.reconcile_from_task(task)
            assert (group.status, group.resolution) == expect, verdict

        for verdict in ("code_reachable", "code_smell", "not_reproduced"):
            group.status = "dispatched"
            task.status, task.verdict = "completed", verdict
            await svc.reconcile_from_task(task)
            assert group.status == "needs_review", verdict

        # 任务 needs_review 状态 → 组退回复核
        group.status = "dispatched"
        task.status, task.verdict = "needs_review", None
        await svc.reconcile_from_task(task)
        assert group.status == "needs_review"

        # 幂等：重复回写 no-op
        task.status, task.verdict = "completed", "confirmed"
        group.status, group.resolution = "resolved", "confirmed"
        await svc.reconcile_from_task(task)
        assert (group.status, group.resolution) == ("resolved", "confirmed")


@pytest.mark.asyncio
async def test_reconcile_stale_groups_sweeper(session_factory, tmp_path):
    """丢事件兜底：dispatched 组按 Task 最新 verdict 补写。"""
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.service import FindingService

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [("CWE-89", "A", "tp", 0.9, "high")])
        group = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().one()
        task.source_alert_group_id = group.id
        task.status, task.verdict = "completed", "confirmed"
        group.status = "dispatched"
        await session.flush()

        fixed = await FindingService(session).reconcile_stale_groups()
        assert fixed == 1
        assert group.status == "resolved" and group.resolution == "confirmed"
        # 再跑一次：幂等
        assert await FindingService(session).reconcile_stale_groups() == 0


@pytest.mark.asyncio
async def test_dispatch_rerun_is_idempotent(session_factory, tmp_path):
    """Celery 重投场景：dispatch 二次执行不炸唯一约束、不重复入队。"""
    from app.contexts.agent.lead_queue import queue_depth, set_redis_client
    from app.contexts.agent.nodes.dispatch import DispatchNode
    from app.contexts.finding.models import LeadRun

    class _MemRedis:
        def __init__(self):
            self.lists: dict[str, list] = {}
            self.sets: dict[str, set] = {}

        async def lpush(self, key, *values):
            self.lists.setdefault(key, [])
            for v in reversed(values):
                self.lists[key].insert(0, v)

        async def rpop(self, key):
            lst = self.lists.get(key) or []
            return lst.pop() if lst else None

        async def sadd(self, key, *members):
            self.sets.setdefault(key, set()).update(members)

        async def srem(self, key, *members):
            s = self.sets.get(key) or set()
            for m in members:
                s.discard(m)

        async def llen(self, key):
            return len(self.lists.get(key) or [])
        async def lrange(self, key, start, end):
            lst = self.lists.get(key) or []
            if end == -1:
                return lst[start:] if start else list(lst)
            return lst[start:end + 1]

        async def smembers(self, key):
            return set(self.sets.get(key) or set())

        async def scard(self, key):
            return len(self.sets.get(key) or set())

        async def delete(self, *keys):
            for k in keys:
                self.lists.pop(k, None)
                self.sets.pop(k, None)

    set_redis_client(_MemRedis())
    try:
        async with session_factory() as session:
            ctx, task, inp = await _seed(session, tmp_path, [
                ("CWE-89", "A", "tp", 0.9, "high"),
                ("CWE-79", "A", "tp", 0.95, "high"),
            ])
            with patch("app.core.config.get_settings", return_value=_settings()):
                out1 = await DispatchNode().execute(ctx, inp)
                out2 = await DispatchNode().execute(ctx, inp)  # 重投：不得抛 IntegrityError
            assert out1["queued_count"] == 2
            assert out2["queued_count"] == 2  # 全部由补偿路径复用，不新增
            leads = (await session.execute(
                select(LeadRun).where(LeadRun.task_id == task.id)
            )).scalars().all()
            assert len(leads) == 2  # 无重复 LeadRun
            assert await queue_depth(task.id) == 2  # 队列不重复
    finally:
        set_redis_client(None)


@pytest.mark.asyncio
async def test_dispatch_recovers_lost_leads_after_crash(session_factory, tmp_path):
    """commit 后、入队前崩溃：queued LeadRun 不在 Redis → 重投时绑回新 run 并补入队。"""
    from app.contexts.agent.lead_queue import queue_depth, set_redis_client
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.agent.nodes.dispatch import DispatchNode
    from app.contexts.finding.models import LeadRun
    from app.contexts.task.models import TaskRun

    class _MemRedis:
        def __init__(self):
            self.lists: dict[str, list] = {}
            self.sets: dict[str, set] = {}

        async def lpush(self, key, *values):
            self.lists.setdefault(key, [])
            for v in reversed(values):
                self.lists[key].insert(0, v)

        async def rpop(self, key):
            lst = self.lists.get(key) or []
            return lst.pop() if lst else None

        async def sadd(self, key, *members):
            self.sets.setdefault(key, set()).update(members)

        async def srem(self, key, *members):
            s = self.sets.get(key) or set()
            for m in members:
                s.discard(m)

        async def llen(self, key):
            return len(self.lists.get(key) or [])
        async def lrange(self, key, start, end):
            lst = self.lists.get(key) or []
            if end == -1:
                return lst[start:] if start else list(lst)
            return lst[start:end + 1]

        async def smembers(self, key):
            return set(self.sets.get(key) or set())

        async def scard(self, key):
            return len(self.sets.get(key) or set())

        async def delete(self, *keys):
            for k in keys:
                self.lists.pop(k, None)
                self.sets.pop(k, None)

    mem = _MemRedis()
    set_redis_client(mem)
    try:
        async with session_factory() as session:
            ctx, task, inp = await _seed(session, tmp_path, [
                ("CWE-89", "A", "tp", 0.9, "high"),
            ])
            with patch("app.core.config.get_settings", return_value=_settings()):
                await DispatchNode().execute(ctx, inp)
            # 模拟崩溃窗口：DB 已 commit（LeadRun=queued、组 dispatched）但 Redis 队列丢失
            await mem.delete(*(k for k in list(mem.lists) + list(mem.sets) if task.id in k))
            # 任务重投：新 TaskRun
            run2 = TaskRun(task_id=task.id, status="running")
            session.add(run2)
            await session.flush()
            ctx2 = NodeContext(
                task_id=task.id, run_id=run2.id, host_workdir=ctx.host_workdir,
                source_path=ctx.source_path, vulnerability_description="",
                project_address="x", project_ref=None, db_session=session,
                node_run_id="nr-dispatch-2",
            )
            with patch("app.core.config.get_settings", return_value=_settings()):
                out = await DispatchNode().execute(ctx2, inp)
            assert out["has_lead"] is True  # 不静默丢线索
            assert out["queued_count"] == 1
            assert await queue_depth(task.id) == 1
            leads = (await session.execute(
                select(LeadRun).where(LeadRun.task_id == task.id)
            )).scalars().all()
            assert all(lr.run_id == run2.id for lr in leads)  # 绑回当前 run
    finally:
        set_redis_client(None)
