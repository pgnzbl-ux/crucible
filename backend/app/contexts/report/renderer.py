"""报告渲染:按 document_kind 选标题与 8 节，加平台标题后拼接。"""
from __future__ import annotations

from typing import Any

REPORT_SECTION_KEYS = (
    "product_intro", "vulnerability", "impact", "details",
    "reproduction", "poc_commands", "fix_suggestions", "reporting_decision",
)
REPORT_SECTION_TITLES = (
    "产品介绍", "漏洞描述", "影响范围", "漏洞详情",
    "漏洞复现", "POC", "修复建议", "报送判定",
)
RECORD_SECTION_KEYS = (
    "product_intro", "claimed_issue", "whitebox_analysis", "test_record",
    "blocker", "observed_facts", "remaining_conditions", "reporting_decision",
)
RECORD_SECTION_TITLES = (
    "产品介绍", "声称问题", "白盒分析", "测试记录",
    "阻断原因", "已观察事实", "未满足条件", "报送判定",
)


def render_report_md(report_data: dict[str, Any]) -> str:
    """把 report_data 的 8 节 Markdown 正文拼成带标题的导出文档。

    缺 document_kind 的旧数据按漏洞报告 8 节渲染。
    """
    data = report_data or {}
    if data.get("document_kind") == "verification_record":
        heading = "# 漏洞验证记录"
        keys, titles = RECORD_SECTION_KEYS, RECORD_SECTION_TITLES
    else:
        heading = "# 漏洞验证报告"
        keys, titles = REPORT_SECTION_KEYS, REPORT_SECTION_TITLES
    parts: list[str] = [f"{heading}\n"]
    for i, (key, title) in enumerate(zip(keys, titles, strict=True), start=1):
        body = data.get(key)
        if not isinstance(body, str) or not body.strip():
            body = "—"
        parts.append(f"## {i}. {title}\n\n{body.strip()}\n")
    return "\n".join(parts)
