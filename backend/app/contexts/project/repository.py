from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Project


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

    async def get_by_git_url(self, git_url: str, owner_id: str) -> Project | None:
        result = await self.session.execute(
            select(Project).where(Project.git_url == git_url, Project.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

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
