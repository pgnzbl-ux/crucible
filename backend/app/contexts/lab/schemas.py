from pydantic import BaseModel, ConfigDict, Field


class ContainerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: str
    ports: str
    image: str


class LabResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    commit_sha: str
    status: str
    target_url: str | None
    ttl_remaining_seconds: int | None = Field(default=None, ge=0)
    containers: list[ContainerResponse]
    live_task_count: int = Field(ge=0)
    error_message: str | None = None


class LabGroupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    labs: list[LabResponse]


class LabListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LabGroupResponse]


class LabActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
