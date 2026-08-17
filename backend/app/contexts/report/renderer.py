"""报告渲染:8 节 Markdown 字符串加平台标题后拼接。"""
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


def render_report_md(report_data: dict[str, Any]) -> str:
    """把 report_data 的 8 节 Markdown 正文拼成带 ## N. 标题的导出文档。"""
    data = report_data or {}
    parts: list[str] = []
    for i, (key, title) in enumerate(zip(REPORT_SECTION_KEYS, REPORT_SECTION_TITLES, strict=True), start=1):
        body = data.get(key)
        if not isinstance(body, str) or not body.strip():
            body = "—"
        parts.append(f"## {i}. {title}\n\n{body.strip()}\n")
    return "\n".join(parts)
