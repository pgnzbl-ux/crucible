"""节点 5 双模：成功路径拷贝 reproduce 报告，误报路径才跑 AI。"""
import sys
import os
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.report import ReportNode
from app.contexts.agent.tasks import report_columns_from_orch_result
from tests.test_ai_runner import _confirmed_ok, _md_sections


def _ctx(**prev):
    return NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs=prev,
    )


@pytest.mark.asyncio
async def test_report_node_copies_reproduce_without_ai():
    repro = _confirmed_ok()
    with patch(
        "app.contexts.agent.ai_runner.run_ai_node",
        AsyncMock(side_effect=AssertionError("成功路径不该跑 AI")),
    ) as mocked:
        out = await ReportNode().execute(_ctx(reproduce=repro, audit={"gate_verdict": "pass"}))
    mocked.assert_not_called()
    assert out["authored_by"] == "reproduce"
    assert out["final_verdict"] == "confirmed"
    assert out["report_data"]["product_intro"] == repro["report_data"]["product_intro"]
    assert out["cvss"]["base_score"] == 9.8


@pytest.mark.asyncio
async def test_report_node_runs_ai_when_reproduce_skipped():
    fake = AsyncMock(return_value={
        "report_data": _md_sections(),
        "final_verdict": "false_positive",
    })
    with patch("app.contexts.agent.ai_runner.run_ai_node", fake):
        out = await ReportNode().execute(_ctx(reproduce={}, audit={"gate_verdict": "fail"}))
    fake.assert_awaited_once()
    assert fake.await_args.kwargs["node_key"] == "report"
    assert out["authored_by"] == "reporter"
    assert out["final_verdict"] == "false_positive"


def test_report_columns_from_orch_result():
    cols = report_columns_from_orch_result({
        "verdict": "confirmed",
        "report_data": _md_sections(product_intro="产品X介绍" * 20),
        "cvss": {"base_score": 9.8, "severity": "Critical"},
        "vulnerable_file": "app/login.py",
    })
    assert cols["cvss_score"] == 9.8
    assert cols["severity"] == "Critical"
    assert cols["vulnerable_file"] == "app/login.py"
    assert cols["summary"].startswith("产品X")
    assert len(cols["summary"]) <= 500
