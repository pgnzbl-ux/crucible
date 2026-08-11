from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── 请求 ──

class TaskCreateRequest(BaseModel):
    project_address: str = Field(..., min_length=1, max_length=1024, description="项目 Git 地址")
    project_ref: str | None = Field(None, max_length=255, description="分支/commit/tag")
    source_type: str = Field("git", pattern=r"^(git|local_upload)$")
    vulnerability_description: str = Field(..., min_length=10, description="漏洞描述")
    vulnerability_reasoning: str | None = Field(None, description="漏洞推理过程")
    priority: str = Field("medium", pattern=r"^(low|medium|high|critical)$")
    credential_refs: list[str] = Field(default_factory=list, description="关联凭据 id 列表（P1-6）")


class TaskUpdateRequest(BaseModel):
    status: str | None = None
    priority: str | None = None


class TaskListRequest(BaseModel):
    status: str | None = None
    priority: str | None = None
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


# ── 响应 ──

class TaskSummary(BaseModel):
    id: str
    project_address: str
    status: str
    priority: str
    source_type: str
    owner_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RunSummary(BaseModel):
    id: str
    task_id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentEventResponse(BaseModel):
    id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TaskDetail(TaskSummary):
    project_ref: str | None = None
    vulnerability_description: str = ""
    vulnerability_reasoning: str | None = None
    credential_refs: list[str] = []
    runs: list[RunSummary] = []


class TaskListResponse(BaseModel):
    items: list[TaskSummary]
    total: int
    limit: int
    offset: int
