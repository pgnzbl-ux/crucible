"""WP1 · 发现侧流水线骨架契约测试(discovery-spec §4.2 / §4.2.2 / §4.2.4 / WP1 DoD)。

覆盖：拓扑合法性、验证任务 skip 集、审计任务并行就绪波次、
NO_DISPATCH_LEAD 时任务 completed 而非 failed、创建二选一校验、
retry from_node 覆盖新节点。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

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


# ---------- 拓扑与就绪 ----------

def test_pipeline_topology_valid():
    from app.contexts.agent.contracts import validate_pipeline

    validate_pipeline()  # 不抛即合法：requires 引用存在、无环、索引唯一递增


def test_pipeline_shape_per_spec():
    from app.contexts.agent.contracts import DEFAULT_PIPELINE, node_by_key, pipeline_for

    keys = [s.key for s in DEFAULT_PIPELINE]
    assert keys[:2] == ["source", "profile"]
    # 扫描三兄弟：gitleaks/osv 只依赖 source；semgrep 依赖 profile(要语言选包)
    assert node_by_key("scan_gitleaks").requires == ("source",)
    assert node_by_key("scan_osv").requires == ("source",)
    assert node_by_key("scan_semgrep").requires == ("source", "profile")
    assert node_by_key("scan_semgrep").require_data == ("profile.semgrep_configs",)
    assert node_by_key("api_inventory").requires == ("source", "profile")
    assert node_by_key("api_hunt").requires == ("api_inventory",)
    assert node_by_key("screen").requires == ("cluster",)
    assert node_by_key("dispatch").requires == ("triage", "api_hunt")
    # cluster 等齐三个扫描；audit 等dispatch(验证模式 dispatch 被 skip 视为满足)
    assert set(node_by_key("cluster").requires) == {"scan_semgrep", "scan_gitleaks", "scan_osv"}
    assert "dispatch" in node_by_key("audit").requires
    assert node_by_key("env_ready").requires == ("source", "profile", "dispatch")
    assert [s.key for s in pipeline_for("verify")] == [
        "source", "profile", "env_ready", "audit", "reproduce", "report",
    ]
    assert node_by_key("audit", task_type="verify").requires == ("source", "profile")
    # 声明式出口与策略（原 heavy 标记已删除：从未被强制且与 lead 并发矛盾）
    assert node_by_key("reproduce").skip_verdict == {"gate_fail": "false_positive"}
    assert node_by_key("report").lead_driven_aggregate is True
    assert node_by_key("report").failure_policy == "preserve_audit_verdict"
    assert node_by_key("source").requires_workspace is True
    assert node_by_key("source").updates_source_path is True
    assert not any(getattr(s, "heavy", False) for s in DEFAULT_PIPELINE)


def test_discovery_ready_waves_parallel_scans():
    """审计任务就绪波次：发现链先跑，有合格线索后才准备靶场。"""
    from app.contexts.agent.contracts import DEFAULT_PIPELINE
    from app.contexts.agent.orchestrator import compute_ready

    wave1 = {s.key for s in compute_ready(DEFAULT_PIPELINE, set())}
    assert wave1 == {"source"}

    after_source = {"source"}
    wave2 = {s.key for s in compute_ready(DEFAULT_PIPELINE, after_source)}
    assert wave2 == {"profile", "scan_gitleaks", "scan_osv"}

    after_scans = after_source | wave2
    wave3 = {s.key for s in compute_ready(DEFAULT_PIPELINE, after_scans)}
    assert wave3 == {"scan_semgrep", "api_inventory"}

    # 扫描齐即可开 cluster，无需等 api_inventory
    after_semgrep = after_scans | {"scan_semgrep"}
    assert {s.key for s in compute_ready(DEFAULT_PIPELINE, after_semgrep)} == {
        "api_inventory", "cluster",
    }

    after_deep = after_scans | wave3
    assert {s.key for s in compute_ready(DEFAULT_PIPELINE, after_deep)} == {
        "cluster", "api_hunt",
    }

    # 清单齐即可猎洞，无需等 cluster；扫描齐即可 cluster，无需等猎洞
    after_inventory = after_scans | {"api_inventory"}
    assert "api_hunt" in {s.key for s in compute_ready(DEFAULT_PIPELINE, after_inventory)}
    assert "screen" not in {s.key for s in compute_ready(DEFAULT_PIPELINE, after_inventory)}

    # cluster 齐即可开 screen，无需等 api_hunt
    after_cluster_only = after_deep | {"cluster"}
    assert {s.key for s in compute_ready(DEFAULT_PIPELINE, after_cluster_only)} == {
        "api_hunt", "screen",
    }
    after_hunt_only = after_deep | {"api_hunt"}
    assert {s.key for s in compute_ready(DEFAULT_PIPELINE, after_hunt_only)} == {"cluster"}

    # 扫描复核链与猎洞并列汇入 dispatch
    after_screen = after_deep | {"cluster", "api_hunt", "screen"}
    assert {s.key for s in compute_ready(DEFAULT_PIPELINE, after_screen)} == {"triage"}
    after_triage_no_hunt = after_deep | {"cluster", "screen", "triage"}
    assert "dispatch" not in {
        s.key for s in compute_ready(DEFAULT_PIPELINE, after_triage_no_hunt)
    }
    after_triage = after_screen | {"triage"}
    assert {s.key for s in compute_ready(DEFAULT_PIPELINE, after_triage)} == {"dispatch"}
    after_dispatch = after_triage | {"dispatch"}
    assert {s.key for s in compute_ready(DEFAULT_PIPELINE, after_dispatch)} == {
        "env_ready", "audit",
    }


def test_verify_pipeline_is_pruned_not_skip_chain():
    from app.contexts.agent.contracts import descendant_keys, pipeline_for, validate_pipeline
    from app.contexts.agent.orchestrator import compute_ready

    pipeline = pipeline_for("verify")
    validate_pipeline(pipeline)
    keys = {spec.key for spec in pipeline}
    assert keys == {"source", "profile", "env_ready", "audit", "reproduce", "report"}
    assert {s.key for s in compute_ready(pipeline, {"source", "profile"})} == {
        "env_ready", "audit",
    }
    discovery = pipeline_for("discovery")
    assert "env_ready" in descendant_keys(discovery, "triage")
    assert "api_hunt" not in descendant_keys(discovery, "triage")
    with pytest.raises(ValueError, match="未知任务类型"):
        pipeline_for("banana")


def test_verify_mode_skip_signals():
    """验证任务：扫描/清单/猎洞/聚类/二审/调度被 VERIFY_MODE skip；NON_WEB 不受影响。"""
    from app.contexts.agent.contracts import HandoffStore, SkipWhen, node_by_key
    from app.contexts.agent.orchestrator import _evaluate_skip

    store = HandoffStore()  # 无任何输出
    for key in (
        "scan_gitleaks", "scan_osv", "scan_semgrep", "api_inventory", "api_hunt",
        "cluster", "screen", "triage", "dispatch",
    ):
        assert _evaluate_skip(node_by_key(key), store, verify_mode=True) == SkipWhen.VERIFY_MODE
        assert _evaluate_skip(node_by_key(key), store, verify_mode=False) is None
    # is_web 未定/False 时 env_ready 等仍按 NON_WEB 出口
    store.set("profile", {"is_web": False})
    assert _evaluate_skip(node_by_key("env_ready"), store, verify_mode=True) == SkipWhen.NON_WEB


def test_no_dispatch_lead_signal_semantics():
    from app.contexts.agent.contracts import HandoffStore

    store = HandoffStore()
    store.set("dispatch", {"has_lead": False})
    # 审计任务：无主线索
    assert store.signals(verify_mode=False).no_dispatch_lead is True
    # 验证任务：恒 False(人已给线索)
    assert store.signals(verify_mode=True).no_dispatch_lead is False

    store.set("dispatch", {"has_lead": True})
    assert store.signals(verify_mode=False).no_dispatch_lead is False


# ---------- 编排端到端(骨架节点) ----------

async def _seed(session, task_type="discovery"):
    from app.contexts.task.models import Task, TaskRun

    task = Task(
        project_address="https://github.com/a/b.git",
        task_type=task_type,
        vulnerability_description=None if task_type == "discovery" else "SQL injection in login",
        owner_id="u1", status="running",
    )
    session.add(task)
    await session.flush()
    run = TaskRun(task_id=task.id, status="running")
    session.add(run)
    await session.flush()
    return task, run


def test_lead_driven_signal_skips_audit_reproduce():
    from app.contexts.agent.contracts import HandoffStore, SkipWhen, node_by_key
    from app.contexts.agent.orchestrator import _evaluate_skip

    store = HandoffStore()
    store.set("profile", {"is_web": True})
    store.set("dispatch", {"has_lead": True, "queued_count": 2})
    assert store.signals(verify_mode=False).lead_driven is True
    assert _evaluate_skip(node_by_key("audit"), store, verify_mode=False) == SkipWhen.LEAD_DRIVEN
    assert _evaluate_skip(node_by_key("reproduce"), store, verify_mode=False) == SkipWhen.LEAD_DRIVEN
    # verify 不命中
    assert _evaluate_skip(node_by_key("audit"), store, verify_mode=True) is None


@pytest.mark.asyncio
async def test_discovery_no_lead_completes_with_terminal_skipped(session_factory):
    """无 A 级高置信 TP → 终认节点 skipped，但仍生成零漏洞审计报告。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun, Task

    async with session_factory() as session:
        task, run = await _seed(session)

        async def fake_source(ctx, node_input=None):
            return {"source_path": ctx.source_path, "repo_dirname": "demo", "commit_sha": "abc"}

        async def fake_profile(ctx, node_input=None):
            return {"is_web": True, "language": "python", "semgrep_configs": ["p/python"]}

        async def fake_env(ctx, node_input=None):
            return {"target_url": "http://x:8080", "compose_path": "y.yml"}

        async def fake_scan_ok(engine):
            async def inner(ctx, node_input=None):
                return {"engine": engine, "scan_run_id": None, "status": "skipped", "finding_count": 0}
            return inner

        with (
            patch.object(orch._NODE_EXECUTORS["source"], "execute", fake_source),
            patch.object(orch._NODE_EXECUTORS["profile"], "execute", fake_profile),
            patch.object(orch._NODE_EXECUTORS["env_ready"], "execute", fake_env),
            patch.object(orch._NODE_EXECUTORS["scan_gitleaks"], "execute", await fake_scan_ok("gitleaks")),
            patch.object(orch._NODE_EXECUTORS["scan_osv"], "execute", await fake_scan_ok("osv")),
            patch.object(orch._NODE_EXECUTORS["scan_semgrep"], "execute", await fake_scan_ok("semgrep")),
            patch.object(orch._NODE_EXECUTORS["api_inventory"], "execute",
                         AsyncMock(return_value={"ok": True, "parser": "none", "endpoint_count": 0, "pve_count": 0})),
            patch.object(orch._NODE_EXECUTORS["api_hunt"], "execute",
                         AsyncMock(return_value={"ok": True, "reviewed_count": 0, "suspect_count": 0, "finding_count": 0})),
        ):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "completed"
        assert result["verdict"] is None
        nodes = (
            await session.execute(select(NodeRun).where(NodeRun.run_id == run.id))
        ).scalars().all()
        by_key = {n.node_key: n.status for n in nodes}
        assert by_key["scan_gitleaks"] == "completed"  # WP1 骨架：零 finding 完成
        assert by_key["scan_osv"] == "completed"
        assert by_key["scan_semgrep"] == "completed"
        assert by_key["api_inventory"] == "completed"
        assert by_key["api_hunt"] == "completed"
        assert by_key["cluster"] == "completed"
        assert by_key["triage"] == "completed"
        assert by_key["dispatch"] == "completed"
        # 无主线索 → 终认被 NO_DISPATCH_LEAD skip；聚合报告仍完成
        assert by_key["audit"] == "skipped"
        assert by_key["reproduce"] == "skipped"
        assert by_key["report"] == "completed"
        report_node = next(n for n in nodes if n.node_key == "report")
        assert "code_audit_report" in (report_node.output_json or "")
        refreshed = await session.get(Task, task.id)
        assert refreshed.status == "completed"
        assert refreshed.verdict is None


