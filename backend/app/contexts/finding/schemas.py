"""finding context schemas — 复核台请求/响应。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class FindingSummary(BaseModel):
    id: str
    engine: str
    rule_id: str
    cwe: str | None = None
    severity: str | None = None
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    message: str
    source_to_sink: list[Any] | None = None
    code_snippet: str | None = None
    # 降噪/二审证据元数据（已脱敏）；非引擎结论措辞
    raw: dict[str, Any] | None = None


class AlertGroupSummary(BaseModel):
    id: str
    task_id: str
    project_id: str | None = None
    project_address: str | None = None
    project_ref: str | None = None
    audit_created_at: datetime | None = None
    cwe: str | None = None
    cwe_source: str = "missing"
    vulnerability_title: str = "未命名安全风险"
    representative_rule_id: str | None = None
    representative_message: str | None = None
    severity: str | None = None
    primary_engine: str | None = None
    screening_status: str = "processing"
    screening_summary: str = "等待初筛"
    screening_reasons: list[str] = Field(default_factory=list)
    file_path: str
    function_symbol: str | None = None
    line_span: str | None = None
    member_count: int = 1
    engine_set: list[str] = Field(default_factory=list)
    status: str
    clue_grade: str | None = None
    ai_verdict: str | None = None
    ai_confidence: float | None = None
    # 判决来源：null=agent 亲审(历史) | agent | fast_model | rule | carryover | propagated
    verdict_source: str | None = None
    priority: str | None = None
    resolution: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AlertGroupListRequest(BaseModel):
    task_id: str | None = None
    status: str | None = None
    resolution: str | None = Field(
        None, pattern=r"^(confirmed|false_positive|ignored)$",
        description="结案结果细分；与 status=resolved 配合或单独使用",
    )
    cwe: str | None = None
    ai_verdict: str | None = None
    engine: str | None = None
    clue_grade: str | None = None
    scope: str | None = Field(None, pattern=r"^(focus|review|processing|noise|all)$")
    q: str | None = Field(None, max_length=200, description="模糊搜索：CWE/路径/函数/项目/任务ID")
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class AlertGroupIdsRequest(BaseModel):
    """跨页全选：与列表相同的筛选，不分页。"""
    task_id: str | None = None
    status: str | None = None
    resolution: str | None = Field(None, pattern=r"^(confirmed|false_positive|ignored)$")
    cwe: str | None = None
    ai_verdict: str | None = None
    engine: str | None = None
    clue_grade: str | None = None
    scope: str | None = Field(None, pattern=r"^(focus|review|processing|noise|all)$")
    q: str | None = Field(None, max_length=200)


class AlertGroupListResponse(BaseModel):
    total: int
    items: list[AlertGroupSummary]


class AlertGroupIdsResponse(BaseModel):
    total: int
    ids: list[str]


class FindingStatsResponse(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_resolution: dict[str, int] = Field(default_factory=dict)
    by_queue: dict[str, int] = Field(default_factory=dict)


class AdjudicationDetail(BaseModel):
    id: str
    attempt: int
    verdict: str
    confidence: float | None = None
    why: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    need: list[str] = Field(default_factory=list)
    prompt_text: str
    response_text: str
    usage: dict[str, int] = Field(default_factory=dict)
    created_at: datetime | None = None


class ReviewActionDetail(BaseModel):
    id: str
    action: str
    reason_tags: list[str] = Field(default_factory=list)
    reason_text: str | None = None
    user_id: str
    created_at: datetime | None = None


class LeadRunSummary(BaseModel):
    id: str
    status: str
    verdict: str | None = None
    gate_verdict: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AlertGroupDetail(AlertGroupSummary):
    members: list[FindingSummary] = Field(default_factory=list)
    representative: FindingSummary | None = None
    adjudications: list[AdjudicationDetail] = Field(default_factory=list)
    reviews: list[ReviewActionDetail] = Field(default_factory=list)
    lead_runs: list[LeadRunSummary] = Field(default_factory=list)
    verification_task_id: str | None = None
    verification_verdict: str | None = None


class ReviewRequest(BaseModel):
    action: str = Field(..., pattern=r"^(confirm|reject|revise_cwe|adjust_confidence)$")
    reason_tags: list[str] = Field(default_factory=list)
    reason_text: str | None = None
    cwe: str | None = Field(None, description="revise_cwe 时必填")
    confidence: float | None = Field(None, ge=0, le=1, description="adjust_confidence 时必填")

    @model_validator(mode="after")
    def _validate(self) -> "ReviewRequest":
        if self.action == "revise_cwe" and not self.cwe:
            raise ValueError("revise_cwe 需要提供 cwe")
        if self.action == "adjust_confidence" and self.confidence is None:
            raise ValueError("adjust_confidence 需要提供 confidence")
        if self.action == "confirm" and not self.reason_tags and not (self.reason_text or "").strip():
            raise ValueError("确认漏洞必须填写审计理由")
        if self.action == "reject" and not self.reason_tags:
            raise ValueError("驳回必须带 reason_tags(预设标签，动作即标注数据)")
        return self


class ReviveResponse(BaseModel):
    id: str
    status: str


class ManualDispatchRequest(BaseModel):
    include_engine_conclusion: bool = Field(False, description="勾选后描述追加【引擎线索】段(§6.4)")


class ManualDispatchResponse(BaseModel):
    group_id: str
    verification_task_id: str


class BatchDeleteGroupsRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=100)


class BatchDeleteGroupsResponse(BaseModel):
    deleted: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(
        default_factory=list,
        description="未删项：{id, reason}；reason 为 not_found | in_progress",
    )
