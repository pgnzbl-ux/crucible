"""漏洞线索工作台的初筛读模型；不改动审计状态机。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScreeningPresentation:
    status: str
    summary: str
    reasons: list[str]


def screening_presentation(group, adjudication=None) -> ScreeningPresentation:
    """把扫描/AI/终认状态翻译成审计人员能直接行动的初筛结论。"""
    why = list((getattr(adjudication, "why", None) or []))
    resolution = getattr(group, "resolution", None)
    status = getattr(group, "status", "")
    verdict = getattr(group, "ai_verdict", None)

    if resolution == "confirmed":
        return ScreeningPresentation("confirmed", "已确认漏洞", why or ["已通过人工或终认流程确认"])
    if resolution in {"false_positive", "ignored"}:
        label = "已确认误报" if resolution == "false_positive" else "已忽略"
        return ScreeningPresentation("suppressed", label, why or ["已从重点工作队列移除"])
    if status == "dispatched":
        return ScreeningPresentation("retained", "已进入终认", why or ["初筛证据达到终认条件"])
    if verdict == "tp":
        return ScreeningPresentation("retained", "AI 初筛保留", why or ["AI 初筛认为存在可利用风险"])
    if verdict == "bypass":
        return ScreeningPresentation("retained", "漏洞情报直报", why or ["依赖漏洞情报无需静态规则二审"])
    if verdict == "fp":
        return ScreeningPresentation("suppressed", "AI 初筛判为误报", why or ["AI 未发现可成立的攻击路径"])
    if verdict == "need_more_context":
        return ScreeningPresentation("review", "上下文不足，需复核", why or ["现有代码切片不足以形成可靠结论"])
    if status in {"new", "clustered"}:
        return ScreeningPresentation("processing", "等待初筛", ["扫描命中已入组，初筛尚未完成"])
    if getattr(group, "clue_grade", None) == "F":
        return ScreeningPresentation("suppressed", "规则降噪", ["缺少可定位位置或可识别漏洞类别"])
    if status == "needs_review" and getattr(group, "priority", None) == "low":
        return ScreeningPresentation("suppressed", "规则降噪", ["命中位于低攻击面路径或证据信号较弱"])
    return ScreeningPresentation("review", "初筛未形成结论", ["自动初筛未完成，需要人工判断是否继续"])
