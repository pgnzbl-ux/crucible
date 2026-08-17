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
    status: str
    conclusion: str
    title: str
    summary: str | None
    verdict: str | None = None
    severity: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReportListResponse(BaseModel):
    items: list[ReportSummary]
    total: int
    limit: int
    offset: int


# ── 请求 ──

class ReportUpdateRequest(BaseModel):
    status: str | None = None
    title: str | None = None
    summary: str | None = None


class ReportPublishRequest(BaseModel):
    """发布报告：生成产物归档并标记 published"""
    pass