@pytest.mark.asyncio
async def test_discovery_with_lead_runs_terminal_nodes(session_factory):
    """dispatch 有入队线索 → DAG audit/reproduce LEAD_DRIVEN skip；report 聚合完成。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.agent.lead_queue import set_redis_client
    from app.contexts.task.models import NodeRun

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
            task, run = await _seed(session)

            async def fake_source(ctx, node_input=None):
                return {"source_path": ctx.source_path, "repo_dirname": "demo", "commit_sha": "abc"}

            async def fake_profile(ctx, node_input=None):
                return {"is_web": True, "language": "python"}

            async def fake_dispatch(ctx, node_input=None):
                return {
                    "has_lead": True, "queued_count": 1, "queued_group_ids": ["g1"],
                    "lead_group_id": "g1",
                    "lead_description": "【疑似漏洞】SQL注入", "review_count": 0,
                    "archived_count": 0, "skipped_unaudited_count": 0,
                }

            async def fake_env(ctx, node_input=None):
                return {"target_url": "http://x:8080", "compose_path": "y.yml"}

            async def fake_scan_ok(engine):
                async def inner(ctx, node_input=None):
                    return {"engine": engine, "scan_run_id": None, "status": "skipped", "finding_count": 0}
                return inner

            execs = orch._NODE_EXECUTORS
            with (
                patch.object(execs["source"], "execute", fake_source),
                patch.object(execs["profile"], "execute", fake_profile),
                patch.object(execs["dispatch"], "execute", fake_dispatch),
                patch.object(execs["env_ready"], "execute", fake_env),
                patch.object(execs["scan_gitleaks"], "execute", await fake_scan_ok("gitleaks")),
                patch.object(execs["scan_osv"], "execute", await fake_scan_ok("osv")),
                patch.object(execs["scan_semgrep"], "execute", await fake_scan_ok("semgrep")),
                patch.object(execs["api_inventory"], "execute",
                             AsyncMock(return_value={"ok": True, "parser": "none", "endpoint_count": 0, "pve_count": 0})),
                patch.object(execs["api_hunt"], "execute",
                             AsyncMock(return_value={"ok": True, "reviewed_count": 0, "suspect_count": 0, "finding_count": 0})),
            ):
                result = await orch.run_orchestration(
                    task_id=task.id, run_id=run.id, session=session,
                    host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
                )

            assert result["status"] == "completed"
            # 无真实 LeadRun 确认项 → 聚合空报告，verdict 空
            assert result["verdict"] is None
            nodes = (
                await session.execute(select(NodeRun).where(NodeRun.run_id == run.id))
            ).scalars().all()
            by_key = {n.node_key: n.status for n in nodes}
            assert by_key["audit"] == "skipped"  # LEAD_DRIVEN
            assert by_key["reproduce"] == "skipped"
            assert by_key["report"] == "completed"
    finally:
        set_redis_client(None)


@pytest.mark.asyncio
async def test_verify_task_instantiates_only_verify_subgraph(session_factory):
    """非 Web 定向验证不创建发现链 NodeRun。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun

    async with session_factory() as session:
        task, run = await _seed(session, task_type="verify")

        async def fake_source(ctx, node_input=None):
            return {"source_path": ctx.source_path, "repo_dirname": "demo", "commit_sha": "abc"}

        async def fake_profile(ctx, node_input=None):
            return {"is_web": False, "language": "python"}

        async def fake_audit(ctx, node_input=None):
            return {"gate_verdict": "pass", "core_claim": "代码路径可达"}

        async def fake_report(ctx, node_input=None):
            return {
                "final_verdict": "code_reachable",
                "report_data": {"document_kind": "verification_record", "product_intro": "白盒终认"},
            }

        with (
            patch.object(orch._NODE_EXECUTORS["source"], "execute", fake_source),
            patch.object(orch._NODE_EXECUTORS["profile"], "execute", fake_profile),
            patch.object(orch._NODE_EXECUTORS["audit"], "execute", fake_audit),
            patch.object(orch._NODE_EXECUTORS["report"], "execute", fake_report),
        ):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "completed"
        assert result["non_web"] is True
        nodes = (
            await session.execute(select(NodeRun).where(NodeRun.run_id == run.id))
        ).scalars().all()
        by_key = {n.node_key: n.status for n in nodes}
        assert by_key["source"] == "completed"
        assert by_key["profile"] == "completed"
        for key in ("scan_gitleaks", "scan_osv", "scan_semgrep", "api_inventory", "api_hunt",
                    "cluster", "screen", "triage", "dispatch"):
            assert key not in by_key, f"{key} 不应实例化"
        assert by_key["env_ready"] == "skipped"
        assert by_key["reproduce"] == "skipped"
        assert by_key["audit"] == "completed"
        assert by_key["report"] == "completed"


