"""WP2 · 扫描节点与 SARIF+ 归一化测试(discovery-spec §6.1 / §8.2)。

覆盖：三引擎 fixture 归一化、codeFlows→source_to_sink、gitleaks 命中原文、
OSV 可读摘要、RawFinding 幂等、引擎失败隔离(ScanRun=failed 但节点完成)、不适用 skip。
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


# ---------- semgrep SARIF 归一化 ----------

SEMGREP_SARIF_WITH_FLOWS = {
    "runs": [{
        "tool": {"driver": {"rules": [{
            "id": "python.sqlalchemy.security.aiohttp Unsafely-influenced Query (sql-injection)",
            "shortDescription": {"text": "sql injection via sqlalchemy"},
            "properties": {"tags": ["security", "CWE-89"], "cwe": ["CWE-89: SQL Injection"]},
        }]}},
        "results": [{
            "ruleId": "python.sqlalchemy.security.aiohttp Unsafely-influenced Query (sql-injection)",
            "level": "error",
            "message": {"text": "User data flows into query"},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": "app/db.py"},
                    "region": {"startLine": 42, "endLine": 42, "snippet": {"text": "session.execute(q)"}},
                }
            }],
            "codeFlows": [{
                "threadFlows": [{
                    "locations": [
                        {"location": {"physicalLocation": {"artifactLocation": {"uri": "app/api.py"}, "region": {"startLine": 10}}, "message": {"text": "request param"}}},
                        {"location": {"physicalLocation": {"artifactLocation": {"uri": "app/db.py"}, "region": {"startLine": 42}}, "message": {"text": "execute"}}},
                    ]
                }]
            }],
        }],
    }]
}


def test_semgrep_normalize_with_codeflows():
    from app.contexts.finding.sarif import normalize_semgrep

    findings = normalize_semgrep(SEMGREP_SARIF_WITH_FLOWS)
    assert len(findings) == 1
    f = findings[0]
    assert f["cwe"] == "CWE-89"
    assert f["file_path"] == "app/db.py"
    assert f["line_start"] == 42
    # codeFlows → source_to_sink 非空(带文件:行)
    assert f["source_to_sink"] and len(f["source_to_sink"]) == 2
    assert f["source_to_sink"][0].startswith("app/api.py:10")
    assert f["fingerprint"]
    assert f["raw"]["has_dataflow"] is True
    assert f["raw"]["confidence"] == "UNKNOWN"  # fixture 未带 metadata.confidence
    assert f["raw"]["category"] == "security"


def test_semgrep_normalize_without_codeflows_gives_none():
    from app.contexts.finding.sarif import normalize_semgrep

    no_flows = {"runs": [{"results": [{
        "ruleId": "python.hardcoded-password",
        "level": "warning",
        "message": {"text": "hardcoded password"},
        "locations": [{"physicalLocation": {
            "artifactLocation": {"uri": "a.py"},
            "region": {"startLine": 3, "snippet": {"text": "pw = 'x'"}},
        }}],
    }]}]}
    f = normalize_semgrep(no_flows)[0]
    assert f["source_to_sink"] is None
    assert f["code_snippet"]
    assert f["raw"]["has_dataflow"] is False
    assert f["raw"]["confidence"] == "UNKNOWN"


def test_semgrep_normalize_reads_confidence_and_category():
    from app.contexts.finding.sarif import normalize_semgrep

    sarif = {
        "runs": [{
            "tool": {"driver": {"rules": [{
                "id": "py.low",
                "properties": {
                    "confidence": "LOW",
                    "category": "best-practice",
                    "tags": ["best-practice"],
                },
            }]}},
            "results": [{
                "ruleId": "py.low",
                "level": "note",
                "message": {"text": "style"},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": "a.py"},
                    "region": {"startLine": 1},
                }}],
            }],
        }]
    }
    f = normalize_semgrep(sarif)[0]
    assert f["raw"]["confidence"] == "LOW"
    assert f["raw"]["category"] == "best-practice"
    assert f["raw"]["has_dataflow"] is False


# ---------- gitleaks SARIF 归一化（线索台保留原文） ----------

GITLEAKS_SARIF = {
    "runs": [{
        "tool": {"driver": {"rules": [{
            "id": "aws-access-token-id",
            "name": "AWS Access Key ID",
            "shortDescription": {"text": "AWS Access Key ID"},
        }]}},
        "results": [{
            "ruleId": "aws-access-token-id",
            "level": "error",
            "message": {"text": "aws-access-token-id has detected secret for file config/prod.env."},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": "config/prod.env"},
                "region": {"startLine": 8, "snippet": {"text": "AKIAIOSFODNN7EXAMPLE"}},
            }}],
            "partialFingerprints": {
                "commitSha": "abc123def",
                "author": "alice",
                "email": "alice@example.com",
                "date": "2024-01-02T03:04:05Z",
            },
        }],
    }]
}


def test_gitleaks_keeps_secret_in_snippet():
    from app.contexts.finding.sarif import normalize_gitleaks

    f = normalize_gitleaks(GITLEAKS_SARIF)[0]
    assert f["cwe"] == "CWE-798"
    assert f["file_path"] == "config/prod.env"
    assert "AKIAIOSFODNN7EXAMPLE" in (f["code_snippet"] or "")
    assert "REDACTED" not in (f["code_snippet"] or "")
    assert "config/prod.env:8" in f["message"]
    assert "AWS Access Key ID" in f["message"]
    assert f["raw"]["rule_class"] == "known"
    assert f["raw"]["description"] == "AWS Access Key ID"
    assert f["raw"]["commit"] == "abc123def"
    assert f["raw"]["author"] == "alice"


def test_gitleaks_generic_rule_class_and_entropy():
    from app.contexts.finding.sarif import normalize_gitleaks

    sarif = {
        "runs": [{"results": [{
            "ruleId": "generic-api-key",
            "level": "error",
            "message": {"text": "key=abc"},
            "properties": {"entropy": 4.2},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": "x.py"},
                "region": {"startLine": 1},
            }}],
        }]}]
    }
    f = normalize_gitleaks(sarif)[0]
    assert f["raw"]["rule_class"] == "generic"
    assert f["raw"]["entropy"] == 4.2


def test_redact_private_key_block_and_context_password():
    from app.contexts.finding.sarif import redact_secrets

    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAACAYEAfaketext1234567890\n-----END RSA PRIVATE KEY-----"
    out = redact_secrets(f"leak: {key}")
    assert "faketext1234567890" not in out
    assert "BEGIN" in out

    out2 = redact_secrets("password = 'supersecretvalue12345'")
    assert "supersecretvalue12345" not in out2
    assert "password" in out2


# ---------- osv JSON 归一化 ----------

OSV_JSON = {
    "results": [{
        "source": {"type": "lockfile", "path": "/repo/requirements.txt"},
        "packages": [{
            "package": {"name": "jinja2", "version": "2.11.3"},
            "vulnerabilities": [
                {"id": "GHSA-7ww5-4wqc-8m2g", "aliases": ["CVE-2024-22195"],
                 "summary": "JinjaXSS", "severity": [{"type": "CVSS_V3", "score": 5.4}]},
                {"id": "PYSEC-2021-663", "aliases": [], "summary": "injection"},
            ],
        }],
    }]
}


def test_osv_normalize_maps_dependencies():
    from app.contexts.finding.sarif import normalize_osv

    findings = normalize_osv(OSV_JSON)
    assert len(findings) == 2
    by_rule = {f["rule_id"]: f for f in findings}
    assert "GHSA-7ww5-4wqc-8m2g" in by_rule
    f = by_rule["GHSA-7ww5-4wqc-8m2g"]
    assert f["engine"] == "osv"
    assert f["file_path"] == "/repo/requirements.txt"
    assert f["cwe"] is None  # 依赖情报直报，不占 CWE
    assert f["raw"]["dependency_name"] == "jinja2"
    assert "CVE-2024-22195" in f["message"]
    assert "jinja2" in f["message"]
    assert "依赖漏洞" in f["message"] or "JinjaXSS" in f["message"]
    assert f["severity"] == "warning"  # 5.4 → medium → SARIF warning
    assert len(f["severity"] or "") <= 20
    assert f["raw"]["called"] is None  # fixture 无 call analysis
    assert f["raw"]["summary"] == "JinjaXSS"
    assert f["code_snippet"] and "jinja2 2.11.3" in f["code_snippet"]
    assert "https://osv.dev/vulnerability/GHSA-7ww5-4wqc-8m2g" in f["code_snippet"]
    # 同一依赖两条漏洞 fingerprint 不同
    assert len({f["fingerprint"] for f in findings}) == 2


def test_osv_missing_summary_still_human_readable():
    from app.contexts.finding.sarif import normalize_osv

    report = {
        "results": [{
            "source": {"path": "/abs/workspace/repo/go.mod"},
            "packages": [{
                "package": {"name": "github.com/foo/bar", "version": "1.0.0", "ecosystem": "Go"},
                "vulnerabilities": [{
                    "id": "GO-2021-0053",
                    "aliases": ["CVE-2021-3121"],
                    "details": "A panic can be triggered by malformed input.\n\nSee advisory.",
                    "affected": [{
                        "package": {"name": "github.com/foo/bar", "ecosystem": "Go"},
                        "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.3.2"}]}],
                    }],
                    "database_specific": {"cwe_ids": ["CWE-20"]},
                    "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"}],
                }],
            }],
        }],
    }
    f = normalize_osv(report)[0]
    assert "github.com/foo/bar" in f["message"]
    assert "CVE-2021-3121" in f["message"]
    assert "REDACTED" not in f["message"]
    assert f["raw"]["ecosystem"] == "Go"
    assert f["raw"]["fixed_versions"] == ["1.3.2"]
    assert f["cwe"] is None  # 依赖情报直报，不占 CWE 聚类
    assert f["raw"].get("cwe") == "CWE-20"
    assert f["code_snippet"]
    assert "修复版本" in f["code_snippet"]
    assert "https://osv.dev/vulnerability/GO-2021-0053" in f["code_snippet"]
    assert f["raw"]["cvss_score"] == 7.5


def test_osv_normalize_called_and_unimportant():
    from app.contexts.finding.sarif import normalize_osv

    report = {
        "results": [{
            "source": {"path": "go.mod"},
            "packages": [{
                "package": {"name": "github.com/x/y", "version": "1.0.0"},
                "vulnerabilities": [{
                    "id": "GO-2021-0053",
                    "aliases": ["GHSA-c3h9-896r-86jm"],
                    "summary": "proto",
                    "database_specific": {"unimportant": True},
                    "severity": [{"type": "CVSS_V3", "score": 7.5}],
                }],
                "groups": [{
                    "ids": ["GO-2021-0053", "GHSA-c3h9-896r-86jm"],
                    "experimentalAnalysis": {"GO-2021-0053": {"called": False}},
                }],
            }],
        }],
    }
    f = normalize_osv(report)[0]
    assert f["raw"]["called"] is False
    assert f["raw"]["unimportant"] is True


@pytest.mark.parametrize(
    "score,expected",
    [
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H", "error"),  # 禅道落库炸库的向量，≈7.5 High
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "error"),  # 9.8 Critical
        ("CVSS:4.0/AV:L/AC:L/AT:N/PR:N/UI:P/VC:N/VI:N/VA:N/SC:L/SI:L/SA:N", "note"),
        (5.4, "warning"),
        ("9.8", "error"),
        ("LOW", "note"),
    ],
)
def test_osv_severity_fits_varchar20(score, expected):
    """OSV 的 score 常是 CVSS 向量（远超 varchar(20)），必须映射成 error/warning/note。"""
    from app.contexts.finding.sarif import normalize_osv

    report = {
        "results": [{
            "source": {"path": "composer.lock"},
            "packages": [{
                "package": {"name": "phpoffice/phpspreadsheet", "version": "1.8.2"},
                "vulnerabilities": [{
                    "id": "GHSA-2mrg-gjxq-2gvr",
                    "aliases": ["CVE-2026-59932"],
                    "summary": "gzip bomb",
                    "severity": [{"type": "CVSS_V3", "score": score}],
                }],
            }],
        }],
    }
    f = normalize_osv(report)[0]
    assert f["severity"] == expected
    assert len(f["severity"] or "") <= 20
    assert f["raw"].get("cvss") == score


# ---------- 节点行为 ----------

async def _make_ctx(session, tmp_path):
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.task.models import Task, TaskRun

    (tmp_path / "repo").mkdir(exist_ok=True)
    task = Task(project_address="x", task_type="discovery",
                vulnerability_description=None, owner_id="u1", status="running")
    session.add(task)
    await session.flush()
    run = TaskRun(task_id=task.id, status="running")
    session.add(run)
    await session.flush()
    ctx = NodeContext(
        task_id=task.id, run_id=run.id, host_workdir=str(tmp_path),
        source_path=str(tmp_path / "repo"),
        vulnerability_description="", project_address="x", project_ref=None,
        db_session=session, node_run_id="nr-fake-1",
    )
    return ctx, task, run


def _gitleaks_input(tmp_path):
    from app.contexts.agent.contracts import ScanGitleaksInput, SourceHandoff

    return ScanGitleaksInput(
        source=SourceHandoff(
            commit_sha="abc", repo_dirname="repo",
            project_path=str(tmp_path / "repo"), source_path=str(tmp_path / "repo"),
        ),
        host_workdir=str(tmp_path), source_path=str(tmp_path / "repo"),
    )


@pytest.mark.asyncio
async def test_engine_failure_isolated_node_completes(session_factory, tmp_path):
    """引擎子进程失败 → ScanRun=failed、节点输出 status=failed、不抛异常(失败隔离)。"""
    from app.contexts.agent.nodes.scan import GitleaksNode
    from app.contexts.discovery.models import ScanRun
    from unittest.mock import patch

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        node = GitleaksNode()
        from app.contexts.agent.nodes.scan.base import EngineScanError

        async def boom(*a, **k):
            raise EngineScanError("gitleaks 超时(600s)")

        with patch.object(node, "_run_subprocess", new=boom):
            out = await node.execute(ctx, _gitleaks_input(tmp_path))
        assert out["engine"] == "gitleaks"
        assert out["status"] == "failed"
        assert out["finding_count"] == 0
        assert out["error"] and "超时" in out["error"]
        scan_runs = (await session.execute(
            select(ScanRun).where(ScanRun.run_id == run.id)
        )).scalars().all()
        assert len(scan_runs) == 1
        assert scan_runs[0].status == "failed"
        assert "超时" in (scan_runs[0].error or "")


@pytest.mark.asyncio
async def test_scan_subprocess_nonzero_keeps_stderr():
    """非成功退出码必须把 stderr 带进 EngineScanError，不能只报退出码。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.contexts.agent.nodes.scan.base import EngineScanError, EngineScanNode

    class _Dummy(EngineScanNode):
        engine = "gitleaks"

        def success_exit_codes(self) -> tuple[int, ...]:
            return (0,)

        def timeout_seconds(self, settings) -> int:
            return 30

    node = _Dummy()
    proc = MagicMock()
    proc.returncode = 2
    proc.communicate = AsyncMock(return_value=(b"", b"secret scanner crashed: db locked\n"))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    settings = SimpleNamespace(scanner_output_max_bytes=1024 * 1024)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        with pytest.raises(EngineScanError, match="db locked"):
            await node._run_subprocess(["gitleaks"], "/tmp", settings)


