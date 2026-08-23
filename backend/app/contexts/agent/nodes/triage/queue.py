"""队列：priority → 评分表覆盖 → severity → member_count。模型只处理队头。

跳过 LLM：grade=F，或攻击面降权（should_downgrade）；不以 priority=low 一刀切
（note/info 的 low 仍可二审）。
"""
from __future__ import annotations

from collections.abc import Callable

from app.contexts.finding.clustering import should_downgrade
from app.contexts.finding.hypothesis import RUBRIC_COVERED_CWES

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2, None: 3}
_SEVERITY_RANK = {"error": 0, "warning": 1, None: 2, "": 2, "note": 3, "info": 3}


def order_groups(
    groups,
    *,
    severity_of: Callable | None = None,
) -> list:
    """排序键：priority 高 → 首发 CWE 评分表覆盖 → 引擎 severity 高 → member_count 大。

    severity_of(group) → 代表 finding 的 severity；缺省读 g.rep_severity。
    """
    def _sev(g) -> str:
        if severity_of is not None:
            return (severity_of(g) or "").lower()
        return (getattr(g, "rep_severity", None) or "").lower()

    return sorted(
        groups,
        key=lambda g: (
            _PRIORITY_RANK.get(g.priority, 3),
            0 if (g.cwe or "") in RUBRIC_COVERED_CWES else 1,
            _SEVERITY_RANK.get(_sev(g), 2),
            -(g.member_count or 1),
        ),
    )


def should_skip_llm(group) -> bool:
    """跳过 LLM：F 级 / 攻击面降权组 → needs_review。"""
    if (getattr(group, "clue_grade", None) or "") == "F":
        return True
    return should_downgrade(
        getattr(group, "file_path", None) or "",
        getattr(group, "cwe", None),
        getattr(group, "engine_set", None) or [],
    )
