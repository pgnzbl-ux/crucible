"""独立漏洞报告组装（discovery-spec §11.1）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.contexts.finding.models import Adjudication, AlertGroup, LeadRun, RawFinding
from app.contexts.finding.narrative import narrative_from_why


_SUCCESS_VERDICTS = frozenset({"confirmed", "partial", "code_reachable"})


def is_vuln_report_verdict(verdict: str | None) -> bool:
    return verdict in _SUCCESS_VERDICTS


def build_vuln_report(
    *,
    group: AlertGroup,
    lead: LeadRun,
    representative: RawFinding | None,
    adjudication: Adjudication | None,
    verification_basis: str,
) -> dict[str, Any]:
    """组装一漏洞一份报告 JSON；章节齐全，修复建议可占位。"""
    why = list(adjudication.why) if adjudication and adjudication.why else []
    evidence = list(adjudication.evidence) if adjudication and adjudication.evidence else []
    summary = (adjudication.summary if adjudication else None) or None
    reasoning = (adjudication.reasoning if adjudication else None) or None
    if not summary or not reasoning:
        syn_s, syn_r = narrative_from_why(why, fallback=lead.lead_description or group.file_path)
        summary = summary or syn_s
        reasoning = reasoning or syn_r

    engines = list(group.engine_set or [])
    if representative and representative.engine and representative.engine not in engines:
        engines = [representative.engine, *engines]

    rem = None
    if isinstance(lead.reproduce_output, dict):
        rem = lead.reproduce_output.get("remediation") or lead.reproduce_output.get("fix")
    if not rem and isinstance(lead.audit_output, dict):
        rem = lead.audit_output.get("remediation") or lead.audit_output.get("fix")
    remediation = str(rem).strip() if rem else "暂缺"

    locus = {
        "file_path": group.file_path,
        "function_symbol": group.function_symbol,
        "line_span": group.line_span,
        "cwe": group.cwe,
    }
    if representative:
        locus.update({
            "rule_id": representative.rule_id,
            "line_start": representative.line_start,
            "line_end": representative.line_end,
            "message": (representative.message or "")[:500],
        })

    final_verdict = lead.verdict or group.resolution
    return {
        "schema_version": 1,
        "document_kind": "vulnerability_report",
        "alert_group_id": group.id,
        "lead_run_id": lead.id,
        "task_id": group.task_id,
        "summary": summary,
        "reasoning": reasoning,
        "locus": locus,
        "evidence": evidence,
        "why": why,
        "engines": engines,
        "primary_engine": engines[0] if engines else None,
        "adjudication": {
            "verdict": adjudication.verdict if adjudication else group.ai_verdict,
            "confidence": adjudication.confidence if adjudication else group.ai_confidence,
            "verdict_source": group.verdict_source,
            "summary": summary,
            "reasoning": reasoning,
        } if adjudication or group.ai_verdict else None,
        "final_verdict": final_verdict,
        "verification_basis": verification_basis,
        "remediation": remediation,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def vuln_report_to_markdown(report: dict[str, Any]) -> str:
    """导出用 Markdown。"""
    basis = report.get("verification_basis")
    basis_label = "靶场验证" if basis == "lab" else "代码闭环" if basis == "code_path" else (basis or "—")
    locus = report.get("locus") if isinstance(report.get("locus"), dict) else {}
    lines = [
        f"# {report.get('summary') or '漏洞报告'}",
        "",
        "## 简述",
        str(report.get("summary") or "—"),
        "",
        "## 代码/依赖推理",
        str(report.get("reasoning") or "—"),
        "",
        "## 定位与证据",
        f"- 文件：`{locus.get('file_path') or '—'}`",
        f"- 函数：{locus.get('function_symbol') or '—'}",
        f"- 行：{locus.get('line_span') or locus.get('line_start') or '—'}",
        f"- CWE：{locus.get('cwe') or '—'}",
    ]
    evidence = report.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        lines.append("- 证据：")
        for item in evidence[:20]:
            if isinstance(item, dict):
                lines.append(f"  - `{item.get('file') or ''}` {item.get('lines') or ''}".rstrip())
            else:
                lines.append(f"  - {item}")
    lines.extend([
        "",
        "## 来源引擎",
        ", ".join(str(e) for e in (report.get("engines") or [])) or "—",
        "",
        "## 二审/叙事结论",
    ])
    adj = report.get("adjudication") if isinstance(report.get("adjudication"), dict) else {}
    lines.append(f"- 判决：{adj.get('verdict') or '—'}")
    lines.append(f"- 置信度：{adj.get('confidence') if adj.get('confidence') is not None else '—'}")
    lines.extend([
        "",
        "## 终认结论",
        str(report.get("final_verdict") or "—"),
        "",
        "## 验证方式",
        basis_label,
        "",
        "## 修复建议",
        str(report.get("remediation") or "暂缺"),
        "",
    ])
    return "\n".join(lines)
