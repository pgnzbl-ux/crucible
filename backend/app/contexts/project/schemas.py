from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydBase, ConfigDict, Field


class ProjectCreateRequest(PydBase):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=255)
    git_url: str = Field(..., min_length=1, max_length=1024)
    default_ref: str | None = Field(None, max_length=255)
    description: str | None = None


class ProjectUpdateRequest(PydBase):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=255)
    default_ref: str | None = Field(None, max_length=255)
    description: str | None = None


class ProjectResponse(PydBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    git_url: str
    default_ref: str | None
    description: str | None
    owner_id: str
    detected_language: str | None = None
    detected_framework: str | None = None
    is_web: bool | None = None
    last_cloned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(PydBase):
    items: list[ProjectResponse]
    total: int