@pytest.mark.asyncio
async def test_parallel_wave_runs_concurrently(session_factory, tmp_path):
    """并发波专项(WP1 DoD)：profile/gitleaks/osv 同波并发执行(提供 session 工厂)。

    文件版 sqlite + 独立会话工厂，三个节点用事件屏障验证真实并发。
    """
    import asyncio

    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import Task, TaskRun

    db_file = tmp_path / "parallel.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            task = Task(
                project_address="https://github.com/a/b.git", task_type="discovery",
                vulnerability_description=None, owner_id="u1", status="running",
            )
            session.add(task)
            await session.flush()
            run = TaskRun(task_id=task.id, status="running")
            session.add(run)
            await session.flush()

            barrier = asyncio.Barrier(3)
            overlapped = {"n": 0}

            async def fake_source(ctx, node_input=None):
                return {"source_path": ctx.source_path, "repo_dirname": "demo", "commit_sha": "abc"}

            async def gated_profile(ctx, node_input=None):
                overlapped["n"] += 1
                await barrier.wait()
                return {"is_web": True, "language": "python", "semgrep_configs": ["p/python"]}

            async def gated_scan(node_key):
                async def inner(ctx, node_input=None):
                    overlapped["n"] += 1
                    await barrier.wait()  # 三者必须同时在飞，否则超时
                    return {"engine": node_key.replace("scan_", ""), "scan_run_id": None,
                            "status": "skipped", "finding_count": 0}
                return inner

            async def fake_env(ctx, node_input=None):
                return {"target_url": "http://x:8080", "compose_path": "y.yml", "ok": True}

            async def fake_pass(ctx, node_input=None):
                return {"ok": True}

            execs = orch._NODE_EXECUTORS
            with (
                patch.object(execs["source"], "execute", fake_source),
                patch.object(execs["profile"], "execute", gated_profile),
                patch.object(execs["scan_gitleaks"], "execute", await gated_scan("scan_gitleaks")),
                patch.object(execs["scan_osv"], "execute", await gated_scan("scan_osv")),
                patch.object(execs["scan_semgrep"], "execute", fake_pass),
                patch.object(execs["api_inventory"], "execute", fake_pass),
                patch.object(execs["env_ready"], "execute", fake_env),
                patch.object(execs["cluster"], "execute", fake_pass),
                patch.object(execs["api_hunt"], "execute", fake_pass),
                patch.object(execs["screen"], "execute", fake_pass),
                patch.object(execs["triage"], "execute", fake_pass),
                patch.object(execs["dispatch"], "execute",
                             AsyncMock(return_value={"has_lead": False})),
            ):
                result = await asyncio.wait_for(
                    orch.run_orchestration(
                        task_id=task.id, run_id=run.id, session=session,
                        host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
                        node_session_factory=factory,
                    ),
                    timeout=10,
                )
            assert result["status"] == "completed"
            assert overlapped["n"] == 3  # profile + gitleaks + osv 同波并发
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cluster_starts_while_env_ready_still_running(tmp_path):
    """渐进调度：三扫描完成后 cluster 立即开工，不整波等待 env_ready。"""
    import asyncio

    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import Task, TaskRun

    db_file = tmp_path / "progressive.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            task = Task(
                project_address="https://github.com/a/b.git", task_type="discovery",
                vulnerability_description=None, owner_id="u1", status="running",
            )
            session.add(task)
            await session.flush()
            run = TaskRun(task_id=task.id, status="running")
            session.add(run)
            await session.flush()

            release_env = asyncio.Event()
            cluster_started = asyncio.Event()
            env_still_running_when_cluster_started = {"ok": False}

            from app.shared.object_store import MemoryObjectStore, set_object_store_for_tests
            set_object_store_for_tests(MemoryObjectStore())

            async def fake_source(ctx, node_input=None):
                return {"source_path": ctx.source_path, "repo_dirname": "demo", "commit_sha": "abc"}

            async def fake_profile(ctx, node_input=None):
                return {"is_web": True, "language": "python", "semgrep_configs": ["p/python"]}

            async def fake_scan(ctx, node_input=None):
                return {"engine": "x", "scan_run_id": None, "status": "completed", "finding_count": 0}

            async def slow_env(ctx, node_input=None):
                await release_env.wait()
                return {"target_url": "http://x:8080", "compose_path": "y.yml", "ok": True}

            async def watch_cluster(ctx, node_input=None):
                env_still_running_when_cluster_started["ok"] = not release_env.is_set()
                cluster_started.set()
                return {"ok": True, "group_count": 0, "finding_count": 0}

            async def fake_pass(ctx, node_input=None):
                return {
                    "ok": True,
                    "parser": "none",
                    "endpoint_count": 0,
                    "pve_count": 0,
                    "reviewed_count": 0,
                    "suspect_count": 0,
                    "finding_count": 0,
                    "group_count": 0,
                }

            execs = orch._NODE_EXECUTORS
            try:
                with (
                    patch.object(execs["source"], "execute", fake_source),
                    patch.object(execs["profile"], "execute", fake_profile),
                    patch.object(execs["scan_gitleaks"], "execute", fake_scan),
                    patch.object(execs["scan_osv"], "execute", fake_scan),
                    patch.object(execs["scan_semgrep"], "execute", fake_scan),
                    patch.object(execs["api_inventory"], "execute", fake_pass),
                    patch.object(execs["env_ready"], "execute", slow_env),
                    patch.object(execs["cluster"], "execute", watch_cluster),
                    patch.object(execs["api_hunt"], "execute", fake_pass),
                    patch.object(execs["screen"], "execute", fake_pass),
                    patch.object(execs["triage"], "execute", fake_pass),
                    patch.object(execs["dispatch"], "execute",
                                 AsyncMock(return_value={"has_lead": False})),
                ):
                    orch_task = asyncio.create_task(orch.run_orchestration(
                        task_id=task.id, run_id=run.id, session=session,
                        host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
                        node_session_factory=factory,
                    ))
                    await asyncio.wait_for(cluster_started.wait(), timeout=5)
                    assert env_still_running_when_cluster_started["ok"] is True
                    release_env.set()
                    result = await asyncio.wait_for(orch_task, timeout=10)
                assert result["status"] == "completed"
            finally:
                set_object_store_for_tests(None)
    finally:
        await engine.dispose()