@pytest.mark.asyncio
async def test_semgrep_exit_1_is_success_with_findings():
    """semgrep 退出码 1 = 有 findings，不得当引擎失败。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.contexts.agent.nodes.scan.semgrep import SemgrepNode

    node = SemgrepNode()
    proc = MagicMock()
    proc.returncode = 1
    proc.communicate = AsyncMock(return_value=(b'{"runs":[]}', b""))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    settings = SimpleNamespace(scanner_output_max_bytes=1024 * 1024, scanner_semgrep_timeout_seconds=60)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        out = await node._run_subprocess(["semgrep"], "/tmp", settings)
    assert out == '{"runs":[]}'


@pytest.mark.asyncio
async def test_disabled_engine_marks_scanrun_skipped(session_factory, tmp_path):
    from app.contexts.agent.nodes.scan import OsvScanNode
    from app.contexts.discovery.models import ScanRun
    from unittest.mock import MagicMock, patch

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        node = OsvScanNode()
        settings = MagicMock(scanner_osv_enabled=False)
        from app.contexts.agent.contracts import ScanOsvInput, SourceHandoff

        inp = ScanOsvInput(
            source=SourceHandoff(project_path=str(tmp_path / "repo")),
            host_workdir=str(tmp_path), source_path=str(tmp_path / "repo"),
        )
        with patch("app.core.config.get_settings", return_value=settings):
            out = await node.execute(ctx, inp)
        assert out["status"] == "skipped"
        assert out["finding_count"] == 0
        scan_run = (await session.execute(
            select(ScanRun).where(ScanRun.run_id == run.id)
        )).scalars().one()
        assert scan_run.status == "skipped"


@pytest.mark.asyncio
async def test_scan_node_rerun_idempotent_findings(session_factory, tmp_path):
    """同一任务重跑引擎：RawFinding 按 (task_id, fingerprint) 不重复。"""
    from app.contexts.agent.nodes.scan import GitleaksNode
    from app.contexts.finding.models import RawFinding
    from unittest.mock import patch

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        node = GitleaksNode()
        sarif = '{"runs":[{"results":[{"ruleId":"aws-access-token-id","level":"error","message":{"text":"AWS: AKIAIOSFODNN7EXAMPLE"},"locations":[{"physicalLocation":{"artifactLocation":{"uri":"config/prod.env"},"region":{"startLine":8}}}]}]}]}'

        async def fake_run(argv, cwd, settings, **kwargs):
            return sarif

        with patch.object(node, "_run_subprocess", new=fake_run):
            out1 = await node.execute(ctx, _gitleaks_input(tmp_path))
            await session.commit()
            out2 = await node.execute(ctx, _gitleaks_input(tmp_path))
            await session.commit()

        assert out1["status"] == "completed" and out1["finding_count"] == 1
        assert out2["finding_count"] == 1
        findings = (await session.execute(
            select(RawFinding).where(RawFinding.task_id == task.id)
        )).scalars().all()
        assert len(findings) == 1  # 重跑不重复
        assert findings[0].engine == "gitleaks"
        assert "AKIAIOSFODNN7EXAMPLE" in (findings[0].code_snippet or findings[0].message)


@pytest.mark.asyncio
async def test_semgrep_node_uses_profile_configs(session_factory, tmp_path):
    """semgrep 只跑 profile.semgrep_configs，命令包含 --dataflow-traces。"""
    from unittest.mock import MagicMock, patch

    from app.contexts.agent.nodes.scan import SemgrepNode

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        node = SemgrepNode()
        settings = MagicMock(
            scanner_semgrep_enabled=True, scanner_output_max_bytes=1024 * 1024,
            scanner_semgrep_rules_dir=str(tmp_path / "rules"),
            scanner_auto_install=False,
        )
        from app.contexts.agent.contracts import ScanSemgrepInput, SourceHandoff
        from app.contexts.agent.contracts.outputs import ProfileHandoff

        (tmp_path / "rules" / "python").mkdir(parents=True)
        inp = ScanSemgrepInput(
            source=SourceHandoff(project_path=str(tmp_path / "repo")),
            profile=ProfileHandoff(is_web=True, semgrep_configs=["p/python", "p/trailofbits"]),
            host_workdir=str(tmp_path), source_path=str(tmp_path / "repo"),
        )
        with patch.object(node, "_binary", return_value="semgrep"):
            argv = node.build_command(ctx, inp, settings)
        assert argv[0] == "semgrep"
        assert "--oss-only" in argv
        assert "--dataflow-traces" in argv
        assert not any(str(a).startswith("p/") for a in argv)
        python_root = str(tmp_path / "rules" / "python")
        assert python_root in argv
        # 社区 python + 仓库 overlay/python（若存在）
        assert argv.count("--config") >= 1
        summary = node.config_summary(ctx, inp, settings)
        assert summary["configs"] == ["python"]
        assert summary["oss_only"] is True
        assert "overlay_configs" in summary
        assert any(
            ("/crucible/" in p.replace("\\", "/") or p.replace("\\", "/").endswith("/crucible/python"))
            and p.endswith("python")
            for p in summary["overlay_configs"]
        )
        for overlay in summary["overlay_configs"]:
            assert overlay in argv


def _osv_input(tmp_path):
    from app.contexts.agent.contracts import ScanOsvInput, SourceHandoff

    return ScanOsvInput(
        source=SourceHandoff(project_path=str(tmp_path / "repo")),
        host_workdir=str(tmp_path), source_path=str(tmp_path / "repo"),
    )


@pytest.mark.asyncio
async def test_osv_command_uses_v2_format_json(session_factory, tmp_path):
    """锁定 osv-scanner 2.x：--json 已删除，必须 scan --format=json。"""
    from unittest.mock import MagicMock, patch

    from app.contexts.agent.nodes.scan import OsvScanNode

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        node = OsvScanNode()
        settings = MagicMock()
        with patch.object(node, "_binary", return_value="/opt/osv-scanner"):
            argv = node.build_command(ctx, _osv_input(tmp_path), settings)
        assert "--json" not in argv
        assert "-json" not in argv
        assert argv[:4] == ["/opt/osv-scanner", "scan", "--format=json", "-r"]
        assert argv[-1] == str(tmp_path / "repo")


@pytest.mark.asyncio
async def test_gitleaks_command_does_not_redact(session_factory, tmp_path):
    """线索台要看命中原文；--redact 会把 SARIF snippet 写成 REDACTED。"""
    from unittest.mock import MagicMock, patch

    from app.contexts.agent.nodes.scan import GitleaksNode

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        node = GitleaksNode()
        settings = MagicMock()
        with patch.object(node, "_binary", return_value="/opt/gitleaks"):
            argv = node.build_command(ctx, _gitleaks_input(tmp_path), settings)
        assert "--redact" not in argv
        assert "detect" in argv


@pytest.mark.asyncio
async def test_scan_node_emits_phase_events(session_factory, tmp_path):
    """扫描基座必须写过程事件，事件流才能按新节点过滤(discovery-spec §6.1)。"""
    from app.contexts.agent.nodes.scan import GitleaksNode, OsvScanNode
    from app.contexts.agent.nodes.scan.base import EngineScanError
    from unittest.mock import MagicMock, patch

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        events: list[dict] = []
        ctx.on_event = events.append

        skipped = OsvScanNode()
        settings = MagicMock(scanner_osv_enabled=False)
        from app.contexts.agent.contracts import ScanOsvInput, SourceHandoff

        inp = ScanOsvInput(
            source=SourceHandoff(project_path=str(tmp_path / "repo")),
            host_workdir=str(tmp_path), source_path=str(tmp_path / "repo"),
        )
        with patch("app.core.config.get_settings", return_value=settings):
            await skipped.execute(ctx, inp)
        assert any(e.get("type") == "phase.updated" and "跳过" in str(e.get("message")) for e in events)
        assert events[-1]["phase"] == "scan_osv"

        events.clear()
        node = GitleaksNode()
        sarif = '{"runs":[{"results":[{"ruleId":"aws-access-token-id","level":"error","message":{"text":"AWS: x"},"locations":[{"physicalLocation":{"artifactLocation":{"uri":"a.env"},"region":{"startLine":1}}}]}]}]}'

        async def fake_run(*a, **k):
            return sarif

        with patch.object(node, "_run_subprocess", new=fake_run):
            await node.execute(ctx, _gitleaks_input(tmp_path))
        messages = [e.get("message") for e in events if e.get("type") == "phase.updated"]
        assert any("超时上限" in str(m) for m in messages)
        assert any("git 全历史" in str(m) or "files 模式" in str(m) for m in messages)
        assert any("启动" in str(m) and "gitleaks" in str(m) for m in messages)
        assert any("解析输出" in str(m) for m in messages)
        assert any("入库" in str(m) for m in messages)
        assert any("命中" in str(m) for m in messages)
        assert events[0]["phase"] == "scan_gitleaks"

        events.clear()

        async def boom(*a, **k):
            raise EngineScanError("gitleaks 超时(600s)")

        with patch.object(node, "_run_subprocess", new=boom):
            await node.execute(ctx, _gitleaks_input(tmp_path))
        assert any("失败" in str(e.get("message")) for e in events)


@pytest.mark.asyncio
async def test_scan_subprocess_emits_progress_ticks():
    """长跑 subprocess 每 tick 回调 on_tick，填满事件流空白。"""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.contexts.agent.nodes.scan import base as scan_base
    from app.contexts.agent.nodes.scan.base import EngineScanNode

    class _Dummy(EngineScanNode):
        engine = "semgrep"
        node_key = "scan_semgrep"

        def success_exit_codes(self) -> tuple[int, ...]:
            return (0, 1)

        def timeout_seconds(self, settings) -> int:
            return 120

    node = _Dummy()
    ticks: list[int] = []
    tick = 0.15

    async def slow_communicate():
        await asyncio.sleep(tick + 0.05)
        return (b'{"runs":[]}', b"")

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = slow_communicate
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    settings = SimpleNamespace(scanner_output_max_bytes=1024 * 1024)

    with (
        patch.object(scan_base, "SCAN_PROGRESS_TICK_SECONDS", tick),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
    ):
        out = await node._run_subprocess(
            ["semgrep"], "/tmp", settings, on_tick=ticks.append,
        )
    assert out == '{"runs":[]}'
    assert ticks, "长跑应至少触发一次心跳"
    assert ticks[0] >= int(tick)


@pytest.mark.asyncio
async def test_semgrep_start_summary_lists_configs(session_factory, tmp_path):
    """semgrep 启动摘要应带规则包列表，事件流可读。"""
    from unittest.mock import MagicMock, patch

    from app.contexts.agent.contracts import ScanSemgrepInput, SourceHandoff
    from app.contexts.agent.contracts.outputs import ProfileHandoff
    from app.contexts.agent.nodes.scan import SemgrepNode

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        events: list[dict] = []
        ctx.on_event = events.append
        node = SemgrepNode()
        (tmp_path / "rules" / "python").mkdir(parents=True)
        settings = MagicMock(
            scanner_semgrep_enabled=True,
            scanner_output_max_bytes=1024 * 1024,
            scanner_semgrep_timeout_seconds=1200,
            scanner_semgrep_rules_dir=str(tmp_path / "rules"),
            scanner_auto_install=False,
        )
        inp = ScanSemgrepInput(
            source=SourceHandoff(project_path=str(tmp_path / "repo")),
            profile=ProfileHandoff(is_web=True, semgrep_configs=["python", "javascript"]),
            host_workdir=str(tmp_path), source_path=str(tmp_path / "repo"),
        )

        async def fake_run(*a, **k):
            return '{"runs":[]}'

        with (
            patch("app.core.config.get_settings", return_value=settings),
            patch.object(node, "_binary", return_value="semgrep"),
            patch.object(node, "_run_subprocess", new=fake_run),
        ):
            # javascript 目录缺失会在 build_command 炸；只造 python
            (tmp_path / "rules" / "javascript").mkdir(parents=True, exist_ok=True)
            await node.execute(ctx, inp)

        messages = [str(e.get("message")) for e in events if e.get("type") == "phase.updated"]
        assert any("规则包" in m and "python" in m for m in messages)
        assert any("启动 semgrep" in m for m in messages)
        assert any("解析输出" in m for m in messages)
        assert any("命中" in m for m in messages)


@pytest.mark.asyncio
async def test_build_command_failure_isolated(session_factory, tmp_path):
    """命令构造失败(本地规则缺失等)同样走失败隔离：ScanRun=failed，节点仍完成。"""
    from app.contexts.agent.nodes.scan import SemgrepNode
    from app.contexts.agent.nodes.scan.base import EngineScanError
    from app.contexts.discovery.models import ScanRun
    from unittest.mock import patch

    from app.contexts.agent.contracts import ScanSemgrepInput, SourceHandoff
    from app.contexts.agent.contracts.outputs import ProfileHandoff

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        node = SemgrepNode()
        inp = ScanSemgrepInput(
            source=SourceHandoff(
                commit_sha="abc", repo_dirname="repo",
                project_path=str(tmp_path / "repo"), source_path=str(tmp_path / "repo"),
            ),
            profile=ProfileHandoff(is_web=True, semgrep_configs=["python"]),
            host_workdir=str(tmp_path), source_path=str(tmp_path / "repo"),
        )

        def boom(self, ctx, inp, settings):
            raise EngineScanError("semgrep 本地规则目录缺失")

        with patch.object(SemgrepNode, "build_command", boom):
            out = await node.execute(ctx, inp)
        assert out["status"] == "failed"
        assert "规则目录缺失" in (out.get("error") or "")
        scan_runs = (await session.execute(
            select(ScanRun).where(ScanRun.run_id == run.id)
        )).scalars().all()
        assert len(scan_runs) == 1
        assert scan_runs[0].status == "failed"  # 不悬挂在 running


@pytest.mark.asyncio
async def test_build_command_unexpected_error_isolated(session_factory, tmp_path):
    """构造期意外异常也不得悬挂 ScanRun（仍失败隔离，节点 completed）。"""
    from app.contexts.agent.nodes.scan import GitleaksNode
    from app.contexts.discovery.models import ScanRun
    from unittest.mock import patch

    async with session_factory() as session:
        ctx, task, run = await _make_ctx(session, tmp_path)
        node = GitleaksNode()

        def boom(self, ctx, inp, settings):
            raise KeyError("unexpected")

        with patch.object(GitleaksNode, "build_command", boom):
            out = await node.execute(ctx, _gitleaks_input(tmp_path))
        assert out["status"] == "failed"
        scan_runs = (await session.execute(
            select(ScanRun).where(ScanRun.run_id == run.id)
        )).scalars().all()
        assert scan_runs[0].status == "failed"


@pytest.mark.asyncio
async def test_scanner_cancellation_kills_the_whole_process_group():
    """取消扫描节点时不仅杀扫描器主进程，也要杀其派生进程。"""
    import asyncio
    import signal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.contexts.agent.nodes.scan.base import EngineScanNode

    class _Dummy(EngineScanNode):
        engine = "semgrep"

        def timeout_seconds(self, settings) -> int:
            return 60

    never = asyncio.Event()

    async def communicate():
        await never.wait()
        return b"", b""

    proc = MagicMock()
    proc.pid = 4321
    proc.communicate = communicate
    proc.wait = AsyncMock()
    settings = SimpleNamespace(scanner_output_max_bytes=1024)

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as create,
        patch("app.contexts.agent.nodes.scan.base.os.killpg") as killpg,
    ):
        running = asyncio.create_task(_Dummy()._run_subprocess(["semgrep"], "/tmp", settings))
        await asyncio.sleep(0)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    assert create.await_args.kwargs["start_new_session"] is True
    killpg.assert_called_once_with(4321, signal.SIGKILL)


def test_worker_sigterm_registry_kills_all_active_scanner_groups():
    import signal
    from unittest.mock import patch

    from app.contexts.agent.nodes.scan import base

    base._ACTIVE_SCANNER_PROCESS_GROUPS.update({101, 202})
    with patch.object(base.os, "killpg") as killpg:
        assert base.kill_all_active_scanner_processes() == 2

    assert {call.args for call in killpg.call_args_list} == {
        (101, signal.SIGKILL),
        (202, signal.SIGKILL),
    }
    assert base._ACTIVE_SCANNER_PROCESS_GROUPS == set()


@pytest.mark.asyncio
async def test_scan_subprocess_cancel_check_kills_early():
    """取消探测命中即杀进程并抛 ScanCancelledError，不跑满超时窗口才被丢弃。"""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    import pytest

    from app.contexts.agent.nodes.scan import base as scan_base
    from app.contexts.agent.nodes.scan.base import EngineScanNode, ScanCancelledError

    class _Dummy(EngineScanNode):
        engine = "gitleaks"
        node_key = "scan_gitleaks"

        def timeout_seconds(self, settings) -> int:
            return 60

    node = _Dummy()
    ticks: list[int] = []
    tick = 0.1

    async def never_finishes():
        await asyncio.sleep(30)
        return (b"", b"")

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = never_finishes
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    settings = SimpleNamespace(scanner_output_max_bytes=1024 * 1024)

    async def cancelled():
        return True

    with (
        patch.object(scan_base, "SCAN_PROGRESS_TICK_SECONDS", tick),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
    ):
        with pytest.raises(ScanCancelledError):
            await node._run_subprocess(
                ["gitleaks"], "/tmp", settings, on_tick=ticks.append,
                stop_check=cancelled,
            )
    proc.kill.assert_called()
