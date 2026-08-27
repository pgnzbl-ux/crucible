"""线索叙事（discovery-spec §2.3.1）：统一 summary / reasoning。"""
from __future__ import annotations

from typing import Any


def _nonempty_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def narrative_from_agent(output: dict[str, Any]) -> tuple[str | None, str | None]:
    """从 triage / api_hunt 输出提取叙事；缺省返回 (None, None)。"""
    return _nonempty_str(output.get("summary")), _nonempty_str(output.get("reasoning"))


def narrative_from_why(why: list[str] | None, *, fallback: str = "") -> tuple[str, str]:
    """级联/快审无独立叙事时，用 why 合成最低可用简述与推理。"""
    bullets = [str(x).strip() for x in (why or []) if str(x).strip()]
    if bullets:
        summary = bullets[0][:500]
        reasoning = "\n".join(f"- {b}" for b in bullets)[:8000]
        return summary, reasoning
    text = (fallback or "（暂无叙事）").strip() or "（暂无叙事）"
    return text[:500], text[:8000]


def osv_template_narrative(
    *,
    message: str,
    raw: dict[str, Any] | None,
    file_path: str = "",
    rule_id: str = "",
) -> tuple[str, str]:
    """OSV bypass 确定性模板叙事（不进 T3）。"""
    raw = raw if isinstance(raw, dict) else {}
    dep = str(raw.get("dependency_name") or raw.get("package") or "").strip()
    version = str(raw.get("version") or raw.get("dependency_version") or "").strip()
    vuln_id = str(raw.get("rule_id") or raw.get("vuln_id") or raw.get("id") or rule_id or "").strip()
    severity = str(raw.get("severity_label") or raw.get("severity") or "").strip()
    called = raw.get("called")
    fixed = raw.get("fixed_versions")
    fixed_s = "、".join(str(x) for x in fixed) if isinstance(fixed, list) and fixed else ""
    summary_bits = [
        "依赖组件存在已知漏洞" + (f"（{vuln_id}）" if vuln_id else ""),
    ]
    if dep:
        summary_bits.append(f"组件 {dep}" + (f" {version}" if version else ""))
    if severity:
        summary_bits.append(f"严重度 {severity}")
    summary = "；".join(summary_bits) + "。"
    if message and message.strip() and message.strip() not in summary:
        summary = f"{summary} {message.strip()[:300]}"

    called_txt = (
        "本仓库调用分析显示已调用受影响 API（called=true）。"
        if called is True
        else "本仓库调用分析未确认调用受影响 API（called≠true）；默认不入终认，仅作依赖情报。"
        if called is False
        else "本扫描未提供调用分析（called 未知）；默认不入终认，仅作依赖情报。"
    )
    reasoning_parts = [
        f"定位：{file_path or '（锁文件/清单）'}。",
        called_txt,
    ]
    if fixed_s:
        reasoning_parts.append(f"建议升级至修复版本：{fixed_s}。")
    advisory = str(raw.get("osv_url") or raw.get("advisory_url") or "").strip()
    if not advisory and vuln_id:
        advisory = f"https://osv.dev/vulnerability/{vuln_id}"
    if advisory:
        reasoning_parts.append(f"公告：{advisory}。")
    return summary[:800], "\n".join(reasoning_parts)[:8000]
