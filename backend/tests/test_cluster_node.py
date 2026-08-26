"""WP3 · cluster 节点与聚类测试(discovery-spec §6.2 / §2.4)。

覆盖：函数索引构建(Python/Java)、分组键(同函数合并/无索引降级/osv 特例)、
clue_grade A/B/F、攻击面降权(gitleaks/CWE-798 例外、CWE-918 不降权)、
全引擎失败入口检查、重跑合并幂等。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


# ---------- 函数索引 ----------

PY_SOURCE = """import os


def handler(req):
    data = req.args.get("q")
    return query(data)


class Foo:
    def method_a(self):
        return 1
"""

JAVA_SOURCE = """public class Svc {
    public String exec(String input) {
        return input;
    }
    private int helper() {
        return 1;
    }
}
"""


def test_index_python_functions(tmp_path):
    from app.contexts.finding.context_extractor import build_function_index, enclosing

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(PY_SOURCE, encoding="utf-8")
    index = build_function_index(str(repo))
    symbols = {e["symbol"] for e in index}
    assert {"handler", "method_a"} <= symbols
    entry = enclosing(index, "app.py", 5)
    assert entry and entry["symbol"] == "handler"


def test_index_java_methods(tmp_path):
    from app.contexts.finding.context_extractor import build_function_index

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Svc.java").write_text(JAVA_SOURCE, encoding="utf-8")
    index = build_function_index(str(repo))
    symbols = {e["symbol"] for e in index}
    assert {"exec", "helper"} <= symbols


JS_SOURCE = """
export function handleRequest(req) {
  return req.query.q;
}

