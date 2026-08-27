from datetime import datetime
from typing import Any

from pydantic import BaseModel


# ── 响应 ──

class EvidenceResponse(BaseModel):
    id: str
    object_key: str
    bucket: str
    file_name: str
    content_type: str
    size_bytes: int
    kind: str
    created_at: datetime
    download_url: str | None = None  # 预签名 URL（列表/详情接口生成，落库不存）

    model_config = {"from_attributes": True}


class ReportDetail(BaseModel):
    id: str
    task_id: str
    run_id: str
    owner_id: str
    status: str
    conclusion: str
    title: str
    summary: str | None
    reasoning: str | None
    evidence_summary: str | None
    artifact_key: str | None
    # 结构化字段(阶段 1 新增)
    verdict: str | None = None
    cvss_score: float | None = None
    severity: str | None = None
    vulnerable_file: str | None = None
    product_name: str | None = None
    affected_version: str | None = None
    project_address: str | None = None
    report_data: dict[str, Any] | None = None
    md_artifact_key: str | None = None
    docx_artifact_key: str | None = None
    poc_language: str | None = None
    poc_filename: str | None = None
    poc_code: str | None = None
    poc_usage: str | None = None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    evidence: list[EvidenceResponse] = []

    model_config = {"from_attributes": True}


class ReportSummary(BaseModel):
    id: str
    task_id: str
    project_address: str | None = None
    project_ref: str | None = None
    task_type: str | None = None
    document_kind: str | None = None
    status: str
    conclusion: str
    title: str
    summary: str | None
    verdict: str | None = None
    severity: str | None = None
    product_name: str | None = None
    affected_version: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReportListResponse(BaseModel):
    items: list[ReportSummary]
    total: int
    limit: int
    offset: int


class AuditTaskSummary(BaseModel):
    """审计报告 Tab：按 discovery 任务聚合。"""
    task_id: str
    project_id: str | None = None
    project_address: str | None = None
    project_ref: str | None = None
    task_status: str
    report_id: str | None = None
    report_status: str | None = None
    confirmed_count: int = 0
    code_reachable_count: int = 0
    vuln_report_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    published_at: datetime | None = None


class AuditTaskListResponse(BaseModel):
    items: list[AuditTaskSummary]
    total: int
    limit: int
    offset: int


class VulnReportSummary(BaseModel):
    alert_group_id: str
    task_id: str
    summary: str
    final_verdict: str | None = None
    verification_basis: str | None = None
    primary_engine: str | None = None
    cwe: str | None = None
    file_path: str | None = None
    generated_at: str | None = None


class VulnReportListResponse(BaseModel):
    task_id: str
    items: list[VulnReportSummary]
    total: int


# ── 请求 ──

class ReportUpdateRequest(BaseModel):
    status: str | None = None
    title: str | None = None
    summary: str | None = None


class ReportPublishRequest(BaseModel):
    """发布报告：生成产物归档并标记 published"""
    pass
