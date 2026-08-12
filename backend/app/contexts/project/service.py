from .models import Project
from .repository import ProjectRepository
from .schemas import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)


def _to_response(p: Project) -> ProjectResponse:
    return ProjectResponse.model_validate(p)


class ProjectService:
    def __init__(self, repo: ProjectRepository):
        self.repo = repo

    async def create_project(self, request: ProjectCreateRequest, owner_id: str) -> ProjectResponse:
        project = Project(
            name=request.name,
            git_url=request.git_url,
            default_ref=request.default_ref,
            description=request.description,
            owner_id=owner_id,
        )
        project = await self.repo.create(project)
        return _to_response(project)

    async def upsert_by_git_url(
        self,
        *,
        git_url: str,
        owner_id: str,
        name: str | None = None,
        default_ref: str | None = None,
    ) -> Project:
        """同 git_url + owner 复用,不存在则建。供任务创建时自动建 project 用。"""
        existing = await self.repo.get_by_git_url(git_url, owner_id)
        if existing:
            return existing
        fallback_name = (
            name
            or git_url.rstrip("/").split("/")[-1].replace(".git", "")
            or "project"
        )
        project = Project(
            name=fallback_name,
            git_url=git_url,
            default_ref=default_ref,
            owner_id=owner_id,
        )
        return await self.repo.create(project)

    async def get_project(self, project_id: str) -> ProjectResponse | None:
        p = await self.repo.get_by_id(project_id)
        return _to_response(p) if p else None

    async def list_projects(
        self, owner_id: str, limit: int = 50, offset: int = 0
    ) -> ProjectListResponse:
        items, total = await self.repo.list_by_owner(owner_id, limit, offset)
        return ProjectListResponse(items=[_to_response(i) for i in items], total=total)

    async def update_project(
        self, project_id: str, request: ProjectUpdateRequest
    ) -> ProjectResponse | None:
        p = await self.repo.get_by_id(project_id)
        if not p:
            return None
        for field in ("name", "default_ref", "description"):
            v = getattr(request, field)
            if v is not None:
                setattr(p, field, v)
        await self.repo.session.flush()
        return _to_response(p)

    async def delete_project(self, project_id: str) -> bool:
        p = await self.repo.get_by_id(project_id)
        if not p:
            return False
        await self.repo.delete(p)
        return True

    async def update_profile(
        self,
        project_id: str,
        *,
        language: str | None = None,
        framework: str | None = None,
        is_web: bool | None = None,
    ) -> None:
        """节点 1 画像后回填(供编排器调用,后续任务复用省 AI)。"""
        p = await self.repo.get_by_id(project_id)
        if not p:
            return
        if language is not None:
            p.detected_language = language
        if framework is not None:
            p.detected_framework = framework
        if is_web is not None:
            p.is_web = is_web
        await self.repo.session.flush()
