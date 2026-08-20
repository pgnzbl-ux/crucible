from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydBase, ConfigDict, Field, field_validator

_REF_TYPES = frozenset({"branch", "tag", "commit"})


class ProjectCreateRequest(PydBase):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=255)
    git_url: str = Field(..., min_length=1, max_length=1024)
    default_ref: str | None = Field(None, max_length=255)
    default_ref_type: str | None = Field(None, max_length=16)
    description: str | None = None

    @field_validator("default_ref_type")
    @classmethod
    def _validate_ref_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _REF_TYPES:
            raise ValueError("default_ref_type 必须是 branch、tag 或 commit")
        return v


class ProjectUpdateRequest(PydBase):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=255)
    default_ref: str | None = Field(None, max_length=255)
    default_ref_type: str | None = Field(None, max_length=16)
    description: str | None = None

    @field_validator("default_ref_type")
    @classmethod
    def _validate_ref_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _REF_TYPES:
            raise ValueError("default_ref_type 必须是 branch、tag 或 commit")
        return v


class SourceRefSummary(PydBase):
    """列表/详情里给任务下拉用的版本摘要：branch|tag|commit + 名称。"""
    ref_type: str
    ref_name: str


class ProjectResponse(PydBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    git_url: str
    source_type: str = "git"
    default_ref: str | None
    default_ref_type: str | None = None
    description: str | None
    owner_id: str
    detected_language: str | None = None
    detected_framework: str | None = None
    is_web: bool | None = None
    last_cloned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    source_refs: list[SourceRefSummary] = Field(default_factory=list)


class ProjectListResponse(PydBase):
    items: list[ProjectResponse]
    total: int


class SourceArtifactResponse(PydBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    git_url: str
    git_host: str
    project_key: str
    repo_dirname: str
    ref_type: str
    ref_name: str
    commit_sha: str
    object_url: str
    size_bytes: int | None = None
    created_at: datetime
    updated_at: datetime


class SourceArtifactListResponse(PydBase):
    items: list[SourceArtifactResponse]
    total: int
