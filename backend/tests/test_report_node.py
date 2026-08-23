"""节点 5 是唯一文档作者：始终跑 AI，final_verdict 不得漂移。"""
import sys
import os
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.report import ReportNode
from app.contexts.agent.tasks import report_columns_from_orch_result
from tests.test_ai_runner import (
    _confirmed_ok,
    _md_sections,
    _not_reproduced_ok,
    _poc_ok,
    _record_sections,
)


def _ctx(**prev):
    return NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs=prev,
    )


@pytest.mark.asyncio
async def test_report_node_always_runs_ai_for_confirmed():
    repro = _confirmed_ok()
    fake = AsyncMock(return_value={
        "report_data": _md_sections(product_intro="由 report 撰写"),
        "final_verdict": "confirmed",
        "cvss": repro["cvss"],
        "vulnerable_file": repro["vulnerable_file"],
    })
    with patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", fake):
        out = await ReportNode().execute(_ctx(reproduce=repro, audit={"gate_verdict": "pass"}))
    fake.assert_awaited_once()
    node_input = fake.await_args.kwargs["input_json"]
    assert node_input["expected_verdict"] == "confirmed"
    assert node_input["document_kind"] == "vulnerability_report"
    assert "report_data" not in (node_input.get("reproduce") or {})
    assert out["authored_by"] == "reporter"
    assert out["final_verdict"] == "confirmed"
    assert out["report_data"]["product_intro"] == "由 report 撰写"


@pytest.mark.asyncio
async def test_report_node_rejects_verdict_drift():
    fake = AsyncMock(return_value={
        "report_data": _md_sections(),
        "final_verdict": "confirmed",
        "cvss": {
            "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
            "base_score": 9.8,
            "severity": "Critical",
        },
    })
    with patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", fake):
        with pytest.raises(RuntimeError, match="verdict 漂移"):
            await ReportNode().execute(_ctx(
                reproduce=_not_reproduced_ok(),
                audit={"gate_verdict": "pass"},
            ))


@pytest.mark.asyncio
async def test_report_node_overwrites_poc_commands_from_reproduce():
    repro = _confirmed_ok(poc=_poc_ok(code="print('FROM_REPRO')\n"))
    fake = AsyncMock(return_value={
        "report_data": _md_sections(poc_commands="```bash\ncurl rewritten\n```"),
        "final_verdict": "confirmed",
        "cvss": repro["cvss"],
        "vulnerable_file": repro["vulnerable_file"],
    })
    with patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", fake):
        out = await ReportNode().execute(_ctx(reproduce=repro, audit={"gate_verdict": "pass"}))
    assert "FROM_REPRO" in out["report_data"]["poc_commands"]
    assert "curl rewritten" not in out["report_data"]["poc_commands"]
    assert out["poc"]["filename"] == "poc.py"


@pytest.mark.asyncio
async def test_report_node_runs_ai_when_reproduce_skipped():
    fake = AsyncMock(return_value={
        "report_data": _record_sections(),
        "final_verdict": "false_positive",
    })
    with patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", fake):
        out = await ReportNode().execute(_ctx(reproduce={}, audit={"gate_verdict": "fail"}))
    fake.assert_awaited_once()
    assert fake.await_args.kwargs["node_key"] == "report"
    assert fake.await_args.kwargs["input_json"]["document_kind"] == "verification_record"
    assert out["authored_by"] == "reporter"
    assert out["final_verdict"] == "false_positive"


@pytest.mark.asyncio
async def test_report_node_runs_ai_for_uncertain_audit():
    """audit uncertain 无 reproduce → report 仍撰写 needs_review 验证记录。"""
    fake = AsyncMock(return_value={
        "report_data": _record_sections(),
        "final_verdict": "needs_review",
    })
    with patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", fake):
        out = await ReportNode().execute(_ctx(reproduce={}, audit={"gate_verdict": "uncertain"}))
    fake.assert_awaited_once()
    node_input = fake.await_args.kwargs["input_json"]
    assert node_input["expected_verdict"] == "needs_review"
    assert node_input["document_kind"] == "verification_record"
    assert out["final_verdict"] == "needs_review"
    assert "poc" not in out


def test_report_columns_from_orch_result_confirmed():
    cols = report_columns_from_orch_result({
        "verdict": "confirmed",
        "report_data": _md_sections(product_intro="产品X介绍" * 20),
        "cvss": {"base_score": 9.8, "severity": "Critical"},
        "vulnerable_file": "app/login.py",
        "poc": {
            "language": "python",
            "filename": "poc.py",
            "code": "print('x')\n",
            "usage": "python poc.py --url http://x",
        },
    })
    assert cols["cvss_score"] == 9.8
    assert cols["severity"] == "Critical"
    assert cols["vulnerable_file"] == "app/login.py"
    assert cols["summary"].startswith("产品X")
    assert len(cols["summary"]) <= 500
    assert cols["title"].startswith("漏洞验证报告")
    assert cols["poc_language"] == "python"
    assert cols["poc_filename"] == "poc.py"
    assert cols["poc_code"] == "print('x')\n"
    assert cols["poc_usage"] == "python poc.py --url http://x"


def test_report_columns_drop_cvss_for_not_reproduced():
    cols = report_columns_from_orch_result({
        "verdict": "not_reproduced",
        "report_data": _record_sections(product_intro="产品Y"),
        "cvss": {"base_score": 8.9, "severity": "Critical"},
        "vulnerable_file": "server/agent.routes.ts",
        "poc": {"language": "python", "filename": "poc.py", "code": "print(1)", "usage": "x"},
    })
    assert cols["cvss_score"] is None
    assert cols["severity"] is None
    assert cols["title"].startswith("漏洞验证记录")
    assert cols["summary"] == "产品Y"
    assert cols["poc_language"] is None
    assert cols["poc_code"] is None


def test_report_columns_use_audit_title_for_discovery_aggregate():
    cols = report_columns_from_orch_result({
        "verdict": None,
        "report_data": {
            "document_kind": "code_audit_report",
            "product_intro": "仓库代码审计报告",
        },
    })
    assert cols["title"] == "代码审计报告"
