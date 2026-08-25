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

    for i, spec in enumerate(groups_spec):
        if isinstance(spec, dict):
            cwe = spec.get("cwe", "CWE-89")
            grade = spec.get("grade", "B")
            verdict = spec.get("verdict", "tp")
            conf = spec.get("conf", 0.9)
            priority = spec.get("priority", "high")
            source = spec.get("source", "agent" if verdict == "tp" else None)
            file_path = spec.get("file_path", f"app/mod{i}.py")
            engines = spec.get("engine_set", ["semgrep"])
            raw = spec.get("raw") or {}
            qualify = spec.get("qualify")
            if qualify is None and verdict == "tp" and source == "agent":
                qualify = {
                    "attacker_controlled": True,
                    "reaches_sink": True,
                    "sanitizer": "none",
                }
        else:
            cwe, grade, verdict, conf, priority = spec
            source = "agent" if verdict == "tp" else None
            file_path = f"app/mod{i}.py"
            engines = ["semgrep"]
            raw = {}
            qualify = {
                "attacker_controlled": True,
                "reaches_sink": True,
                "sanitizer": "none",
            } if verdict == "tp" else None
        fp = hashlib.sha256(f"wp5-{i}".encode()).hexdigest()
        f = RawFinding(
            task_id=task.id, scan_run_id=sr.id, engine=engines[0], rule_id="python.sqli",
            cwe=cwe, severity="error", file_path=file_path, line_start=2,
            line_end=2, message="secret-rule-msg", source_to_sink=["a.py:1 (x)"],
            code_snippet="2\tq = 'SELECT ' + user", fingerprint=fp, raw=raw,
        )
        session.add(f)
        await session.flush()
        g = AlertGroup(
            task_id=task.id, group_key=f"gk-{i:03d}", cwe=cwe, file_path=file_path,
            function_symbol="handler", line_span="1-3", member_count=1,
            representative_finding_id=f.id, engine_set=engines,
            status="adjudicated", clue_grade=grade, ai_verdict=verdict,
            ai_confidence=conf, priority=priority, verdict_source=source,
        )
        session.add(g)
        await session.flush()
        if verdict in ("tp", "need_more_context"):
            log = [{"qualify": qualify}] if qualify else []
            session.add(Adjudication(
                alert_group_id=g.id, attempt=1, verdict=verdict, confidence=conf,
                why=["拼接注入"], evidence=[{"file": file_path, "lines": "2-2"}],
                need=[], context_log=log, prompt_text="p", response_text="r", usage={},
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
            assert any("无合格线索" in str(e.get("message")) for e in events)
    finally:
        set_redis_client(None)


@pytest.mark.asyncio
async def test_dispatch_b_grade_t3_can_enqueue(session_factory, tmp_path):
    """B 级只要 T3 合格即可入队，不再要求 A 级。"""
    from app.contexts.agent.lead_queue import queue_depth, set_redis_client
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
            return lst[start:] if end == -1 else lst[start:end + 1]

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
                ("CWE-89", "B", "tp", 0.95, "high"),
            ])
            with patch("app.core.config.get_settings", return_value=_settings()):
                out = await DispatchNode().execute(ctx, inp)
            assert out["has_lead"] is True
            assert out["queued_count"] == 1
            assert await queue_depth(task.id) == 1
    finally:
        set_redis_client(None)


@pytest.mark.asyncio
async def test_dispatch_rejects_fast_model_and_propagated_tp(session_factory, tmp_path):
    from app.contexts.agent.nodes.dispatch import DispatchNode

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [
            {"cwe": "CWE-79", "grade": "B", "verdict": "tp", "conf": 0.99,
             "priority": "high", "source": "fast_model"},
            {"cwe": "CWE-79", "grade": "B", "verdict": "tp", "conf": 0.9,
             "priority": "high", "source": "propagated"},
        ])
        with patch("app.core.config.get_settings", return_value=_settings()):
            out = await DispatchNode().execute(ctx, inp)
        assert out["has_lead"] is False
        assert out["queued_count"] == 0


@pytest.mark.asyncio
async def test_dispatch_zentaopms_fast_model_tp_not_all_enqueued(session_factory, tmp_path):
    """禅道口径：不得把快审 tp 整包送终认。"""
    from app.contexts.agent.nodes.dispatch import DispatchNode

    specs = [
        {"cwe": "CWE-79", "grade": "B", "verdict": "tp", "conf": 0.95,
         "priority": "medium", "source": "fast_model", "file_path": f"www/js/jquery-{i}.js"}
        for i in range(20)
    ]
    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, specs)
        with patch("app.core.config.get_settings", return_value=_settings()):
            out = await DispatchNode().execute(ctx, inp)
        assert out["has_lead"] is False
        assert out["queued_count"] == 0


