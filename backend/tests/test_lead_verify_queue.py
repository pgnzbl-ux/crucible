"""终认队列 + 聚合报告过滤(discovery-spec §4.4 / §6.4)。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.contexts.agent.lead_worker import build_discovery_report_from_leads
from app.contexts.finding.models import LeadRun


def test_aggregate_report_includes_code_reachable_in_body():
    class _L:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    out = build_discovery_report_from_leads([
        _L(lead_description="可达线索", verdict="code_reachable",
           audit_output={"kill_chain": "a→b"}, reproduce_output=None),
        _L(lead_description="确认线索", verdict="confirmed",
           audit_output={"kill_chain": "x"}, reproduce_output={"verdict": "confirmed"}),
    ])
    rd = out["report_data"]
    assert "可达线索" in rd["vulnerability"]
    assert "代码可达" in rd["vulnerability"]
    assert rd["audit_summary"]["code_reachable_count"] == 1
    assert rd["audit_summary"]["confirmed_count"] == 1
    assert "需人工关注" not in rd["reporting_decision"]


def test_aggregate_report_only_confirmed_partial():
    leads = [
        LeadRun(
            task_id="t", run_id="r", alert_group_id="g1",
            lead_description="线索A", status="completed", verdict="confirmed",
            audit_output={"kill_chain": "a→b"}, reproduce_output={"verdict": "confirmed"},
        ),
        LeadRun(
            task_id="t", run_id="r", alert_group_id="g2",
            lead_description="线索B", status="completed", verdict="false_positive",
            audit_output={"gate_verdict": "fail"},
        ),
        LeadRun(
            task_id="t", run_id="r", alert_group_id="g3",
            lead_description="线索C", status="completed", verdict="not_reproduced",
        ),
    ]
    # LeadRun 未经 session 时需手动赋 id 等 — 用简单 namespace
    class _L:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    leads = [
        _L(lead_description="A", verdict="confirmed",
           audit_output={"kill_chain": "x"}, reproduce_output={"verdict": "confirmed"}),
        _L(lead_description="B", verdict="false_positive",
           audit_output={}, reproduce_output=None),
        _L(lead_description="C", verdict="partial",
           audit_output={"kill_chain": "y"}, reproduce_output={"verdict": "partial"}),
        _L(lead_description="D", verdict="code_smell",
           audit_output={}, reproduce_output=None),
    ]
    out = build_discovery_report_from_leads(leads)
    assert out is not None
    assert out["final_verdict"] == "confirmed"
    rd = out["report_data"]
    assert "线索A" in rd["vulnerability"] or "A" in rd["vulnerability"]
    assert "线索C" in rd["vulnerability"] or "C" in rd["vulnerability"]
    assert "false_positive" not in rd["vulnerability"]
    assert "线索B" not in rd["vulnerability"]
    assert "线索D" not in rd["vulnerability"]


def test_aggregate_report_zero_confirmed_still_returns_audit_report():
    class _L:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    leads = [
        _L(lead_description="x", verdict="false_positive",
           audit_output={}, reproduce_output=None),
        _L(lead_description="y", verdict="not_reproduced",
           audit_output={}, reproduce_output=None),
    ]
    out = build_discovery_report_from_leads(leads)
    assert out is not None
    assert out["final_verdict"] is None
    assert out["empty_aggregate"] is True
    assert out["report_data"]["document_kind"] == "code_audit_report"
    assert "未确认" in out["report_data"]["reporting_decision"]


def test_aggregate_report_includes_denoise_funnel():
    class _L:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    out = build_discovery_report_from_leads(
        [_L(lead_description="x", verdict="false_positive",
            audit_output={}, reproduce_output=None)],
        denoise={
            "finding_count": 40,
            "dropped_c_count": 12,
            "dropped_c_by_engine": {"semgrep": 10, "gitleaks": 2},
            "group_count": 8,
            "bypass_count": 3,
        },
    )
    funnel = out["report_data"]["audit_summary"]["denoise_funnel"]
    assert funnel["dropped_c_count"] == 12
    assert funnel["finding_count"] == 40
    assert "C 档降噪 12" in out["report_data"]["product_intro"]


@pytest.mark.asyncio
async def test_lead_queue_concurrency_cap(monkeypatch):
    """同任务并发不超过 lead_verify_per_task。"""
    import asyncio

    from app.contexts.agent import lead_queue as lq
    from app.contexts.agent import lead_worker as lw

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
            s = self.sets.setdefault(key, set())
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
    lq.set_redis_client(mem)
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: type("S", (), {"lead_verify_per_task": 2})(),
    )

    current = 0
    peak = 0
    lock = asyncio.Lock()

    async def fake_process(**kwargs):
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.08)
        async with lock:
            current -= 1
        return None

    monkeypatch.setattr(lw, "process_one_lead", fake_process)

    items = [
        {"lead_run_id": f"lr{i}", "group_id": f"g{i}", "run_id": "r1"}
        for i in range(5)
    ]
    await lq.enqueue_leads("t1", items)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            return None

        async def rollback(self):
            return None

        async def get(self, *a, **k):
            return None

        async def execute(self, *a, **k):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

                def scalar_one_or_none(self):
                    return None

                def scalar(self):
                    return 0

            return _R()

    def factory():
        return _FakeSession()

    try:
        await lw.drain_lead_queue(
            session_factory=factory,
            task_id="t1",
            host_workdir="/tmp",
            source_path="/tmp",
            runner_env={},
            profile={"is_web": True},
            env_ready=None,
        )
        assert peak <= 2
        assert await lq.is_drained("t1")
    finally:
        lq.set_redis_client(None)


@pytest.mark.asyncio
async def test_drain_reclaims_inflight_orphan(monkeypatch):
    """claim 后进程崩溃遗留 inflight 孤儿：drain 回收入队并消费，不卡死。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.shared.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from app.contexts.agent import lead_queue as lq
    from app.contexts.agent import lead_worker as lw
    from app.contexts.finding.models import LeadRun
    from app.contexts.task.models import Task

    try:
        async with factory() as session:
            task = Task(project_address="x", task_type="discovery",
                        vulnerability_description=None, owner_id="u1", status="running")
            session.add(task)
            await session.flush()
            lead = LeadRun(task_id=task.id, run_id="r1", alert_group_id="g1",
                           queue_position=0, lead_description="d", status="running")
            session.add(lead)
            await session.commit()

            class _MemRedis:
                def __init__(self):
                    self.lists: dict[str, list] = {}
                    self.sets: dict[str, set] = {lq.inflight_key(task.id): {lead.id}}

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
                    s = self.sets.setdefault(key, set())
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

            lq.set_redis_client(_MemRedis())
            monkeypatch.setattr(
                "app.core.config.get_settings",
                lambda: type("S", (), {"lead_verify_per_task": 2})(),
            )

            calls: list[str] = []

            async def fake_process(*, session, lead_run_id, **kw):
                calls.append(lead_run_id)
                row = await session.get(LeadRun, lead_run_id)
                row.status = "completed"
                row.verdict = "confirmed"
                await session.flush()

            monkeypatch.setattr(lw, "process_one_lead", fake_process)
            await lw.drain_lead_queue(
                session_factory=factory, task_id=task.id,
                host_workdir="/tmp", source_path="/tmp", runner_env={},
                profile={"is_web": True}, env_ready=None,
            )
            assert calls == [lead.id]  # 孤儿被回收并恰好消费一次
            assert await lq.is_drained(task.id)
            lq.set_redis_client(None)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lead_path_reuses_nodes_with_container_semantics():
    """终认工位单一实现：lead 路径复用 Audit/Reproduce 节点——容器 source_path
    与 target_url 容器重写自动对齐（旧实现传宿主路径、不重写 URL）。"""
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.shared.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from app.contexts.agent.lead_worker import process_one_lead
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, LeadRun, RawFinding
    from app.contexts.task.models import Task, TaskRun

    try:
        async with factory() as session:
            task = Task(project_address="x", task_type="discovery",
                        vulnerability_description=None, owner_id="u1", status="running")
            session.add(task)
            await session.flush()
            run = TaskRun(task_id=task.id, status="running")
            session.add(run)
            await session.flush()
            sr = ScanRun(task_id=task.id, run_id=run.id, node_run_id="nr",
                         engine="semgrep", status="completed", config_summary={})
            session.add(sr)
            await session.flush()
            finding = RawFinding(
                task_id=task.id, scan_run_id=sr.id, engine="semgrep",
                rule_id="r", cwe="CWE-89", severity="error", file_path="app.py",
                line_start=1, line_end=1, message="m", fingerprint="fp1", raw={},
            )
            session.add(finding)
            await session.flush()
            group = AlertGroup(
                task_id=task.id, group_key="gk", cwe="CWE-89", file_path="app.py",
                member_count=1, representative_finding_id=finding.id,
                status="dispatched",
            )
            session.add(group)
            await session.flush()
            lead = LeadRun(task_id=task.id, run_id="r1", alert_group_id=group.id,
                           queue_position=0, lead_description="【疑似漏洞】SQL注入",
                           status="queued")
            session.add(lead)
            await session.commit()
            lead_id = lead.id

            captured: list[dict] = []

            async def fake_run(**kw):
                captured.append(kw)
                if kw["node_key"] == "audit":
                    return {"gate_verdict": "pass", "gate_reason": "ok",
                            "kill_chain": "a→b", "payloads": [{"m": "GET"}],
                            "runtime_dependent": False}
                return {"verdict": "confirmed", "reproduced": True, "attempts": [],
                        "evidence": []}

            with patch(
                "app.contexts.agent.ai_runner.run_ai_node_with_shape_retry",
                new=fake_run,
            ):
                out = await process_one_lead(
                    session=session, lead_run_id=lead_id,
                    host_workdir="/tmp/w", source_path="/tmp/w/repo",
                    runner_env={},
                    source={"repo_dirname": "repo", "commit_sha": "abc",
                            "project_path": "/tmp/w/repo", "workspace_path": "/workspace/repo"},
                    profile={"is_web": True},
                    env_ready={"target_url": "http://localhost:8080",
                               "compose_path": ".vuln-env/docker-compose.yml",
                               "initial_creds": {}},
                )
            assert out.status == "completed"
            audit_call, repro_call = captured[0], captured[1]
            # audit 走容器路径（节点行为），不再是宿主绝对路径
            assert audit_call["input_json"]["source_path"] == "/workspace/repo"
            assert audit_call["input_json"]["vulnerability_description"] == "【疑似漏洞】SQL注入"
            # reproduce 经节点做了容器侧 URL 重写
            assert repro_call["input_json"]["target_url"] != "http://localhost:8080"
            assert "host.docker.internal" in repro_call["input_json"]["target_url"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_uncertain_lead_group_returns_to_review_not_stuck_dispatched():
    """gate=uncertain（verdict=None）的线索组退回人工。

    旧行为保持 dispatched 等"任务级收尾"，但收尾只认 source_alert_group_id
    指针组——多线索任务里其余组会永久悬挂在 dispatched（复核台永远终认中）。
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.shared.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from app.contexts.agent.lead_worker import _reconcile_group
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding
    from app.contexts.task.models import Task, TaskRun

    async def _group(key: str, path: str) -> AlertGroup:
        f = RawFinding(
            task_id=task.id, scan_run_id=sr.id, engine="semgrep",
            rule_id="r", cwe="CWE-89", severity="error", file_path=path,
            line_start=1, line_end=1, message="m",
            fingerprint=f"fp-{key}", raw={},
        )
        session.add(f)
        await session.flush()
        return AlertGroup(
            task_id=task.id, group_key=key, file_path=path,
            member_count=1, representative_finding_id=f.id,
            status="dispatched",
        )

    try:
        async with factory() as session:
            task = Task(project_address="x", task_type="discovery",
                        vulnerability_description=None, owner_id="u1", status="running")
            session.add(task)
            await session.flush()
            run = TaskRun(task_id=task.id, status="running")
            session.add(run)
            await session.flush()
            sr = ScanRun(task_id=task.id, run_id=run.id, node_run_id="nr",
                         engine="semgrep", status="completed", config_summary={})
            session.add(sr)
            await session.flush()

            group = await _group("gk-uncertain", "module/a.py")
            group2 = await _group("gk-confirmed", "module/b.py")
            session.add_all([group, group2])
            await session.flush()

            await _reconcile_group(session, group.id, None)
            assert group.status == "needs_review"

            # 有权威结论的档位不受影响
            await _reconcile_group(session, group2.id, "confirmed")
            assert group2.status == "resolved"
            assert group2.resolution == "confirmed"
    finally:
        await engine.dispose()
