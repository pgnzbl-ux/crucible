"""叙事字段与独立漏洞报告（discovery-spec §2.3.1 / §11.1）。"""
from __future__ import annotations

from app.contexts.finding.narrative import osv_template_narrative
from app.contexts.finding.vuln_report import build_vuln_report, is_vuln_report_verdict, vuln_report_to_markdown


def test_osv_template_narrative_includes_called_and_fix():
    summary, reasoning = osv_template_narrative(
        message="依赖漏洞：jinja2",
        raw={
            "dependency_name": "jinja2",
            "version": "2.11.3",
            "rule_id": "GHSA-7ww5-4wqc-8m2g",
            "severity_label": "中危",
            "called": False,
            "fixed_versions": ["3.1.4"],
            "osv_url": "https://osv.dev/vulnerability/GHSA-7ww5-4wqc-8m2g",
        },
        file_path="requirements.txt",
        rule_id="GHSA-7ww5-4wqc-8m2g",
    )
    assert "jinja2" in summary
    assert "GHSA-7ww5-4wqc-8m2g" in summary
    assert "called≠true" in reasoning or "called" in reasoning
    assert "3.1.4" in reasoning
    assert "osv.dev" in reasoning


def test_build_vuln_report_has_required_sections():
    class _G:
        id = "g1"
        task_id = "t1"
        file_path = "app.py"
        function_symbol = "handle"
        line_span = "10-20"
        cwe = "CWE-89"
        engine_set = ["semgrep"]
        ai_verdict = "tp"
        ai_confidence = 0.9
        verdict_source = "agent"
        resolution = "confirmed"

    class _L:
        id = "lr1"
        verdict = "confirmed"
        lead_description = "SQLi"
        audit_output = {"remediation": "使用参数化查询"}
        reproduce_output = None

    class _A:
        why = ["可控输入到 SQL"]
        evidence = [{"file": "app.py", "lines": "10-12"}]
        summary = "SQL 注入简述"
        reasoning = "入口→拼接→执行"
        verdict = "tp"
        confidence = 0.9

    class _R:
        engine = "semgrep"
        rule_id = "python.sql-injection"
        line_start = 10
        line_end = 12
        message = "SQL injection"

    assert is_vuln_report_verdict("confirmed")
    assert is_vuln_report_verdict("code_reachable")
    assert not is_vuln_report_verdict("false_positive")

    report = build_vuln_report(
        group=_G(), lead=_L(), representative=_R(), adjudication=_A(),
        verification_basis="code_path",
    )
    assert report["summary"] == "SQL 注入简述"
    assert report["verification_basis"] == "code_path"
    assert report["remediation"] == "使用参数化查询"
    assert report["final_verdict"] == "confirmed"
    md = vuln_report_to_markdown(report)
    assert "简述" in md
    assert "代码闭环" in md
    assert "修复建议" in md