@pytest.mark.asyncio
async def test_dispatch_non_web_still_enqueues_qualified(session_factory, tmp_path):
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

        async def sadd(self, key, *members):
            self.sets.setdefault(key, set()).update(members)

        async def llen(self, key):
            return len(self.lists.get(key) or [])

        async def lrange(self, key, start, end):
            return list(self.lists.get(key) or [])

        async def smembers(self, key):
            return set(self.sets.get(key) or set())

        async def scard(self, key):
            return len(self.sets.get(key) or set())

        async def delete(self, *keys):
            pass

        async def rpop(self, key):
            return None

        async def srem(self, key, *members):
            pass

    set_redis_client(_MemRedis())
    try:
        async with session_factory() as session:
            ctx, task, inp = await _seed(session, tmp_path, [
                ("CWE-89", "A", "tp", 0.95, "high"),
            ], is_web=False)
            with patch("app.core.config.get_settings", return_value=_settings()):
                out = await DispatchNode().execute(ctx, inp)
            assert out["has_lead"] is True
    finally:
        set_redis_client(None)


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
    """confirmed/partial→resolved(confirmed)；code_reachable 可见；fp 丢组。"""
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.service import FindingService

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [("CWE-89", "A", "tp", 0.9, "high")])
        task.task_type = "verify"
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
        ]:
            group.status = "dispatched"; group.resolution = None
            task.status, task.verdict = "completed", verdict
            await svc.reconcile_from_task(task)
            assert (group.status, group.resolution) == expect, verdict

        group.status = "dispatched"
        group.resolution = None
        task.status, task.verdict = "completed", "code_reachable"
        await svc.reconcile_from_task(task)
        assert (group.status, group.resolution) == ("resolved", "code_reachable")

        for verdict in ("code_smell", "not_reproduced"):
            group.status = "dispatched"
            group.resolution = None
            task.status, task.verdict = "completed", verdict
            await svc.reconcile_from_task(task)
            assert group.status == "needs_review", verdict

        group.status = "dispatched"
        task.status, task.verdict = "needs_review", None
        await svc.reconcile_from_task(task)
        assert group.status == "needs_review"

        task.status, task.verdict = "completed", "confirmed"
        group.status, group.resolution = "resolved", "confirmed"
        await svc.reconcile_from_task(task)
        assert (group.status, group.resolution) == ("resolved", "confirmed")

        group.status = "dispatched"
        group.resolution = None
        task.status, task.verdict = "completed", "false_positive"
        await svc.reconcile_from_task(task)
        gone = (await session.execute(
            select(AlertGroup).where(AlertGroup.id == group.id)
        )).scalar_one_or_none()
        assert gone is None


@pytest.mark.asyncio
async def test_reconcile_from_task_skips_discovery(session_factory, tmp_path):
    """discovery 聚合任务的 verdict 不得回写溯源组。"""
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.service import FindingService

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [("CWE-89", "A", "tp", 0.9, "high")])
        group = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().one()
        task.source_alert_group_id = group.id
        task.task_type = "discovery"
        group.status = "dispatched"
        task.status, task.verdict = "completed", "confirmed"
        out = await FindingService(session).reconcile_from_task(task)
        assert out is None
        assert group.status == "dispatched"


@pytest.mark.asyncio
async def test_reconcile_stale_groups_sweeper(session_factory, tmp_path):
    """丢事件兜底：dispatched 组按 Task 最新 verdict 补写。"""
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.service import FindingService

    async with session_factory() as session:
        ctx, task, inp = await _seed(session, tmp_path, [("CWE-89", "A", "tp", 0.9, "high")])
        task.task_type = "verify"
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


@pytest.mark.asyncio
async def test_reps_and_adjudications_picks_highest_attempt(session_factory):
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import Adjudication, AlertGroup, RawFinding
    from app.contexts.finding.service import FindingService
    from app.contexts.task.models import Task, TaskRun

    async with session_factory() as session:
        task = Task(
            project_address="x", task_type="discovery",
            vulnerability_description=None, owner_id="u1", status="running",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="running")
        session.add(run)
        await session.flush()
        scan_run = ScanRun(
            task_id=task.id, run_id=run.id, node_run_id="nr-s",
            engine="semgrep", status="completed", config_summary={},
        )
        session.add(scan_run)
        await session.flush()
        finding = RawFinding(
            task_id=task.id, scan_run_id=scan_run.id, engine="semgrep",
            rule_id="python.sqli", cwe="CWE-89", severity="error",
            file_path="app/db.py", line_start=1, line_end=2, message="sqli",
            fingerprint="a" * 64, raw={},
        )
        session.add(finding)
        await session.flush()
        group = AlertGroup(
            task_id=task.id, group_key="gk-1", cwe="CWE-89",
            file_path="app/db.py", line_span="1-2", member_count=1,
            representative_finding_id=finding.id, engine_set=["semgrep"],
            status="adjudicated", clue_grade="A", ai_verdict="tp",
        )
        session.add(group)
        await session.flush()
        session.add(Adjudication(
            alert_group_id=group.id, attempt=1, verdict="need_more_context",
            confidence=0.4, prompt_text="p", response_text="r",
        ))
        session.add(Adjudication(
            alert_group_id=group.id, attempt=2, verdict="tp",
            confidence=0.9, prompt_text="p2", response_text="r2",
        ))
        await session.flush()

        reps, adjs = await FindingService(session).reps_and_adjudications([group])
        assert reps[group.id].id == finding.id
        assert adjs[group.id].attempt == 2
        assert adjs[group.id].verdict == "tp"
