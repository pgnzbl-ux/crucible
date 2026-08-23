"""HypothesisPack — 所有 AI 工位唯一线索包结构(discovery-spec §2.3)。

Phase 1 不建新表：由 AlertGroup + 代表 RawFinding + 切片即时组装。
没有 HypothesisPack(或等价人工描述)不得调用 LLM(§2.1 硬规则)。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CWE_NAMES = {
    "CWE-89": "SQL 注入",
    "CWE-78": "命令注入",
    "CWE-79": "跨站脚本(XSS)",
    "CWE-22": "路径穿越",
    "CWE-798": "硬编码密钥",
    "CWE-502": "反序列化",
    "CWE-918": "SSRF",
    "CWE-611": "XXE",
    "CWE-863": "越权",
    "CWE-601": "不安全重定向",
}

# 首发 CWE 评分表覆盖集(discovery-spec §2.4/§6.4)：triage 队列排序与 dispatch 主线索
# 选择共用的单一事实来源；新增评分表时只改 CWE_NAMES 这一处。
RUBRIC_COVERED_CWES: frozenset[str] = frozenset(CWE_NAMES)


class Locus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_path: str
    function_symbol: str | None = None
    line_span: str | None = None


class Slice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str  # 如 "sink 函数 app/db.py handler"
    text: str   # 带行号前缀的源码；禁止含仓库绝对路径


class HypothesisPack(BaseModel):
    model_config = ConfigDict(extra="ignore")

    locus: Locus
    hypothesis_class: str  # CWE-xxx；未知则不得调用轻量裁决
    support: str = "engine"  # engine | human；Phase 1 引擎路径只会出现 engine
    source_to_sink: list[str] = Field(default_factory=list)  # 非空才可能 grade=A
    slices: list[Slice] = Field(default_factory=list)
    closed_question: str
    grade: Literal["A", "B", "F"] = "B"  # C 只存在于 verify 任务的人工描述


def closed_question_for(cwe: str | None) -> str | None:
    """封闭问题：缺 CWE 拒收(§2.3 F 级)，禁止开放式提问。"""
    if not cwe:
        return None
    name = CWE_NAMES.get(cwe, "该类漏洞")
    return f"给定切片与数据流，此处是否存在可利用的{cwe}（{name}）？只回答这一问。"


def build_pack(
    *,
    group,
    representative,
    slices: list[Slice],
) -> HypothesisPack | None:
    """从 AlertGroup + 代表 RawFinding 组装；不满足最低条件返回 None(不调 LLM)。

    - grade=F(无 locus 且无 CWE) → None；
    - 无 CWE → None(未知类不做轻量裁决)。
    """
    cwe = group.cwe
    question = closed_question_for(cwe)
    if question is None:
        return None
    grade = (getattr(group, "clue_grade", None) or "B")
    if grade == "F":
        return None
    if not (group.file_path or "").strip():
        return None
    return HypothesisPack(
        locus=Locus(
            file_path=group.file_path,
            function_symbol=group.function_symbol,
            line_span=group.line_span,
        ),
        hypothesis_class=cwe or "",
        source_to_sink=list(representative.source_to_sink or []),
        slices=slices,
        closed_question=question,
        grade=grade,  # type: ignore[arg-type]
    )


LEAD_DESCRIPTION_TEMPLATE = """【疑似漏洞】{cwe_name}（{cwe}）
【位置】{file_path} L{line_span}，函数 {function_symbol}
【数据流】{source_to_sink}
【命中代码】
{code_snippet}
【AI 二审意见】{why}
请按白盒方法验证此告警是否为真实可利用漏洞；推演不通即判误报。"""


def build_lead_description(*, group, representative, adjudication) -> str:
    """终认线索描述模板：只进定位+切片+二审意见；**不含** rule_id/message(§2.2 反锚定)。

    属 finding 领域（复核台人工派发与 dispatch 共用），供 agent.dispatch 与
    finding.api 两侧调用。
    """
    flow = "\n".join(representative.source_to_sink or []) or "无（引擎未给出数据流）"
    why = "\n".join(f"- {w}" for w in ((adjudication.why if adjudication else None) or [])) or "- 无"
    snippet = (representative.code_snippet or "").strip() or "（见位置行段）"
    return LEAD_DESCRIPTION_TEMPLATE.format(
        cwe_name=CWE_NAMES.get(group.cwe or "", "疑似漏洞"),
        cwe=group.cwe or "CWE-?",
        file_path=group.file_path,
        line_span=group.line_span or "?",
        function_symbol=group.function_symbol or "未知",
        source_to_sink=flow,
        code_snippet=snippet,
        why=why,
    )
