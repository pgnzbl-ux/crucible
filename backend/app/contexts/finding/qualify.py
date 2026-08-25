"""合格可疑真洞门（discovery-spec §2.7）。dispatch 与 LeadStreamer 共用。

Agent 输出不可信：why/evidence/三布尔由 schema 拒收后再过本门。
"""
from __future__ import annotations

from typing import Any

from app.contexts.finding.clustering import should_downgrade

SANITIZER_OK = frozenset({"none", "bypassable"})

# 对用户文案；禁止把 tp/fp 当标签原文
USER_VERDICT_LABELS = {
    "tp": "可疑真洞",
    "fp": "误报",
    "need_more_context": "二审未决",
    "bypass": "依赖情报",
}


def user_verdict_label(code: str | None) -> str:
    if not code:
        return "尚未研判"
    return USER_VERDICT_LABELS.get(code, "未知结论")


def qualify_fields_from_adjudication(adjudication: Any) -> dict[str, Any]:
    if adjudication is None:
        return {}
    for item in reversed(list(getattr(adjudication, "context_log", None) or [])):
        if isinstance(item, dict) and isinstance(item.get("qualify"), dict):
            return item["qualify"]
    return {}


def rejection_reason(
    group: Any,
    *,
    representative: Any = None,
    adjudication: Any = None,
    high_confidence: float = 0.8,
) -> str | None:
    """返回拒绝原因；None 表示可入终认队。"""
    if (getattr(group, "ai_verdict", None) or "") != "tp":
        return "不是可疑真洞"
    if (getattr(group, "verdict_source", None) or "") != "agent":
        return "须 T3 亲审"
    try:
        conf = float(getattr(group, "ai_confidence", None) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < high_confidence:
        return "置信不足"
    why = list(getattr(adjudication, "why", None) or [])
    if not any(str(item).strip() for item in why):
        return "缺少 why"
    evidence = list(getattr(adjudication, "evidence", None) or [])
    if not evidence:
        return "缺少 evidence"
    fields = qualify_fields_from_adjudication(adjudication)
    if fields.get("attacker_controlled") is not True:
        return "攻击者不可控"
    if fields.get("reaches_sink") is not True:
        return "未达危险点"
    if fields.get("sanitizer") not in SANITIZER_OK:
        return "消毒有效或未声明"
    engines = list(getattr(group, "engine_set", None) or [])
    if should_downgrade(
        getattr(group, "file_path", None) or "",
        getattr(group, "cwe", None),
        engines,
    ):
        return "测试/文档路径注入类"
    rep_engine = (
        getattr(representative, "engine", None)
        or (engines[0] if len(engines) == 1 else "")
    )
    if rep_engine == "osv" or engines == ["osv"]:
        raw = getattr(representative, "raw", None) if representative is not None else None
        raw = raw if isinstance(raw, dict) else {}
        if raw.get("called") is not True:
            return "OSV 未调用受影响 API"
    return None


def is_qualified_lead(
    group: Any,
    *,
    representative: Any = None,
    adjudication: Any = None,
    high_confidence: float = 0.8,
) -> bool:
    return rejection_reason(
        group,
        representative=representative,
        adjudication=adjudication,
        high_confidence=high_confidence,
    ) is None
