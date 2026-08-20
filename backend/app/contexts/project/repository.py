from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Project, SourceArtifact


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, project: Project) -> Project:
        self.session.add(project)
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def get_by_id(self, project_id: str) -> Project | None:
        return await self.session.get(Project, project_id)

    async def names_by_ids(self, project_ids: list[str], owner_id: str) -> dict[str, str]:
        if not project_ids:
            return {}
        result = await self.session.execute(
            select(Project.id, Project.name).where(
                Project.id.in_(project_ids),
                Project.owner_id == owner_id,
            )
        )
        return {project_id: name for project_id, name in result.all()}

    async def get_by_name(self, owner_id: str, name: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(Project.owner_id == owner_id, Project.name == name)
        )
        return result.scalar_one_or_none()

    async def get_by_git_url(self, git_url: str, owner_id: str) -> Project | None:
        from .git_url import git_url_lookup_candidates

        candidates = git_url_lookup_candidates(git_url)
        if not candidates:
            return None
        result = await self.session.execute(
            select(Project).where(Project.git_url.in_(candidates), Project.owner_id == owner_id)
        )
        return result.scalars().first()

    async def list_by_owner(
        self, owner_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[Project], int]:
        result = await self.session.execute(
            select(Project)
            .where(Project.owner_id == owner_id)
            .order_by(Project.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list(result.scalars().all())
        count_result = await self.session.execute(
            select(func.count()).select_from(Project).where(Project.owner_id == owner_id)
        )
        total = count_result.scalar_one()
        return items, total

    async def delete(self, project: Project) -> None:
        await self.session.delete(project)
        await self.session.flush()

    async def find_source_artifact(
        self,
        owner_id: str,
        git_host: str,
        project_key: str,
        ref_type: str,
        ref_name: str,
    ) -> SourceArtifact | None:
        if ref_type == "commit":
            result = await self.session.execute(
                select(SourceArtifact).where(
                    SourceArtifact.owner_id == owner_id,
                    SourceArtifact.git_host == git_host,
                    SourceArtifact.project_key == project_key,
                    SourceArtifact.commit_sha.startswith(ref_name.lower()),
                )
            )
            return result.scalars().first()
        result = await self.session.execute(
            select(SourceArtifact).where(
                SourceArtifact.owner_id == owner_id,
                SourceArtifact.git_host == git_host,
                SourceArtifact.project_key == project_key,
                SourceArtifact.ref_type == ref_type,
                SourceArtifact.ref_name == ref_name,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_source_artifact(self, data: dict) -> SourceArtifact:
        result = await self.session.execute(
            select(SourceArtifact).where(
                SourceArtifact.owner_id == data["owner_id"],
                SourceArtifact.git_host == data["git_host"],
                SourceArtifact.project_key == data["project_key"],
                SourceArtifact.ref_type == data["ref_type"],
                SourceArtifact.ref_name == data["ref_name"],
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            sha_changed = bool(
                data.get("commit_sha") and data["commit_sha"] != existing.commit_sha
            )
            for k, v in data.items():
                setattr(existing, k, v)
            if sha_changed:
                existing.profile_json = None
            await self.session.flush()
            await self.session.refresh(existing)
            return existing
        row = SourceArtifact(**data)
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def list_source_artifacts(self, project_key: str, owner_id: str) -> list[SourceArtifact]:
        result = await self.session.execute(
            select(SourceArtifact)
            .where(
                SourceArtifact.project_key == project_key,
                SourceArtifact.owner_id == owner_id,
            )
            .order_by(SourceArtifact.updated_at.desc())
        )
        return list(result.scalars().all())

    async def list_source_artifacts_by_owner(self, owner_id: str) -> list[SourceArtifact]:
        result = await self.session.execute(
            select(SourceArtifact)
            .where(SourceArtifact.owner_id == owner_id)
            .order_by(SourceArtifact.updated_at.desc())
        )
        return list(result.scalars().all())

    async def find_source_artifact_by_sha(
        self, owner_id: str, commit_sha: str
    ) -> SourceArtifact | None:
        if not owner_id or not commit_sha:
            return None
        sha = commit_sha.lower()
        result = await self.session.execute(
            select(SourceArtifact).where(
                SourceArtifact.owner_id == owner_id,
                SourceArtifact.commit_sha.startswith(sha),
            )
        )
        return result.scalars().first()
