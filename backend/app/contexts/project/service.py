import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.shared.exceptions import ConflictError

from .git_url import classify_ref, parse_git_url
from .models import FRAMEWORK_SNAPSHOT_MAX, LANGUAGE_SNAPSHOT_MAX, Project, SourceArtifact
from .repository import ProjectRepository
from .schemas import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
    SourceArtifactResponse,
    SourceRefSummary,
)
from .source_acquire import CachedSource, SourceAcquireResult
from .source_cache import (
    SOURCE_BUCKET,
    MinioSourceStore,
    object_access_url,
    upload_source_object_key,
)
from .source_upload import (
    UPLOAD_HOST,
    UPLOAD_REF_NAME,
    UPLOAD_REF_TYPE,
    ingest_source_archive,
    parse_source_locator,
    upload_locator,
)


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


def _synthetic_git_refs(p: Project) -> list[SourceRefSummary]:
    if p.source_type == "local_upload":
        return []
    stored_type = p.default_ref_type
    ref_name = (p.default_ref or "").strip()
    if stored_type in ("branch", "tag", "commit"):
        if stored_type == "branch" and (not ref_name or ref_name.upper() == "HEAD"):
            return [SourceRefSummary(ref_type="branch", ref_name="HEAD")]
        if ref_name:
            return [SourceRefSummary(ref_type=stored_type, ref_name=ref_name)]
    ref_type, inferred_name = classify_ref(p.default_ref)
    return [SourceRefSummary(ref_type=ref_type, ref_name=inferred_name)]


def _git_lookup_key(p: Project) -> tuple[str, str] | None:
    if p.source_type == "local_upload":
        return None
    try:
        parsed = parse_git_url(p.git_url)
    except ValueError:
        return None
    return parsed.host, parsed.project_key


def _refs_for_project(
    p: Project, artifacts_by_key: dict[tuple[str, str], list[Any]]
) -> list[SourceRefSummary]:
    key = _git_lookup_key(p)
    rows = artifacts_by_key.get(key, []) if key else []
    seen: set[tuple[str, str]] = set()
    refs: list[SourceRefSummary] = []
    for row in rows:
        pair = (row.ref_type, row.ref_name)
        if pair in seen:
            continue
        seen.add(pair)
        refs.append(SourceRefSummary(ref_type=row.ref_type, ref_name=row.ref_name))
    return refs or _synthetic_git_refs(p)


def _to_response(
    p: Project, source_refs: list[SourceRefSummary] | None = None
) -> ProjectResponse:
    data = ProjectResponse.model_validate(p)
    return data.model_copy(update={"source_refs": source_refs if source_refs is not None else _synthetic_git_refs(p)})


def _to_artifact(row: Any) -> SourceArtifactResponse:
    return SourceArtifactResponse.model_validate(row)


def _cached_from_row(row: Any) -> CachedSource:
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


