"""api_hunt 是候选生成器：只写 RawFinding，统一进 cluster/screen/triage。"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
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


@pytest.mark.asyncio
async def test_api_hunt_persists_candidate_only(session_factory, tmp_path, monkeypatch):
    """猎洞节点不得越权建组/判决；候选由后续统一链处理。"""
    import json

    from sqlalchemy import func, select

    from app.contexts.agent.contracts import ApiHuntInput, ApiInventoryHandoff, SourceHandoff
    from app.contexts.agent.nodes.api_hunt import ApiHuntNode
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.discovery.models import ScanRun
    from app.contexts.finding.models import Adjudication, AlertGroup, RawFinding
    from app.contexts.task.models import Task, TaskRun

    bom = tmp_path / "api-bom.json"
    bom.write_text(json.dumps({"endpoints": [{
        "endpoint_id": "GET /items/{id}", "method": "GET",
        "path_template": "/items/{id}", "handler_file": "app.py",
        "handler_symbol": "get_item", "line_start": 10,
        "resource_key": "item", "is_pve": True, "has_object_id": True,
    }]}), encoding="utf-8")

    async with session_factory() as session:
        task = Task(project_address="x", task_type="discovery",
                    vulnerability_description=None, owner_id="u1", status="running")
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="running")
        session.add(run)
        await session.flush()
        ctx = NodeContext(
            task_id=task.id, run_id=run.id, host_workdir=str(tmp_path),
            source_path=str(tmp_path), vulnerability_description="",
            project_address="x", project_ref=None, db_session=session,
            node_run_id="nr-hunt",
        )
        inp = ApiHuntInput(
            source=SourceHandoff(project_path=str(tmp_path)),
            host_workdir=str(tmp_path), source_path=str(tmp_path),
            inventory=ApiInventoryHandoff(ok=True, bom_path=bom.name, endpoint_count=1),
        )
        node = ApiHuntNode()

        async def fake_batch(*args, **kwargs):
            return {"reviewed_count": 1, "suspects": [{
                "endpoint_id": "GET /items/{id}", "file_path": "app.py",
                "function_symbol": "get_item", "line_start": 10,
                "cwe": "CWE-639", "confidence": 0.75,
                "why": ["缺少对象归属校验"],
                "evidence": [{"file": "app.py", "lines": "10-20"}],
                "attacker_controlled": True, "reaches_sink": True,
                "sanitizer": "none",
            }], "budget_exhausted": False}

        monkeypatch.setattr(node, "_hunt_batch", fake_batch)
        # execute 内部局部 import，直接 patch 配置源。
        with patch("app.core.config.get_settings", return_value=type("S", (), {
            "api_hunt_enabled": True, "api_hunt_top_k": 20,
            "api_hunt_max_batches": 8,
        })()):
            out = await node.execute(ctx, inp)

        assert out["finding_count"] == 1
        assert out["candidate_count"] == 1
        assert out["scan_run_id"]
        assert out["status"] == "completed"
        assert out["candidate_state_counts"] == {"supported": 1}
        assert await session.scalar(select(func.count(RawFinding.id))) == 1
        assert await session.scalar(select(func.count(AlertGroup.id))) == 0
        assert await session.scalar(select(func.count(Adjudication.id))) == 0
        scan = (await session.execute(select(ScanRun))).scalar_one()
        assert (scan.engine, scan.status, scan.finding_count) == ("api_hunt", "completed", 1)


@pytest.mark.asyncio
async def test_api_hunt_keeps_uncertain_candidate_for_triage(
    session_factory, tmp_path, monkeypatch,
):
    """发现层不得因合格门尚未证明就丢弃可定位候选。"""
    import json

    from sqlalchemy import select

    from app.contexts.agent.contracts import ApiHuntInput, ApiInventoryHandoff, SourceHandoff
    from app.contexts.agent.nodes.api_hunt import ApiHuntNode
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.finding.models import RawFinding
    from app.contexts.task.models import Task, TaskRun

    bom = tmp_path / "api-bom-uncertain.json"
    bom.write_text(json.dumps({"endpoints": [{
        "endpoint_id": "GET /items/{id}", "method": "GET",
        "path_template": "/items/{id}", "handler_file": "app.py",
        "handler_symbol": "get_item", "line_start": 10,
        "resource_key": "item", "is_pve": True, "has_object_id": True,
    }]}), encoding="utf-8")

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
        ctx = NodeContext(
            task_id=task.id, run_id=run.id, host_workdir=str(tmp_path),
            source_path=str(tmp_path), vulnerability_description="",
            project_address="x", project_ref=None, db_session=session,
            node_run_id="nr-hunt-uncertain",
        )
        inp = ApiHuntInput(
            source=SourceHandoff(project_path=str(tmp_path)),
            host_workdir=str(tmp_path), source_path=str(tmp_path),
            inventory=ApiInventoryHandoff(
                ok=True, bom_path=bom.name, endpoint_count=1,
            ),
        )
        node = ApiHuntNode()

        async def fake_batch(*args, **kwargs):
            return {"reviewed_count": 1, "suspects": [{
                "endpoint_id": "GET /items/{id}", "file_path": "app.py",
                "function_symbol": "get_item", "line_start": 10,
                "cwe": "CWE-639", "confidence": None,
                "why": ["存在对象级读取，但调用方约束尚未确认"],
                "evidence": [{"file": "app.py", "lines": "10-20"}],
                "attacker_controlled": None, "reaches_sink": True,
                "sanitizer": "unknown",
            }], "budget_exhausted": False}

        monkeypatch.setattr(node, "_hunt_batch", fake_batch)
        with patch("app.core.config.get_settings", return_value=type("S", (), {
            "api_hunt_enabled": True, "api_hunt_top_k": 20,
            "api_hunt_max_batches": 8,
        })()):
            out = await node.execute(ctx, inp)

        assert out["candidate_count"] == 1
        assert out["candidate_state_counts"] == {"uncertain": 1}
        finding = (await session.execute(select(RawFinding))).scalar_one()
        assert finding.raw["confidence"] == "UNKNOWN"
        assert finding.raw["qualify"] == {
            "attacker_controlled": None,
            "reaches_sink": True,
            "sanitizer": "unknown",
        }


@pytest.mark.asyncio
async def test_api_hunt_skipped_still_records_scan_run(session_factory, tmp_path):
    """空跑/关闭也必须落 ScanRun，否则 cluster 无法区分「0 候选」与「没跑」。"""
    from sqlalchemy import select

    from app.contexts.agent.contracts import ApiHuntInput, ApiInventoryHandoff, SourceHandoff
    from app.contexts.agent.nodes.api_hunt import ApiHuntNode
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.discovery.models import ScanRun
    from app.contexts.task.models import Task, TaskRun

    async with session_factory() as session:
        task = Task(project_address="x", task_type="discovery",
                    vulnerability_description=None, owner_id="u1", status="running")
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="running")
        session.add(run)
        await session.flush()
        ctx = NodeContext(
            task_id=task.id, run_id=run.id, host_workdir=str(tmp_path),
            source_path=str(tmp_path), vulnerability_description="",
            project_address="x", project_ref=None, db_session=session,
            node_run_id="nr-hunt",
        )
        inp = ApiHuntInput(
            source=SourceHandoff(project_path=str(tmp_path)),
            host_workdir=str(tmp_path), source_path=str(tmp_path),
            inventory=ApiInventoryHandoff(ok=True),
        )
        with patch("app.core.config.get_settings", return_value=type("S", (), {
            "api_hunt_enabled": False, "api_hunt_top_k": 20,
            "api_hunt_max_batches": 8,
        })()):
            out = await ApiHuntNode().execute(ctx, inp)

        assert out["skipped"] is True
        scan = (await session.execute(select(ScanRun))).scalar_one()
        assert (scan.status, scan.finding_count) == ("skipped", 0)


@pytest.mark.asyncio
async def test_hunt_batch_records_usage(session_factory, tmp_path):
    """api_hunt 每批 Docker 会话必须入台账，否则任务 token 总计漏猎洞。"""
    from sqlalchemy import select

    from app.contexts.agent.nodes.api_hunt import ApiHuntNode
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.task.models import AgentUsage, Task

    async with session_factory() as session:
        task = Task(
            project_address="x",
            task_type="discovery",
            vulnerability_description=None,
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        ctx = NodeContext(
            task_id=task.id,
            run_id="r1",
            host_workdir=str(tmp_path),
            source_path=str(tmp_path),
            vulnerability_description="",
            project_address="x",
            project_ref=None,
            db_session=session,
        )
        batch = [{
            "endpoint_id": "GET /items/{id}",
            "method": "GET",
            "path_template": "/items/{id}",
            "handler_file": "app.py",
            "handler_symbol": "get_item",
            "line_start": 10,
            "id_params": ["id"],
            "auth_observed": [],
            "resource_key": "item",
            "has_object_id": True,
        }]

        async def fake_run(**kwargs):
            meta = kwargs.get("meta_out")
            assert meta is not None
            meta.update({"usage": {"prompt_tokens": 40, "completion_tokens": 8}})
            return {"suspects": [], "reviewed_count": 1}

        with patch("app.contexts.agent.ai_runner.run_ai_node", new=fake_run):
            out = await ApiHuntNode()._hunt_batch(ctx, batch, object())

        assert out["reviewed_count"] == 1
        rows = (await session.execute(select(AgentUsage))).scalars().all()
        assert len(rows) == 1
        assert rows[0].node_key == "api_hunt"
        assert rows[0].prompt_tokens == 40
        assert rows[0].completion_tokens == 8
        assert rows[0].run_id == "r1"