class Svc {
  run(input) {
    return input;
  }
}
"""

TS_SOURCE = """
export async function fetchUser(id: string): Promise<void> {
  return;
}
"""


def test_index_javascript_and_typescript(tmp_path):
    from app.contexts.finding.context_extractor import build_function_index

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(JS_SOURCE, encoding="utf-8")
    (repo / "user.ts").write_text(TS_SOURCE, encoding="utf-8")
    index = build_function_index(str(repo), languages=["javascript", "typescript"])
    symbols = {e["symbol"] for e in index}
    assert {"handleRequest", "run", "fetchUser"} <= symbols


def test_resolve_index_languages_from_profile():
    from app.contexts.finding.context_extractor import resolve_index_languages

    assert resolve_index_languages(["nodejs"]) == ["javascript", "typescript"]
    assert resolve_index_languages(["python"]) == ["python"]
    assert resolve_index_languages(["go"]) == ["go"]
    assert resolve_index_languages(["php"]) == ["php"]
    assert resolve_index_languages(["go", "php"]) == ["go", "php"]
    # 有画像但无一可索引 → 空，不回退全扫
    assert resolve_index_languages(["rust"]) == []
    assert resolve_index_languages([]) == []
    assert resolve_index_languages(None) == [
        "go", "java", "javascript", "php", "python", "typescript",
    ]


def test_index_go_and_php(tmp_path):
    from app.contexts.finding.context_extractor import build_function_index

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "handler.go").write_text(
        "package main\n\nfunc Handle(w int) {}\n\n"
        "func (s *Svc) Run(x int) int { return x }\n",
        encoding="utf-8",
    )
    (repo / "app.php").write_text(
        "<?php\nfunction handle_request($req) { return $req; }\n"
        "class Svc { public function run($x) { return $x; } }\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("def py_only():\n    pass\n", encoding="utf-8")
    index = build_function_index(str(repo), languages=["go", "php"])
    symbols = {e["symbol"] for e in index}
    assert {"Handle", "Run", "handle_request", "run"} <= symbols
    assert "py_only" not in symbols


def test_index_respects_language_filter(tmp_path):
    from app.contexts.finding.context_extractor import build_function_index

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def py_only():\n    pass\n")
    (repo / "app.js").write_text("function jsOnly() {}\n")
    index = build_function_index(str(repo), languages=["python"])
    assert {e["symbol"] for e in index} == {"py_only"}


def test_index_skips_vendor_dirs(tmp_path):
    from app.contexts.finding.context_extractor import build_function_index

    repo = tmp_path / "repo"
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "x.py").write_text("def leak():\n    pass\n")
    (repo / "app.py").write_text("def ok():\n    pass\n")
    index = build_function_index(str(repo))
    assert {e["symbol"] for e in index} == {"ok"}


def test_read_function_source_with_line_numbers(tmp_path):
    from app.contexts.finding.context_extractor import build_function_index, read_function_source

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(PY_SOURCE)
    index = build_function_index(str(repo))
    entry = next(e for e in index if e["symbol"] == "handler")
    src = read_function_source(str(repo), entry)
    assert src and src.splitlines()[0].startswith("4\t")  # 行号前缀
    assert "def handler" in src


# ---------- 分组与降权 ----------

def _finding(**kw):
    base = {
        "id": "f1", "engine": "semgrep", "rule_id": "python.sqli", "cwe": "CWE-89",
        "severity": "error", "file_path": "app/db.py", "line_start": 42,
        "line_end": 42, "source_to_sink": None, "raw": {},
    }
    base.update(kw)
    return base


def test_same_function_merges_into_one_group():
    from app.contexts.finding.clustering import cluster_findings

    index = [{"file": "app/db.py", "symbol": "handler", "start_line": 40, "end_line": 50}]
    groups = cluster_findings(
        [_finding(rule_id="r1"), _finding(id="f2", rule_id="r2", line_start=45)],
        index,
    )
    assert len(groups) == 1
    assert groups[0]["member_count"] == 2
    assert groups[0]["function_symbol"] == "handler"
    assert groups[0]["engine_set"] == ["semgrep"]


def test_no_index_falls_back_to_rule_grouping():
    from app.contexts.finding.clustering import cluster_findings

    groups = cluster_findings([_finding(), _finding(id="f2", rule_id="other.rule")], [])
    assert len(groups) == 2  # 无函数索引 → 按 rule_id 分


def test_osv_groups_by_dependency():
    from app.contexts.finding.clustering import cluster_findings

    f1 = _finding(
        engine="osv", rule_id="GHSA-1", cwe=None, file_path="requirements.txt",
        line_start=None, raw={"dependency_name": "jinja2"},
    )
    f2 = _finding(
        id="f2", engine="osv", rule_id="GHSA-2", cwe=None, file_path="requirements.txt",
        line_start=None, raw={"dependency_name": "jinja2"},
    )
    f3 = _finding(
        id="f3", engine="osv", rule_id="GHSA-3", cwe=None, file_path="requirements.txt",
        line_start=None, raw={"dependency_name": "flask"},
    )
    groups = cluster_findings([f1, f2, f3], [])
    # osv 组键 = (rule_id, dependency) —— 同依赖的不同漏洞各自成组；无 A/B/F
    assert len(groups) == 3
    assert all(g["clue_grade"] is None for g in groups)


def test_clue_grades():
    from app.contexts.finding.clustering import cluster_findings

    a = _finding(source_to_sink=["a.py:1", "b.py:2"])
    b = _finding(file_path="app/other.py", rule_id="python.sqli2")
    f_grade = _finding(cwe=None, rule_id="unknown.rule", line_start=None, file_path="nowhere.py")
    groups = cluster_findings([a, b, f_grade], [])
    grades = {g["clue_grade"] for g in groups}
    assert grades == {"A", "B", "F"}


def test_missing_cwe_is_inferred_before_grouping_and_grading():
    from app.contexts.finding.clustering import cluster_findings

    groups = cluster_findings([
        _finding(cwe=None, rule_id="python.lang.security.audit.subprocess-shell-true", message="subprocess with shell=True"),
    ], [])

    assert groups[0]["cwe"] == "CWE-78"
    assert groups[0]["clue_grade"] == "B"


def test_downgrade_rules():
    from app.contexts.finding.clustering import cluster_findings

    def _priority(path, cwe="CWE-89", engine="semgrep"):
        groups = cluster_findings([_finding(file_path=path, cwe=cwe, engine=engine)], [])
        return groups[0]["priority"]

    assert _priority("tests/test_sqli.py") == "low"          # 注入类在 tests/ → 降权
    assert _priority("app/db.py") != "low"                    # 正常路径不降
    assert _priority("tests/leak.py", cwe="CWE-798", engine="gitleaks") != "low"  # gitleaks 例外
    assert _priority("tests/ssrf.py", cwe="CWE-918") != "low"  # 集合外 CWE 不因路径降权
    assert _priority("docs/config.md") == "low"               # 文档路径降权


# ---------- 节点行为 ----------

async def _seed_discovery_task(session, tmp_path, scan_runs_spec, findings):
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.discovery.models import ScanRun
    from app.contexts.discovery.service import DiscoveryService
    from app.contexts.task.models import Task, TaskRun

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "app.py").write_text("def handler():\n    pass\n")
    task = Task(project_address="x", task_type="discovery",
                vulnerability_description=None, owner_id="u1", status="running")
    session.add(task)
    await session.flush()
    run = TaskRun(task_id=task.id, status="running")
    session.add(run)
    await session.flush()

    svc = DiscoveryService(session)
    for engine, status in scan_runs_spec:
        sr = await svc.start_scan_run(
            task_id=task.id, run_id=run.id, node_run_id=f"nr-{engine}", engine=engine,
            config_summary={},
        )
        await svc.finish_scan_run(sr, status=status)
    if findings:
        count = await svc.upsert_raw_findings(
            task_id=task.id, scan_run_id=f"sr-{findings[0]['engine']}", findings=findings,
        )
        assert count == len(findings)

    ctx = NodeContext(
        task_id=task.id, run_id=run.id, host_workdir=str(tmp_path),
        source_path=str(repo), vulnerability_description="",
        project_address="x", project_ref=None, db_session=session,
        node_run_id="nr-cluster",
    )
    from app.contexts.agent.contracts import ClusterInput, SourceHandoff

    inp = ClusterInput(
        source=SourceHandoff(project_path=str(repo), repo_dirname="repo"),
        host_workdir=str(tmp_path), source_path=str(repo), scans=[],
    )
    return ctx, task, run, inp


@pytest.mark.asyncio
async def test_all_engines_failed_raises(session_factory, tmp_path):
    from app.contexts.agent.nodes.cluster import ClusterNode

    async with session_factory() as session:
        ctx, task, run, inp = await _seed_discovery_task(
            session, tmp_path,
            [("semgrep", "failed"), ("gitleaks", "failed"), ("osv", "failed")],
            [],
        )
        with pytest.raises(RuntimeError, match="全引擎失败"):
            await ClusterNode().execute(ctx, inp)


@pytest.mark.asyncio
async def test_cluster_node_groups_and_grades(session_factory, tmp_path):
    from app.contexts.agent.nodes.cluster import ClusterNode
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.sarif import fingerprint

    findings = [
        {
            "engine": "semgrep", "rule_id": "python.sqli", "cwe": "CWE-89",
            "severity": "error", "file_path": "app.py", "line_start": 2, "line_end": 2,
            "message": "sqli", "source_to_sink": None, "code_snippet": None,
            "fingerprint": fingerprint("semgrep", "python.sqli", "app.py", 2, "CWE-89"),
            "raw": {},
        },
    ]
    async with session_factory() as session:
        ctx, task, run, inp = await _seed_discovery_task(
            session, tmp_path,
            [("semgrep", "completed"), ("gitleaks", "skipped"), ("osv", "skipped")],
            findings,
        )
        out = await ClusterNode().execute(ctx, inp)
        assert out["group_count"] == 1
        assert out["index_built"] is True
        assert out["groups_by_grade"].get("B") == 1
        assert out["index_symbol_count"] >= 1
        assert "python" in out["index_languages"]
        assert out["finding_count"] == 1
        assert out["dropped_c_count"] == 0
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        assert len(groups) == 1
        assert groups[0].function_symbol == "handler"  # 索引反查
        assert groups[0].status == "clustered"

        # 重跑幂等：组不重建
        out2 = await ClusterNode().execute(ctx, inp)
        assert out2["group_count"] == 1
        groups2 = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        assert len(groups2) == 1


@pytest.mark.asyncio
async def test_cluster_drops_c_grade_findings(session_factory, tmp_path):
    from app.contexts.agent.nodes.cluster import ClusterNode
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.sarif import fingerprint

    findings = [
        {
            "engine": "semgrep", "rule_id": "python.low", "cwe": "CWE-89",
            "severity": "note", "file_path": "app.py", "line_start": 2, "line_end": 2,
            "message": "low conf", "source_to_sink": None, "code_snippet": None,
            "fingerprint": fingerprint("semgrep", "python.low", "app.py", 2, "CWE-89"),
            "raw": {"confidence": "LOW", "has_dataflow": False, "category": "security"},
        },
        {
            "engine": "semgrep", "rule_id": "python.sqli", "cwe": "CWE-89",
            "severity": "error", "file_path": "app.py", "line_start": 2, "line_end": 2,
            "message": "sqli", "source_to_sink": ["app.py:1", "app.py:2"], "code_snippet": None,
            "fingerprint": fingerprint("semgrep", "python.sqli", "app.py", 2, "CWE-89"),
            "raw": {"confidence": "HIGH", "has_dataflow": True, "category": "security"},
        },
    ]
    async with session_factory() as session:
        ctx, task, run, inp = await _seed_discovery_task(
            session, tmp_path,
            [("semgrep", "completed"), ("gitleaks", "skipped"), ("osv", "skipped")],
            findings,
        )
        out = await ClusterNode().execute(ctx, inp)
        assert out["finding_count"] == 2
        assert out["dropped_c_count"] == 1
        assert out["dropped_c_by_engine"] == {"semgrep": 1}
        assert out["group_count"] == 1
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        assert len(groups) == 1
        assert groups[0].clue_grade == "A"


@pytest.mark.asyncio
async def test_cluster_emits_phase_events(session_factory, tmp_path):
    """聚类过程必须写 AgentEvent，点流程图才能看到日志。"""
    from app.contexts.agent.nodes.cluster import ClusterNode
    from app.contexts.finding.sarif import fingerprint

    findings = [{
        "engine": "semgrep", "rule_id": "python.sqli", "cwe": "CWE-89",
        "severity": "error", "file_path": "app.py", "line_start": 2, "line_end": 2,
        "message": "sqli", "source_to_sink": None, "code_snippet": None,
        "fingerprint": fingerprint("semgrep", "python.sqli", "app.py", 2, "CWE-89"),
        "raw": {},
    }]
    async with session_factory() as session:
        ctx, task, run, inp = await _seed_discovery_task(
            session, tmp_path,
            [("semgrep", "completed"), ("gitleaks", "skipped"), ("osv", "skipped")],
            findings,
        )
        events: list[dict] = []
        ctx.on_event = events.append
        await ClusterNode().execute(ctx, inp)
        messages = [e.get("message") for e in events if e.get("type") == "phase.updated"]
        assert any("索引" in str(m) for m in messages)
        assert any("组" in str(m) for m in messages)
        assert events[0]["phase"] == "cluster"


@pytest.mark.asyncio
async def test_osv_groups_marked_bypass(session_factory, tmp_path):
    from app.contexts.agent.nodes.cluster import ClusterNode
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.sarif import fingerprint

    findings = [{
        "engine": "osv", "rule_id": "GHSA-1", "cwe": None, "severity": "",
        "file_path": "requirements.txt", "line_start": None, "line_end": None,
        "message": "jinja2", "source_to_sink": None, "code_snippet": None,
        "fingerprint": fingerprint("osv", "GHSA-1", "requirements.txt#jinja2", None, None),
        "raw": {"dependency_name": "jinja2"},
    }]
    async with session_factory() as session:
        ctx, task, run, inp = await _seed_discovery_task(
            session, tmp_path,
            [("semgrep", "skipped"), ("gitleaks", "skipped"), ("osv", "completed")],
            findings,
        )
        out = await ClusterNode().execute(ctx, inp)
        assert out["group_count"] == 1
        assert out["bypass_count"] == 1
        assert out["groups_by_grade"].get("bypass") == 1
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        assert groups[0].status == "adjudicated"
        assert groups[0].ai_verdict == "bypass"  # 直报，不进 triage
        assert groups[0].clue_grade is None


@pytest.mark.asyncio
async def test_cluster_uses_profile_languages(session_factory, tmp_path):
    """画像只有 nodejs 时不应索引 Python 文件。"""
    from app.contexts.agent.contracts import ClusterInput, ProfileHandoff, SourceHandoff
    from app.contexts.agent.contracts.outputs import LanguageFact
    from app.contexts.agent.nodes.cluster import ClusterNode
    from app.contexts.finding.context_extractor import load_index

    async with session_factory() as session:
        ctx, task, run, _inp = await _seed_discovery_task(
            session, tmp_path,
            [("semgrep", "completed"), ("gitleaks", "skipped"), ("osv", "skipped")],
            [],
        )
        repo = tmp_path / "repo"
        (repo / "app.js").write_text("function jsFn() { return 1; }\n", encoding="utf-8")
        inp = ClusterInput(
            source=SourceHandoff(project_path=str(repo), repo_dirname="repo"),
            host_workdir=str(tmp_path),
            source_path=str(repo),
            scans=[],
            profile=ProfileHandoff(languages=[LanguageFact(id="nodejs")]),
        )
        out = await ClusterNode().execute(ctx, inp)
        assert out["index_languages"] == ["javascript", "typescript"]
        symbols = {e["symbol"] for e in load_index(str(tmp_path))}
        assert "jsFn" in symbols
        assert "handler" not in symbols  # seed 的 python 未索引


def test_should_downgrade_engine_set_membership():
    """降权例外按 engine_set 全集判定：合并组含 gitleaks 成员即不降权。"""
    from app.contexts.finding.clustering import should_downgrade

    assert should_downgrade("tests/x.py", "CWE-89", "semgrep") is True
    assert should_downgrade("tests/x.py", "CWE-89", ["semgrep"]) is True
    # gitleaks 不在首位也必须享例外（合并组）
    assert should_downgrade("tests/x.py", "CWE-89", ["semgrep", "gitleaks"]) is False
    assert should_downgrade("tests/x.py", "CWE-89", ["gitleaks"]) is False
    # 集合外 CWE 不因路径降权
    assert should_downgrade("tests/x.py", "CWE-918", ["semgrep"]) is False


def test_should_skip_llm_engine_set_membership():
    """triage 跳过判定同样按全集：gitleaks 合并组不被误跳。"""

    class _G:
        clue_grade = "A"
        file_path = "tests/x.py"
        cwe = "CWE-89"
        engine_set = ["semgrep", "gitleaks"]

    from app.contexts.agent.nodes.triage.queue import should_skip_llm

    assert should_skip_llm(_G()) is False

    class _G2(_G):
        engine_set = ["semgrep"]

    assert should_skip_llm(_G2()) is True


def test_should_skip_llm_only_a_b():
    from app.contexts.agent.nodes.triage.queue import should_skip_llm

    class _G:
        file_path = "app/x.py"
        cwe = "CWE-89"
        engine_set = ["semgrep"]

    class A(_G):
        clue_grade = "A"

    class B(_G):
        clue_grade = "B"

    class F(_G):
        clue_grade = "F"

    class NoneGrade(_G):
        clue_grade = None

    assert should_skip_llm(A()) is False
    assert should_skip_llm(B()) is False
    assert should_skip_llm(F()) is True
    assert should_skip_llm(NoneGrade()) is True


@pytest.mark.asyncio
async def test_cluster_ignores_api_hunt_findings(session_factory, tmp_path):
    """cluster 只汇入三扫描；同 locus 的 api_hunt 不得并入扫描组。"""
    from app.contexts.agent.nodes.cluster import ClusterNode
    from app.contexts.finding.models import AlertGroup
    from app.contexts.finding.sarif import fingerprint

    findings = [
        {
            "engine": "semgrep", "rule_id": "python.sqli", "cwe": "CWE-89",
            "severity": "error", "file_path": "app.py", "line_start": 2, "line_end": 2,
            "message": "sqli", "source_to_sink": None, "code_snippet": None,
            "fingerprint": fingerprint("semgrep", "python.sqli", "app.py", 2, "CWE-89"),
            "raw": {},
        },
        {
            "engine": "api_hunt", "rule_id": "missing_ownership_check", "cwe": "CWE-639",
            "severity": "warning", "file_path": "app.py", "line_start": 2, "line_end": 2,
            "message": "hunt", "source_to_sink": None, "code_snippet": None,
            "fingerprint": fingerprint(
                "api_hunt", "missing_ownership_check|ep1", "app.py", 2, "CWE-639",
            ),
            "raw": {"endpoint_id": "ep1"},
        },
    ]
    async with session_factory() as session:
        ctx, task, _run, inp = await _seed_discovery_task(
            session, tmp_path,
            [("semgrep", "completed"), ("gitleaks", "skipped"), ("osv", "skipped")],
            findings,
        )
        out = await ClusterNode().execute(ctx, inp)
        assert out["finding_count"] == 1
        assert out["group_count"] == 1
        assert out["groups_by_engine"].get("semgrep") == 1
        assert "api_hunt" not in out["groups_by_engine"]
        groups = (await session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task.id)
        )).scalars().all()
        assert len(groups) == 1
        assert groups[0].engine_set == ["semgrep"]


def test_scan_review_groups_excludes_hunt():
    from app.contexts.agent.nodes.triage.queue import scan_review_groups

    class _G:
        def __init__(self, engines):
            self.engine_set = engines

    kept = scan_review_groups([_G(["semgrep"]), _G(["api_hunt"]), _G(["semgrep", "api_hunt"])])
    assert len(kept) == 1
    assert kept[0].engine_set == ["semgrep"]
