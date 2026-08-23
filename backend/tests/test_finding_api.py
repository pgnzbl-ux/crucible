"""WP6 · finding 复核台 API 测试(discovery-spec §9.1 / §4.4 惰性对账)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import patch

from app.shared.base import Base


@pytest_asyncio.fixture
async def client_env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from app.main import create_app
    from app.core.database import get_db_session

    app = create_app()

    async def _override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as c:
        yield c, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_review_env(factory):
    """用户 + discovery 任务 + 一组 adjudicated tp 组(带代表 finding/判决)。"""
    import hashlib

    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import Adjudication, AlertGroup, LeadRun, RawFinding
    from app.contexts.identity.models import User
    from app.contexts.settings.models import LlmProvider
    from app.contexts.task.models import Task, TaskRun

    async with factory() as s:
        s.add(User(id="u1", email="u1@x.test", password_hash="x", role="analyst", display_name="U1"))
        s.add(LlmProvider(
            name="test", provider_type="custom", base_url="http://llm.test",
            api_key_encrypted="k", model="test-model", is_default=True,
        ))
        task = Task(
            project_address="https://github.com/a/b.git", task_type="discovery",
            vulnerability_description=None, owner_id="u1", status="running",
        )
        s.add(task)
        await s.flush()
        run = TaskRun(task_id=task.id, status="running")
        s.add(run)
        await s.flush()
        sr = ScanRun(task_id=task.id, run_id=run.id, node_run_id="nr", engine="semgrep",
                     status="completed", config_summary={})
        s.add(sr)
        await s.flush()
        f = RawFinding(
            task_id=task.id, scan_run_id=sr.id, engine="semgrep", rule_id="python.sqli",
            cwe="CWE-89", severity="error", file_path="app/db.py", line_start=42,
            line_end=42, message="sqli", source_to_sink=["a.py:1 (x)"],
            code_snippet="42\tq = 'SELECT ' + u", fingerprint=hashlib.sha256(b"k").hexdigest(),
            raw={},
        )
        s.add(f)
        await s.flush()
        g = AlertGroup(
            task_id=task.id, group_key="gk1", cwe="CWE-89", file_path="app/db.py",
            function_symbol="handler", line_span="40-50", member_count=1,
            representative_finding_id=f.id, engine_set=["semgrep"],
            status="adjudicated", clue_grade="A", ai_verdict="tp",
            ai_confidence=0.9, priority="high",
        )
        s.add(g)
        await s.flush()
        s.add(LeadRun(
            task_id=task.id, run_id=run.id, alert_group_id=g.id,
            queue_position=0, lead_description="SQL injection hypothesis",
            status="completed", verdict="code_reachable", gate_verdict="pass",
        ))
        s.add(Adjudication(
            alert_group_id=g.id, attempt=1, verdict="tp", confidence=0.9,
            why=["拼接"], evidence=[], need=[], context_log=[],
            prompt_text="p", response_text="r", usage={},
        ))
        await s.commit()
        return g.id, task.id


def _auth(client) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token('u1', 'u1@x.test')}"}


def _auth_as(user_id: str) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id, f'{user_id}@x.test')}"}


def test_list_groups_filters(client_env):
    client, factory = client_env
    import asyncio

    group_id, task_id = asyncio.run(_seed_review_env(factory))
    resp = client.get("/api/v1/findings/groups", headers=_auth(client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["cwe"] == "CWE-89"
    assert data["items"][0]["clue_grade"] == "A"
    assert data["items"][0]["project_address"] == "https://github.com/a/b.git"
    assert data["items"][0]["vulnerability_title"] == "SQL 注入"
    assert data["items"][0]["representative_rule_id"] == "python.sqli"
    assert data["items"][0]["primary_engine"] == "semgrep"
    assert data["items"][0]["screening_status"] == "retained"
    assert data["items"][0]["screening_summary"] == "AI 初筛保留"
    assert data["items"][0]["screening_reasons"] == ["拼接"]

    resp2 = client.get(
        "/api/v1/findings/groups", headers=_auth(client),
        params={"ai_verdict": "fp"},
    )
    assert resp2.json()["total"] == 0


def test_list_groups_queue_scopes_separate_focus_review_processing_and_noise(client_env):
    client, factory = client_env
    import asyncio
    import hashlib

    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding

    _group_id, task_id = asyncio.run(_seed_review_env(factory))

    async def _arrange():
        async with factory() as s:
            scan_run = (await s.execute(
                select(ScanRun).where(ScanRun.task_id == task_id)
            )).scalars().first()
            specs = [
                ("noise", "adjudicated", "B", "fp", "medium"),
                ("review", "needs_review", "B", None, "high"),
                ("processing", "clustered", "B", None, "medium"),
            ]
            for index, (name, status, grade, verdict, priority) in enumerate(specs, start=1):
                finding = RawFinding(
                    task_id=task_id, scan_run_id=scan_run.id, engine="semgrep",
                    rule_id=f"custom.{name}", cwe="CWE-20", severity="warning",
                    file_path=f"app/{name}.py", line_start=index, line_end=index,
                    message=name, source_to_sink=None, code_snippet=None,
                    fingerprint=hashlib.sha256(name.encode()).hexdigest(), raw={},
                )
                s.add(finding)
                await s.flush()
                s.add(AlertGroup(
                    task_id=task_id, group_key=f"gk-{name}", cwe="CWE-20",
                    file_path=finding.file_path, line_span=f"{index}-{index}",
                    member_count=1, representative_finding_id=finding.id,
                    engine_set=["semgrep"], status=status, clue_grade=grade,
                    ai_verdict=verdict, priority=priority,
                ))
            await s.commit()

    asyncio.run(_arrange())

    expected = {"focus": 1, "review": 1, "processing": 1, "noise": 1, "all": 4}
    for scope, count in expected.items():
        response = client.get(
            "/api/v1/findings/groups", headers=_auth(client), params={"scope": scope},
        )
        assert response.status_code == 200
        assert response.json()["total"] == count

    stats = client.get("/api/v1/findings/stats", headers=_auth(client)).json()
    assert stats["by_queue"] == {
        "focus": 1, "review": 1, "processing": 1, "noise": 1,
    }


def test_list_groups_engine_filter_matches_exact_json_element(client_env):
    """engine 筛选在 json 列上按元素精确匹配："osv" 不能命中 "osv-scanner"。"""
    client, factory = client_env
    import asyncio
    import hashlib

    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import AlertGroup, RawFinding

    _group_id, task_id = asyncio.run(_seed_review_env(factory))

    async def _arrange():
        async with factory() as s:
            scan_run = (await s.execute(
                select(ScanRun).where(ScanRun.task_id == task_id)
            )).scalars().first()
            for index, engines in enumerate([["osv"], ["osv-scanner", "semgrep"]], start=1):
                finding = RawFinding(
                    task_id=task_id, scan_run_id=scan_run.id, engine=engines[0],
                    rule_id=f"custom.{'-'.join(engines)}", cwe="CWE-20", severity="warning",
                    file_path=f"app/eng{index}.py", line_start=index, line_end=index,
                    message="eng", source_to_sink=None, code_snippet=None,
                    fingerprint=hashlib.sha256(f"eng{index}".encode()).hexdigest(), raw={},
                )
                s.add(finding)
                await s.flush()
                s.add(AlertGroup(
                    task_id=task_id, group_key=f"gk-eng{index}", cwe="CWE-20",
                    file_path=finding.file_path, line_span=f"{index}-{index}",
                    member_count=1, representative_finding_id=finding.id,
                    engine_set=engines, status="clustered", clue_grade="B",
                ))
            await s.commit()

    asyncio.run(_arrange())

    expected = {"osv": 1, "osv-scanner": 1, "semgrep": 2}
    for engine, count in expected.items():
        response = client.get(
            "/api/v1/findings/groups", headers=_auth(client), params={"engine": engine},
        )
        assert response.status_code == 200, engine
        assert response.json()["total"] == count, engine


def test_list_groups_exposes_inferred_cwe_for_legacy_group_without_overwriting_raw_finding(client_env):
    client, factory = client_env
    import asyncio

    from app.contexts.finding.models import AlertGroup, RawFinding

    group_id, _task_id = asyncio.run(_seed_review_env(factory))

    async def _arrange():
        async with factory() as s:
            group = await s.get(AlertGroup, group_id)
            finding = await s.get(RawFinding, group.representative_finding_id)
            group.cwe = None
            finding.cwe = None
            finding.rule_id = "python.lang.security.audit.subprocess-shell-true"
            finding.message = "subprocess called with shell=True"
            await s.commit()

    asyncio.run(_arrange())
    response = client.get(
        "/api/v1/findings/groups", headers=_auth(client), params={"scope": "all"},
    )
    item = response.json()["items"][0]
    assert item["cwe"] == "CWE-78"
    assert item["cwe_source"] == "inferred"
    assert item["vulnerability_title"] == "命令注入"


def test_finding_stats_are_owner_scoped(client_env):
    client, factory = client_env
    import asyncio

    asyncio.run(_seed_review_env(factory))
    own = client.get("/api/v1/findings/stats", headers=_auth(client))
    assert own.status_code == 200
    assert own.json()["total"] == 1
    assert own.json()["by_status"] == {"adjudicated": 1}

    other = client.get("/api/v1/findings/stats", headers=_auth_as("u2"))
    assert other.status_code == 200
    assert other.json()["total"] == 0


def test_list_groups_task_filter_still_requires_owner(client_env):
    """指定 task_id 只能缩小 owner 范围，不能绕过 owner 隔离。"""
    client, factory = client_env
    import asyncio

    _group_id, task_id = asyncio.run(_seed_review_env(factory))
    resp = client.get(
        "/api/v1/findings/groups",
        headers=_auth_as("u2"),
        params={"task_id": task_id},
    )
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "items": []}


def test_group_detail_lazy_reconcile(client_env):
    """惰性对账(§4.4)：dispatched 组 + 关联 Task 已 confirmed → 读取时当场回写。"""
    import asyncio

    from app.contexts.finding.models import AlertGroup
    from app.contexts.task.models import Task

    client, factory = client_env
    group_id, task_id = asyncio.run(_seed_review_env(factory))

    async def _arrange():
        async with factory() as s:
            g = await s.get(AlertGroup, group_id)
            g.status = "dispatched"
            t = await s.get(Task, task_id)
            t.source_alert_group_id = group_id
            t.status, t.verdict = "completed", "confirmed"
            await s.commit()

    asyncio.run(_arrange())
    resp = client.get(f"/api/v1/findings/groups/{group_id}", headers=_auth(client))
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"
    assert data["resolution"] == "confirmed"
    assert data["verification_verdict"] == "confirmed"
    assert data["adjudications"] and data["adjudications"][0]["verdict"] == "tp"
    assert data["representative"]["file_path"] == "app/db.py"
    assert data["representative"]["source_to_sink"] == ["a.py:1 (x)"]
    assert data["representative"]["code_snippet"] == "42\tq = 'SELECT ' + u"
    assert data["lead_runs"][0]["verdict"] == "code_reachable"


def test_review_actions_and_revive(client_env):
    import asyncio

    client, factory = client_env
    group_id, task_id = asyncio.run(_seed_review_env(factory))

    # 确认必须写审计理由，避免一键不可追溯终态
    confirm = client.post(
        f"/api/v1/findings/groups/{group_id}/review", headers=_auth(client),
        json={"action": "confirm"},
    )
    assert confirm.status_code == 422

    # reject 必带 reason_tags
    resp = client.post(
        f"/api/v1/findings/groups/{group_id}/review", headers=_auth(client),
        json={"action": "reject"},
    )
    assert resp.status_code == 422

    resp = client.post(
        f"/api/v1/findings/groups/{group_id}/review", headers=_auth(client),
        json={"action": "reject", "reason_tags": ["输入不可控"]},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    assert resp.json()["resolution"] == "false_positive"

    # revive 复活
    resp = client.post(f"/api/v1/findings/groups/{group_id}/revive", headers=_auth(client))
    assert resp.status_code == 200
    assert resp.json()["status"] == "needs_review"


def test_manual_dispatch_creates_verify_task(client_env):
    """人工放行：另开 verify Task(非自动路径)；溯源指针写到新任务。"""
    import asyncio

    from app.contexts.task.models import Task

    client, factory = client_env
    group_id, task_id = asyncio.run(_seed_review_env(factory))

    with (
        patch("app.core.agent_runner.agent_runner_manager.image_exists", return_value=True),
        patch("app.core.celery_app.celery_app.send_task"),
    ):
        resp = client.post(
            f"/api/v1/findings/groups/{group_id}/dispatch", headers=_auth(client),
            json={"include_engine_conclusion": True},
        )
    assert resp.status_code == 200, resp.text
    new_task_id = resp.json()["verification_task_id"]
    assert new_task_id != task_id

    async def _check():
        async with factory() as s:
            t = await s.get(Task, new_task_id)
            assert t.task_type == "verify"
            assert t.source_alert_group_id == group_id
            # 描述来自模板：含定位与数据流；勾选后含引擎线索段
            assert "app/db.py" in (t.vulnerability_description or "")
            assert "python.sqli" in (t.vulnerability_description or "")

    asyncio.run(_check())


def test_manual_dispatch_rejects_already_dispatched_group(client_env):
    """幂等守卫：dispatched 组重复投递直接 409，不创建第二个 verify 任务。"""
    import asyncio

    from app.contexts.finding.models import AlertGroup

    client, factory = client_env
    group_id, _task_id = asyncio.run(_seed_review_env(factory))

    async def _mark():
        async with factory() as s:
            group = await s.get(AlertGroup, group_id)
            group.status = "dispatched"
            await s.commit()

    asyncio.run(_mark())

    with (
        patch("app.core.agent_runner.agent_runner_manager.image_exists", return_value=True),
        patch("app.core.celery_app.celery_app.send_task") as send_task,
    ):
        resp = client.post(
            f"/api/v1/findings/groups/{group_id}/dispatch", headers=_auth(client),
            json={"include_engine_conclusion": False},
        )
    assert resp.status_code == 409
    assert "已投递" in resp.json()["error"]["message"]
    send_task.assert_not_called()
