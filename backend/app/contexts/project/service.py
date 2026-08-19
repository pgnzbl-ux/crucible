from datetime import datetime, timezone
from typing import Any
import json

from .git_url import classify_ref, parse_git_url
from .models import FRAMEWORK_SNAPSHOT_MAX, LANGUAGE_SNAPSHOT_MAX, Project
from .repository import ProjectRepository
from .schemas import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
    SourceArtifactResponse,
)
from .source_acquire import CachedSource, SourceAcquireResult
from .source_cache import SOURCE_BUCKET


def _snapshot_text(value: Any, max_len: int) -> str | None:
    """画像列表快照：list 拼成短句并截到列宽，避免 PG VARCHAR 截断把节点卡死。"""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if item not in (None, "")]
        text = ", ".join(parts)
    else:
        text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


def _to_response(p: Project) -> ProjectResponse:
    return ProjectResponse.model_validate(p)


def _to_artifact(row: Any) -> SourceArtifactResponse:
    return SourceArtifactResponse.model_validate(row)


class ProjectService:
    def __init__(self, repo: ProjectRepository):
        self.repo = repo

    async def create_project(self, request: ProjectCreateRequest, owner_id: str) -> ProjectResponse:
        git_url = request.git_url
        try:
            git_url = parse_git_url(request.git_url).normalized
        except ValueError:
            pass
        project = Project(
            name=request.name,
            git_url=git_url,
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
        """同仓库 + owner 复用（.git 后缀不区分），不存在则建。供任务创建时自动建 project 用。"""
        stored_url = git_url
        fallback_name = name
        try:
            parsed = parse_git_url(git_url)
            stored_url = parsed.normalized
            fallback_name = name or parsed.repo_dirname or "project"
        except ValueError:
            fallback_name = (
                name
                or git_url.rstrip("/").split("/")[-1].replace(".git", "")
                or "project"
            )
        existing = await self.repo.get_by_git_url(git_url, owner_id)
        if existing:
            if default_ref and not existing.default_ref:
                existing.default_ref = default_ref
                await self.repo.session.flush()
            return existing
        project = Project(
            name=fallback_name,
            git_url=stored_url,
            default_ref=default_ref,
            owner_id=owner_id,
        )
        return await self.repo.create(project)

    async def get_project(self, project_id: str, owner_id: str) -> ProjectResponse | None:
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return None
        return _to_response(p)

    async def names_by_ids(self, project_ids: list[str], owner_id: str) -> dict[str, str]:
        return await self.repo.names_by_ids(project_ids, owner_id)

    async def list_projects(
        self, owner_id: str, limit: int = 50, offset: int = 0
    ) -> ProjectListResponse:
        items, total = await self.repo.list_by_owner(owner_id, limit, offset)
        return ProjectListResponse(items=[_to_response(i) for i in items], total=total)

    async def update_project(
        self, project_id: str, request: ProjectUpdateRequest, owner_id: str
    ) -> ProjectResponse | None:
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return None
        for field in ("name", "default_ref", "description"):
            v = getattr(request, field)
            if v is not None:
                setattr(p, field, v)
        await self.repo.session.flush()
        return _to_response(p)

    async def delete_project(self, project_id: str, owner_id: str) -> bool:
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return False
        await self.repo.delete(p)
        return True

    async def update_profile(
        self,
        project_id: str,
        *,
        language: Any = None,
        framework: Any = None,
        is_web: bool | None = None,
    ) -> None:
        """节点 1 画像后回填 Project 列表快照（权威画像在 SourceArtifact.profile_json）。"""
        p = await self.repo.get_by_id(project_id)
        if not p:
            return
        if language is not None:
            p.detected_language = _snapshot_text(language, LANGUAGE_SNAPSHOT_MAX)
        if framework is not None:
            p.detected_framework = _snapshot_text(framework, FRAMEWORK_SNAPSHOT_MAX)
        if is_web is not None:
            p.is_web = is_web
        await self.repo.session.flush()

    async def touch_cloned(self, project_id: str) -> None:
        """源码落地（clone 或缓存命中）后更新 last_cloned_at。"""
        p = await self.repo.get_by_id(project_id)
        if not p:
            return
        p.last_cloned_at = datetime.now(timezone.utc)
        await self.repo.session.flush()

    async def find_cached_source(
        self, git_url: str, ref: str | None, owner_id: str
    ) -> CachedSource | None:
        """按 owner + host + space/project + 用户提交的 branch/tag/commit 查表。"""
        if not owner_id:
            return None
        parsed = parse_git_url(git_url)
        ref_type, ref_name = classify_ref(ref)
        row = await self.repo.find_source_artifact(
            owner_id, parsed.host, parsed.project_key, ref_type, ref_name
        )
        if not row:
            return None
        return CachedSource(
            object_key=row.object_key,
            object_url=row.object_url,
            repo_dirname=row.repo_dirname,
            commit_sha=row.commit_sha,
            ref_type=row.ref_type,
            ref_name=row.ref_name,
            git_url_normalized=row.git_url,
            project_key=row.project_key,
            git_host=row.git_host,
        )

    async def record_source_artifact(self, result: SourceAcquireResult, owner_id: str) -> None:
        """clone 成功并上传 MinIO 后写入/覆盖 source_artifacts。"""
        if not owner_id or not result.ok or not result.object_key or not result.commit_sha:
            return
        await self.repo.upsert_source_artifact({
            "owner_id": owner_id,
            "git_url": result.git_url_normalized,
            "git_host": result.git_host,
            "project_key": result.project_key,
            "repo_dirname": result.repo_dirname,
            "ref_type": result.ref_type,
            "ref_name": result.ref_name,
            "commit_sha": result.commit_sha,
            "bucket": SOURCE_BUCKET,
            "object_key": result.object_key,
            "object_url": result.object_url or "",
            "size_bytes": None,
        })

    async def list_artifacts(
        self, project_id: str, owner_id: str
    ) -> list[SourceArtifactResponse] | None:
        """按项目 space/project 列出已缓存的 MinIO 源码包；无权或不存在返回 None。"""
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return None
        try:
            key = parse_git_url(p.git_url).project_key
        except ValueError:
            return []
        rows = await self.repo.list_source_artifacts(key, owner_id)
        return [_to_artifact(row) for row in rows]

    async def find_cached_profile(
        self, owner_id: str, commit_sha: str
    ) -> dict[str, Any] | None:
        """同 owner + commit 已有画像则复用；缺 is_web 或坏 JSON 视为未命中。"""
        if not owner_id or not commit_sha:
            return None
        row = await self.repo.find_source_artifact_by_sha(owner_id, commit_sha)
        if not row or not row.profile_json:
            return None
        try:
            data = json.loads(row.profile_json)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("is_web"), bool):
            return None
        return data

    async def save_source_profile(
        self,
        *,
        owner_id: str,
        commit_sha: str,
        profile: dict[str, Any],
        project_id: str | None = None,
    ) -> None:
        """把画像写到该 SHA 的源码包上，并回填 Project 展示字段。"""
        if not owner_id or not commit_sha or not isinstance(profile.get("is_web"), bool):
            return
        row = await self.repo.find_source_artifact_by_sha(owner_id, commit_sha)
        if row:
            row.profile_json = json.dumps(profile, ensure_ascii=False)
            await self.repo.session.flush()
        if project_id:
            await self.update_profile(
                project_id,
                language=profile.get("language"),
                framework=profile.get("framework"),
                is_web=profile.get("is_web"),
            )
