"""报告渲染:report_data JSON → markdown(8 节,对齐 report_template.md)。

rendered md 可直接喂给 plugin md_to_docx.py 转 Word,
或供前端人读展示。所有可选字段缺失时给"—"占位,不崩。
"""
from __future__ import annotations

import json
from typing import Any


def _s(v: Any, default: str = "—") -> str:
    """safe str:None/空 → 占位。"""
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _code_block(content: str, lang: str = "") -> str:
    if not content:
        return ""
    return f"```{lang}\n{content}\n```\n"


def render_report_md(report_data: dict[str, Any]) -> str:
    """把节点 5 的 report_data 渲染成 8 节中文 markdown。

    report_data 字段见 spec §4.1。
    """
    intro = _s(report_data.get("product_intro"))
    vuln = report_data.get("vulnerability", {}) or {}
    cvss = vuln.get("cvss", {}) or {}
    impact = report_data.get("impact", {}) or {}
    details = report_data.get("details", {}) or {}
    repro = report_data.get("reproduction", {}) or {}
    poc_cmds = report_data.get("poc_commands", []) or []
    fixes = report_data.get("fix_suggestions", []) or []
    decision = report_data.get("reporting_decision", {}) or {}

    lines: list[str] = []
    # §1
    lines.append("## 1. 产品介绍")
    lines.append("")
    lines.append(intro)
    lines.append("")

    # §2 漏洞描述(表格)
    lines.append("## 2. 漏洞描述")
    lines.append("")
    lines.append("| 项 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 漏洞类型 | {_s(vuln.get('type'))} |")
    lines.append(f"| CVSS 3.1 | {_s(cvss.get('vector'))} (Base Score: {_s(cvss.get('base_score'))}, {_s(cvss.get('severity'))}) |")
    lines.append(f"| 漏洞文件 | `{_s(vuln.get('vulnerable_file'))}:{_s(vuln.get('vulnerable_lines'))}` |")
    lines.append(f"| 前置条件 | {_s(vuln.get('preconditions'))} |")
    lines.append(f"| 触发入口 | {_s(vuln.get('entry_point'))} |")
    lines.append(f"| 核心危害 | {_s(vuln.get('core_harm'))} |")
    lines.append(f"| 环境限制 | {_s(vuln.get('environment_constraint'))} |")
    lines.append(f"| 默认即触发 | {_s(vuln.get('trigger_default'))} |")
    lines.append("")

    # §3 影响范围
    lines.append("## 3. 影响范围")
    lines.append("")
    lines.append(f"- 受影响版本: {_s(impact.get('affected_versions'))}")
    lines.append(f"- 不受影响版本: {_s(impact.get('unaffected_versions'))}")
    lines.append(f"- 触发条件默认值: {_s(impact.get('trigger_condition_defaults'))}")
    lines.append("")

    # §4 漏洞详情
    lines.append("## 4. 漏洞详情")
    lines.append("")
    lines.append("### 4.1 代码审计分析")
    lines.append("")
    audit = details.get("audit_analysis", []) or []
    if audit:
        for item in audit:
            lines.append(f"**文件:** `{_s(item.get('file'))}:{_s(item.get('lines'))}`")
            lines.append("")
            content = item.get("content", "")
            if content:
                lines.append(_code_block(content, "python"))
            flaw = item.get("flaw_explanation", "")
            if flaw:
                lines.append(f"**缺陷分析:** {flaw}")
                lines.append("")
    else:
        lines.append("无审计发现。")
        lines.append("")

    lines.append("### 4.2 PoC 构造思路")
    lines.append("")
    poc_constr = details.get("poc_construction", {}) or {}
    lines.append(f"- 端点选择: {_s(poc_constr.get('endpoint_choice_reason'))}")
    bypass = poc_constr.get("bypass_methods", []) or []
    if bypass:
        lines.append(f"- 绕过方法: {', '.join(bypass)}")
    lines.append(f"- Payload 设计: {_s(poc_constr.get('payload_design'))}")
    lines.append(f"- 利用链: {_s(poc_constr.get('exploitation_chain'))}")
    lines.append("")

    # §5 漏洞复现
    lines.append("## 5. 漏洞复现")
    lines.append("")
    lines.append("### 5.1 环境准备  *(MUST 含 transport-shape 描述 — transport-agnostic)*")
    lines.append("")
    ts = repro.get("transport_shape", {}) or {}
    lines.append(f"- 协议: {_s(ts.get('protocol'))}")
    lines.append(f"- Listener: {_s(ts.get('listener'))}")
    lines.append(f"- TLS 终止: {_s(ts.get('tls_termination'))}")
    lines.append(f"- X-Forwarded-Proto: {_s(ts.get('x_forwarded_proto'))}")
    lines.append(f"- 通道检查: {_s(ts.get('channel_check'))}")
    lines.append(f"- 目标产品: {_s(repro.get('target_product'))}")
    lines.append(f"- 前端 URL: {_s(repro.get('frontend_url'))}")
    lines.append("")

    lines.append("### 5.2 复现步骤  *(MUST 每步骤一个截图,inline 嵌入)*")
    lines.append("")
    steps = repro.get("steps", []) or []
    for st in steps:
        n = st.get("step", "?")
        action = _s(st.get("action"))
        lines.append(f"**步骤 {n}** {action}")
        lines.append("")
        obs = st.get("observation")
        if obs:
            lines.append(f"观察: {obs}")
            lines.append("")
        shot = st.get("screenshot")
        if shot:
            alt = shot.replace("img/", "").replace(".png", "").replace("_", " ")
            lines.append(f"![{alt}]({shot})")
            lines.append("")

    lines.append("### 5.3 结果验证")
    lines.append("")
    lines.append("| 验证项 | 结果 |")
    lines.append("|---|---|")
    for rv in repro.get("result_verification", []) or []:
        lines.append(f"| {_s(rv.get('item'))} | {_s(rv.get('result'))} |")
    lines.append("")

    lines.append("### 5.4 攻击链图示")
    lines.append("")
    lines.append("```")
    lines.append(_s(repro.get("attack_chain_diagram")))
    lines.append("```")
    lines.append("")

    # §6 POC
    lines.append("## 6. POC")
    lines.append("")
    for cmd in poc_cmds:
        lines.append(_code_block(cmd, "bash").rstrip("\n"))
        lines.append("")

    # §7 修复建议
    lines.append("## 7. 修复建议")
    lines.append("")
    for fix in fixes:
        lines.append(f"### {_s(fix.get('priority'))}: {_s(fix.get('suggestion'))}")
        lines.append("")
        ce = fix.get("code_example")
        if ce:
            lines.append(_code_block(ce, "python"))
    if not fixes:
        lines.append("无。")
        lines.append("")

    # §8 报送判定
    lines.append("## 8. 报送判定（文字反馈）")
    lines.append("")
    lines.append("| 项 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 建议 | {_s(decision.get('recommendation'))} |")
    lines.append(f"| 实际危害 | {_s(decision.get('actual_harm'))} |")
    lines.append(f"| 修复优先级 | {_s(decision.get('fix_priority'))} |")
    lines.append(f"| 理由 | {_s(decision.get('reason'))} |")
    lines.append(f"| 风险描述 | {_s(decision.get('risk_description'))} |")
    lines.append("")

    return "\n".join(lines)
