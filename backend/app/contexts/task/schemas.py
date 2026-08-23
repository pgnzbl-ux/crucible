from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ── 请求 ──

class TaskCreateRequest(BaseModel):
    project_address: str = Field(..., min_length=1, max_length=1024, description="Git 地址或 upload://local/{slug}")
    project_ref: str | None = Field(None, max_length=255, description="分支/commit/tag")
    project_ref_type: str | None = Field(
        None,
        pattern=r"^(branch|tag|commit)$",
        description="引用类型；省略则自动推断",
    )
    clone_depth: int | None = Field(
        1,
        ge=0,
        le=500,
        description="浅克隆深度；0=全量 clone",
    )
    source_type: str = Field("git", pattern=r"^(git|local_upload)$")
    # discovery-spec §4.2.3：创建二选一，显式传入，禁止「没填描述就算审计」
    task_type: str = Field("verify", pattern=r"^(verify|discovery)$", description="verify(漏洞验证) | discovery(仓库审计)")
    vulnerability_description: str | None = Field(
        None, min_length=10, description="漏洞描述；task_type=verify 必填，discovery 必空",
    )
    vulnerability_reasoning: str | None = Field(None, description="漏洞推理过程")
    priority: str = Field("medium", pattern=r"^(low|medium|high|critical)$")
    credential_refs: list[str] = Field(default_factory=list, description="关联凭据 id 列表（P1-6）")

    @model_validator(mode="after")
    def _validate_task_type(self) -> "TaskCreateRequest":
        desc = (self.vulnerability_description or "").strip()
        if self.task_type == "verify" and len(desc) < 10:
            raise ValueError("漏洞验证任务必须提供至少 10 字的漏洞描述")
        if self.task_type == "discovery" and desc:
            raise ValueError("仓库审计任务禁止填写漏洞描述（人工线索请创建验证任务）")
        return self


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
    project_id: str | None = None
    project_ref: str | None = None
    project_ref_type: str | None = None
    status: str
    verdict: str | None = None
    priority: str
    source_type: str
    task_type: str = "verify"
    source_alert_group_id: str | None = None
    finding_count: int = 0
    pending_review_count: int = 0
    confirmed_count: int = 0
    report_status: str | None = None
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
    project_ref_type: str | None = None
    clone_depth: int | None = 1
    vulnerability_description: str = ""
    vulnerability_reasoning: str | None = None
    credential_refs: list[str] = []
    runs: list[RunSummary] = []
    # token 消耗台账汇总（prompt/completion/total/sessions）；无记录时为 None
    usage: dict[str, int] | None = None


class TaskListResponse(BaseModel):
    items: list[TaskSummary]
    total: int
    limit: int
    offset: int


class TaskStatsResponse(BaseModel):
    """工作台计数：一次 GROUP BY，排除 archived。"""
    total: int
    by_status: dict[str, int]
