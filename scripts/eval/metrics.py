"""发现侧黄金集分账指标（discovery-spec §2.6 / §10 / §12）。

禁止把「无线索全库挖掘召回」算进任何门禁。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

HUMAN_VIEW_STATUSES = frozenset({"needs_review", "dispatched", "resolved"})
LEAD_FINISHED_VERDICTS = frozenset({
    "confirmed", "partial", "code_reachable", "code_smell",
    "false_positive", "not_reproduced",
})


def normalize_cwe(value: str | None) -> str:
    if not value:
        return ""
    raw = str(value).strip().upper().replace("CWE-", "").replace("CWE", "")
    return f"CWE-{raw}" if raw.isdigit() else str(value).strip().upper()


def locator_hits(cwe: str, file_contains: str, items: Iterable[dict[str, Any]]) -> bool:
    want = normalize_cwe(cwe)
    needle = (file_contains or "").lower()
    if not want or not needle:
        return False
    for item in items:
        path = (item.get("file_path") or "").lower()
        if normalize_cwe(item.get("cwe")) == want and needle in path:
            return True
    return False


@dataclass
class CaseRecord:
    case_id: str
    git_url: str
    ref: str
    expected: list[dict[str, Any]]
    tp_samples: list[dict[str, Any]]
    fp_samples: list[dict[str, Any]]
    language: str = ""
    notes: str = ""


@dataclass
class CaseSnapshot:
    """一次 discovery 任务跑完后的可分账快照。"""

    raw_findings: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    has_lead: bool = False
    task_verdict: str | None = None
    review_ready_seconds: float | None = None
    skipped: str | None = None  # 未跑（无 fixture / live 失败）时的原因
    dropped_c_count: int = 0  # 确定性降噪 C 档（未建组）


@dataclass
class CaseMetrics:
    case_id: str
    expected_total: int
    expected_hit: int
    missed: list[str]
    raw_alerts: int
    human_view_groups: int
    tp_groups: int
    tp_groups_matching_expected: int
    bypass_groups: int
    dropped_c_count: int
    tp_samples_in_funnel: int
    tp_samples_judged_fp: int
    has_lead: bool
    lead_false_positive: bool | None
    review_ready_seconds: float | None
    skipped: str | None = None


def _funnel_items(snap: CaseSnapshot) -> list[dict[str, Any]]:
    return list(snap.raw_findings) + list(snap.groups)


def score_case(case: CaseRecord, snap: CaseSnapshot) -> CaseMetrics:
    expected_hit = 0
    missed: list[str] = []
    funnel = _funnel_items(snap)
    for item in case.expected:
        cwe = item.get("cwe") or ""
        path = item.get("file_contains") or ""
        if locator_hits(cwe, path, funnel):
            expected_hit += 1
        else:
            missed.append(f"{normalize_cwe(cwe)} @ {path}")

    human_groups = [g for g in snap.groups if (g.get("status") or "") in HUMAN_VIEW_STATUSES]
    tp_groups = [
        g for g in snap.groups
        if g.get("ai_verdict") == "tp" and g.get("ai_verdict") != "bypass"
    ]
    bypass = sum(1 for g in snap.groups if g.get("ai_verdict") == "bypass")
    tp_match = sum(
        1 for g in tp_groups
        if any(
            locator_hits(e.get("cwe") or "", e.get("file_contains") or "", [g])
            for e in case.expected
        )
    )

    samples = case.tp_samples or [
        {"cwe": e.get("cwe"), "file_contains": e.get("file_contains")}
        for e in case.expected
    ]
    in_funnel = 0
    judged_fp = 0
    for sample in samples:
        matched = [
            g for g in snap.groups
            if locator_hits(sample.get("cwe") or "", sample.get("file_contains") or "", [g])
        ]
        if not matched:
            continue
        in_funnel += 1
        if any(g.get("ai_verdict") == "fp" for g in matched):
            judged_fp += 1

    lead_fp: bool | None = None
    if snap.has_lead and (snap.task_verdict or "") in LEAD_FINISHED_VERDICTS:
        lead_fp = snap.task_verdict == "false_positive"

    return CaseMetrics(
        case_id=case.case_id,
        expected_total=len(case.expected),
        expected_hit=expected_hit,
        missed=missed,
        raw_alerts=len(snap.raw_findings) if snap.raw_findings else sum(
            int(g.get("member_count") or 1) for g in snap.groups
        ),
        human_view_groups=len(human_groups),
        tp_groups=len(tp_groups),
        tp_groups_matching_expected=tp_match,
        bypass_groups=bypass,
        dropped_c_count=int(snap.dropped_c_count or 0),
        tp_samples_in_funnel=in_funnel,
        tp_samples_judged_fp=judged_fp,
        has_lead=bool(snap.has_lead),
        lead_false_positive=lead_fp,
        review_ready_seconds=snap.review_ready_seconds,
        skipped=snap.skipped,
    )


@dataclass
class AggregateReport:
    cases: list[CaseMetrics]
    hypothesis_coverage: float | None
    noise_compression: float | None
    triage_precision: float | None
    recall_redline: float | None
    lead_fp_rate: float | None
    median_review_ready_seconds: float | None
    skipped_count: int
    missed_by_case: dict[str, list[str]]
    dropped_c_total: int = 0


def aggregate(rows: list[CaseMetrics]) -> AggregateReport:
    scored = [r for r in rows if not r.skipped]
    exp_total = sum(r.expected_total for r in scored)
    exp_hit = sum(r.expected_hit for r in scored)
    raw = sum(r.raw_alerts for r in scored)
    human = sum(r.human_view_groups for r in scored)
    tp = sum(r.tp_groups for r in scored)
    tp_ok = sum(r.tp_groups_matching_expected for r in scored)
    funnel = sum(r.tp_samples_in_funnel for r in scored)
    funnel_fp = sum(r.tp_samples_judged_fp for r in scored)
    leads = [r for r in scored if r.lead_false_positive is not None]
    times = sorted(r.review_ready_seconds for r in scored if r.review_ready_seconds is not None)

    def _ratio(num: float, den: float) -> float | None:
        if den <= 0:
            return None
        return num / den

    median = None
    if times:
        mid = len(times) // 2
        median = times[mid] if len(times) % 2 else (times[mid - 1] + times[mid]) / 2

    return AggregateReport(
        cases=rows,
        hypothesis_coverage=_ratio(exp_hit, exp_total),
        noise_compression=_ratio(raw, human) if human else None,
        triage_precision=_ratio(tp_ok, tp),
        recall_redline=_ratio(funnel_fp, funnel),
        lead_fp_rate=_ratio(sum(1 for r in leads if r.lead_false_positive), len(leads)),
        median_review_ready_seconds=median,
        skipped_count=sum(1 for r in rows if r.skipped),
        missed_by_case={r.case_id: r.missed for r in scored if r.missed},
        dropped_c_total=sum(r.dropped_c_count for r in scored),
    )


GATES = {
    "noise_compression": ("≥ 10:1", lambda v: v is not None and v >= 10),
    "triage_precision": ("≥ 80%", lambda v: v is not None and v >= 0.80),
    "recall_redline": ("≤ 5%", lambda v: v is not None and v <= 0.05),
    "lead_fp_rate": ("≤ 50%", lambda v: v is not None and v <= 0.50),
}


def render_markdown(report: AggregateReport) -> str:
    def pct(value: float | None) -> str:
        return "n/a（分母为 0）" if value is None else f"{value:.1%}"

    def ratio(value: float | None) -> str:
        return "n/a（无人视野组）" if value is None else f"{value:.1f}:1"

    def gate_line(key: str, value: float | None, shown: str) -> str:
        label, pred = GATES[key]
        if value is None:
            mark = "— 样本不足"
        else:
            mark = "PASS" if pred(value) else "FAIL"
        return f"| {key} | {shown} | 门禁 {label} | {mark} |"

    lines = [
        "# 发现侧黄金集评估报告",
        "",
        "口径见 `docs/discovery-spec.md` §2.6 / §10 / §12。",
        "**不做、不优化、不作为门禁**：无线索全库挖掘召回。",
        "",
        f"- 用例总数：{len(report.cases)}（跳过 {report.skipped_count}）",
        f"- 时效（复核台就绪，中位数）："
        f"{'n/a' if report.median_review_ready_seconds is None else f'{report.median_review_ready_seconds:.0f}s'}",
        f"- 降噪漏斗（C 档未建组合计）：{report.dropped_c_total}",
        "",
        "## 分账指标",
        "",
        "| 指标 | 本次 | 门禁 | 结果 |",
        "|---|---|---|---|",
        gate_line("noise_compression", report.noise_compression, ratio(report.noise_compression)),
        f"| hypothesis_coverage | {pct(report.hypothesis_coverage)} | 记录；未打中不算二审失败 | 记录 |",
        gate_line("triage_precision", report.triage_precision, pct(report.triage_precision)),
        gate_line("recall_redline", report.recall_redline, pct(report.recall_redline)),
        gate_line("lead_fp_rate", report.lead_fp_rate, pct(report.lead_fp_rate)),
        "",
        "## 引擎未命中（假设覆盖缺口，不计二审失败）",
        "",
    ]
    if not report.missed_by_case:
        lines.append("无。")
    else:
        for cid, missed in sorted(report.missed_by_case.items()):
            lines.append(f"- `{cid}`: " + "; ".join(missed))
    lines += ["", "## 逐用例", "", "| 用例 | 覆盖 | 原始告警 | C档 | 人视野组 | 主线索 | 跳过 |", "|---|---|---|---|---|---|---|"]
    for row in report.cases:
        cov = f"{row.expected_hit}/{row.expected_total}"
        lead = "是/FP" if row.lead_false_positive else ("是" if row.has_lead else "否")
        if row.lead_false_positive is False and row.has_lead:
            lead = "是/非FP"
        lines.append(
            f"| `{row.case_id}` | {cov} | {row.raw_alerts} | {row.dropped_c_count} "
            f"| {row.human_view_groups} | {lead} | {row.skipped or ''} |"
        )
    lines.append("")
    return "\n".join(lines)