# ---------- 创建二选一 / retry ----------

def test_task_create_request_two_modes():
    from app.contexts.task.schemas import TaskCreateRequest
    from pydantic import ValidationError

    ok_verify = TaskCreateRequest(
        project_address="https://github.com/a/b.git",
        vulnerability_description="SQL injection in login",
    )
    assert ok_verify.task_type == "verify"

    ok_disc = TaskCreateRequest(
        project_address="https://github.com/a/b.git", task_type="discovery",
    )
    assert ok_disc.vulnerability_description is None

    with pytest.raises(ValidationError):
        TaskCreateRequest(
            project_address="x", task_type="discovery",
            vulnerability_description="不该填的描述内容",
        )
    with pytest.raises(ValidationError):
        TaskCreateRequest(project_address="x")  # verify 缺描述


def test_to_detail_discovery_null_description():
    """审计任务描述为 NULL 时，详情序列化不能把 ValidationError 打成 400。"""
    from datetime import datetime, timezone

    from app.contexts.task.models import Task
    from app.contexts.task.service import TaskService

    now = datetime.now(timezone.utc)
    task = Task(
        id="t-disc",
        project_address="https://github.com/a/b.git",
        source_type="git",
        task_type="discovery",
        vulnerability_description=None,
        status="queued",
        priority="medium",
        owner_id="u1",
        credential_refs="[]",
        created_at=now,
        updated_at=now,
    )
    detail = TaskService._to_detail(task)
    assert detail.task_type == "discovery"
    assert detail.vulnerability_description == ""