class ProjectService:
    def __init__(self, repo: ProjectRepository):
        self.repo = repo

    async def _require_unique_name(self, owner_id: str, name: str) -> None:
        existing = await self.repo.get_by_name(owner_id, name)
        if existing:
            raise ConflictError(f"项目名称已存在: {name}，请换一个名称")

    async def create_project(self, request: ProjectCreateRequest, owner_id: str) -> ProjectResponse:
        parsed = parse_git_url(request.git_url)
        await self._require_unique_name(owner_id, request.name)
        project = Project(
            name=request.name,
            git_url=parsed.normalized,
            source_type="git",
            default_ref=request.default_ref,
            default_ref_type=request.default_ref_type,
            description=request.description,
            owner_id=owner_id,
        )
        project = await self.repo.create(project)
        return (await self._responses_with_artifact_refs([project]))[0]

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
        await self._require_unique_name(owner_id, fallback_name)
        project = Project(
            name=fallback_name,
            git_url=stored_url,
            default_ref=default_ref,
            owner_id=owner_id,
        )
        return await self.repo.create(project)

    async def _responses_with_artifact_refs(self, projects: list[Project]) -> list[ProjectResponse]:
        if not projects:
            return []
        artifacts = await self.repo.list_source_artifacts_by_owner(projects[0].owner_id)
        by_key: dict[tuple[str, str], list[Any]] = {}
        for row in artifacts:
            if row.git_host == UPLOAD_HOST:
                continue
            by_key.setdefault((row.git_host, row.project_key), []).append(row)
        return [_to_response(p, _refs_for_project(p, by_key)) for p in projects]

    async def get_project(self, project_id: str, owner_id: str) -> ProjectResponse | None:
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return None
        return (await self._responses_with_artifact_refs([p]))[0]

    async def names_by_ids(self, project_ids: list[str], owner_id: str) -> dict[str, str]:
        return await self.repo.names_by_ids(project_ids, owner_id)

    async def list_projects(
        self, owner_id: str, limit: int = 50, offset: int = 0
    ) -> ProjectListResponse:
        items, total = await self.repo.list_by_owner(owner_id, limit, offset)
        return ProjectListResponse(
            items=await self._responses_with_artifact_refs(items),
            total=total,
            limit=limit,
            offset=offset,
        )

    async def update_project(
        self, project_id: str, request: ProjectUpdateRequest, owner_id: str
    ) -> ProjectResponse | None:
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return None
        if request.name is not None and request.name != p.name:
            await self._require_unique_name(owner_id, request.name)
        for field in ("name", "default_ref", "default_ref_type", "description"):
            v = getattr(request, field)
            if v is not None:
                setattr(p, field, v)
        await self.repo.session.flush()
        return (await self._responses_with_artifact_refs([p]))[0]

    async def delete_project(self, project_id: str, owner_id: str, store=None) -> bool:
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return False
        rows = await self._artifacts_for_project(p)
        object_keys = list({row.object_key for row in rows if row.object_key})
        for row in rows:
            await self.repo.delete_source_artifact(row)
        try:
            await self.repo.delete(p)
        except IntegrityError as exc:
            raise ConflictError(
                "项目仍被任务或靶场引用，无法删除",
                code="PROJECT_IN_USE",
            ) from exc
        object_store = store or MinioSourceStore()
        for key in object_keys:
            await self._delete_object_if_unreferenced(key, object_store)
        return True

    async def delete_artifact(
        self,
        project_id: str,
        artifact_id: str,
        owner_id: str,
        store=None,
    ) -> bool:
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return False
        row = await self.repo.get_source_artifact(artifact_id)
        if not row or row.owner_id != owner_id:
            return False
        try:
            parsed = parse_source_locator(p.git_url, getattr(p, "source_type", None))
        except ValueError:
            return False
        if row.project_key != parsed.project_key or row.git_host != parsed.host:
            return False
        object_key = row.object_key
        await self.repo.delete_source_artifact(row)
        await self._delete_object_if_unreferenced(object_key, store or MinioSourceStore())
        return True

    async def _artifacts_for_project(self, project: Project) -> list[SourceArtifact]:
        try:
            key = parse_source_locator(
                project.git_url, getattr(project, "source_type", None)
            ).project_key
        except ValueError:
            return []
        return await self.repo.list_source_artifacts(key, project.owner_id)

    async def _delete_object_if_unreferenced(self, object_key: str, store) -> None:
        if not object_key:
            return
        remaining = await self.repo.count_source_artifacts_by_object_key(object_key)
        if remaining == 0:
            store.delete(object_key)

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
        self,
        git_url: str,
        ref: str | None,
        owner_id: str,
        *,
        ref_type: str | None = None,
    ) -> CachedSource | None:
        """按 owner + host + space/project + 用户提交的 branch/tag/commit 查表。"""
        if not owner_id:
            return None
        parsed = parse_source_locator(git_url)
        if parsed.host == UPLOAD_HOST:
            row = await self.repo.find_source_artifact(
                owner_id, parsed.host, parsed.project_key, UPLOAD_REF_TYPE, UPLOAD_REF_NAME
            )
            return _cached_from_row(row) if row else None
        from app.contexts.project.git_url import resolve_ref_type

        resolved_type, ref_name = resolve_ref_type(ref_type, ref)
        row = await self.repo.find_source_artifact(
            owner_id, parsed.host, parsed.project_key, resolved_type, ref_name
        )
        if not row:
            return None
        return _cached_from_row(row)

    async def find_cached_source_by_commit(
        self,
        owner_id: str,
        git_host: str,
        project_key: str,
        commit_sha: str,
    ) -> CachedSource | None:
        """按 owner + host + project + commit 取已打包源码（MinIO 对象键就是 SHA）。"""
        if not owner_id or not commit_sha:
            return None
        row = await self.repo.find_source_artifact_by_sha(owner_id, commit_sha)
        if (
            not row
            or row.git_host != git_host
            or row.project_key != project_key
        ):
            return None
        return _cached_from_row(row)

    async def list_cached_sources(
        self, owner_id: str, git_host: str, project_key: str
    ) -> list[CachedSource]:
        rows = await self.repo.list_source_artifacts(project_key, owner_id)
        return [
            _cached_from_row(row)
            for row in rows
            if row.git_host == git_host and row.commit_sha
        ]

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
            "size_bytes": result.size_bytes,
        })

    async def ingest_uploaded_source(
        self,
        *,
        owner_id: str,
        filename: str,
        data: bytes,
        name: str | None = None,
        description: str | None = None,
        store=None,
    ) -> tuple[Project, SourceAcquireResult]:
        """校验并入库本地源码包。同 owner 项目名冲突则 409；不按内容指纹复用。"""
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix="crucible-upload-")
        try:
            ingested = ingest_source_archive(
                data, filename, display_name=name, workdir=tmp
            )
            project_name = (name or "").strip() or ingested.display_name
            await self._require_unique_name(owner_id, project_name)

            import uuid

            object_store = store or MinioSourceStore()
            project_id = str(uuid.uuid4())
            locator = upload_locator(project_id)
            project = await self.repo.create(
                Project(
                    id=project_id,
                    name=project_name,
                    git_url=locator,
                    source_type="local_upload",
                    owner_id=owner_id,
                    description=description,
                )
            )
            project_key = f"local/{project.id}"
            object_key = upload_source_object_key(owner_id, project.id)
            object_store.upload(object_key, ingested.sha256, ingested.archive_path)
            object_url = object_access_url(object_key)
            result = SourceAcquireResult(
                ok=True,
                origin="upload",
                git_url_original=locator,
                git_url_normalized=locator,
                project_key=project_key,
                git_host=UPLOAD_HOST,
                repo_dirname=ingested.repo_dirname,
                ref_type=UPLOAD_REF_TYPE,
                ref_name=UPLOAD_REF_NAME,
                commit_sha=ingested.sha256,
                object_key=object_key,
                object_url=object_url,
                top_level=ingested.top_level,
                file_count=ingested.file_count,
                size_bytes=ingested.size_bytes,
            )
            await self.record_source_artifact(result, owner_id)
            await self.repo.session.flush()
            return project, result
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def list_artifacts(
        self, project_id: str, owner_id: str
    ) -> list[SourceArtifactResponse] | None:
        """列出该项目的 Git 缓存包或上传原始包；无权或不存在返回 None。"""
        p = await self.repo.get_by_id(project_id)
        if not p or p.owner_id != owner_id:
            return None
        try:
            key = parse_source_locator(p.git_url, getattr(p, "source_type", None)).project_key
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