def test_retryable_from_nodes_cover_discovery():
    from app.contexts.task.service import _RETRYABLE_FROM_NODES

    for key in ("scan_gitleaks", "scan_osv", "scan_semgrep", "cluster", "screen", "triage",
                "dispatch", "env_ready", "audit", "reproduce", "report"):
        assert key in _RETRYABLE_FROM_NODES
    assert "source" not in _RETRYABLE_FROM_NODES
    assert "profile" not in _RETRYABLE_FROM_NODES


@pytest.mark.asyncio
async def test_wave_failure_does_not_mark_cancelled_siblings_completed(tmp_path):
    """波内一节点失败：兄弟节点取消，尚未就绪的下游节点保持未创建/未执行。"""
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from unittest.mock import patch

    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun, Task, TaskRun
    from app.shared.base import Base

    db_file = tmp_path / "wavefail.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            task = Task(
                project_address="https://github.com/a/b.git", task_type="discovery",
                vulnerability_description=None, owner_id="u1", status="running",
            )
            session.add(task)
            await session.flush()
            run = TaskRun(task_id=task.id, status="running")
            session.add(run)
            await session.flush()

            events: list[tuple[str, str]] = []

            async def on_node_event(key, status, output):
                events.append((key, status))

            async def fake_source(ctx, node_input=None):
                return {"source_path": ctx.source_path, "repo_dirname": "demo", "commit_sha": "abc"}

            async def slow_node(ctx, node_input=None):
                await asyncio.sleep(30)  # 失败发生时仍在飞 → 被连带取消
                return {"engine": "x", "scan_run_id": None, "status": "skipped",
                        "finding_count": 0}

            async def failing_osv(ctx, node_input=None):
                await asyncio.sleep(0.05)
                raise RuntimeError("osv engine exploded")

            execs = orch._NODE_EXECUTORS
            with (
                patch.object(execs["source"], "execute", fake_source),
                patch.object(execs["profile"], "execute", slow_node),
                patch.object(execs["scan_gitleaks"], "execute", slow_node),
                patch.object(execs["scan_osv"], "execute", failing_osv),
            ):
                result = await asyncio.wait_for(
                    orch.run_orchestration(
                        task_id=task.id, run_id=run.id, session=session,
                        host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
                        node_session_factory=factory,
                        on_node_event=on_node_event,
                    ),
                    timeout=20,
                )
            assert result["status"] == "failed"
            assert result["node"] == "scan_osv"
            # 被连带取消的兄弟节点绝不能对外报 completed
            assert ("profile", "completed") not in events
            assert ("scan_gitleaks", "completed") not in events
            nodes = (await session.execute(
                select(NodeRun).where(NodeRun.run_id == run.id)
            )).scalars().all()
            by_key = {n.node_key: n.status for n in nodes}
            assert by_key["scan_osv"] == "failed"
            # 兄弟节点收敛到终态，不残留 running
            assert by_key.get("profile") == "cancelled"
            assert by_key.get("scan_gitleaks") == "cancelled"
            # 后续必要节点尚未进入 ready wave，不能被批量伪造成 failed/cancelled。
            assert by_key.get("scan_semgrep") is None
            assert by_key.get("env_ready") is None
            assert by_key.get("cluster") is None
    finally:
        await engine.dispose()
