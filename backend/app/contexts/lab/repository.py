from collections.abc import Collection
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Lab


class LabRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, lab_id: str) -> Lab | None:
        return await self.session.get(Lab, lab_id)

    async def get_by_key(
        self, owner_id: str, project_id: str, commit_sha: str
    ) -> Lab | None:
        result = await self.session.execute(
            select(Lab).where(
                Lab.owner_id == owner_id,
                Lab.project_id == project_id,
                Lab.commit_sha == commit_sha,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_id: str) -> list[Lab]:
        result = await self.session.execute(
            select(Lab)
            .where(Lab.owner_id == owner_id)
            .order_by(Lab.project_id, Lab.created_at.desc())
        )
        return list(result.scalars().all())

    async def add(self, lab: Lab) -> Lab:
        self.session.add(lab)
        await self.session.flush()
        return lab

    async def cas_reclaim(
        self,
        lab_id: str,
        from_statuses: Collection[str],
        creator_task_id: str,
    ) -> bool:
        result = await self.session.execute(
            update(Lab)
            .where(Lab.id == lab_id, Lab.status.in_(from_statuses))
            .values(status="creating", creator_task_id=creator_task_id)
        )
        return result.rowcount == 1

    async def cas_status(
        self,
        lab_id: str,
        from_statuses: Collection[str],
        to_status: str,
    ) -> bool:
        result = await self.session.execute(
            update(Lab)
            .where(Lab.id == lab_id, Lab.status.in_(from_statuses))
            .values(status=to_status)
        )
        return result.rowcount == 1

    async def cas_takeover_creating(
        self,
        lab_id: str,
        *,
        previous_creator_task_id: str | None,
        creator_task_id: str,
        stale_before: datetime | None = None,
    ) -> bool:
        conditions = [
            Lab.id == lab_id,
            Lab.status == "creating",
            Lab.creator_task_id == previous_creator_task_id,
        ]
        if stale_before is not None:
            conditions.append(
                or_(Lab.last_seen_at.is_(None), Lab.last_seen_at <= stale_before)
            )
        result = await self.session.execute(
            update(Lab)
            .where(*conditions)
            .values(
                creator_task_id=creator_task_id,
                last_seen_at=datetime.now(timezone.utc),
                error_message=None,
            )
        )
        return result.rowcount == 1

    async def cas_transition(
        self,
        lab_id: str,
        *,
        from_statuses: Collection[str],
        to_status: str,
        creator_task_id: str | None = None,
        values: dict[str, Any] | None = None,
    ) -> bool:
        conditions = [Lab.id == lab_id, Lab.status.in_(from_statuses)]
        if creator_task_id is not None:
            conditions.append(Lab.creator_task_id == creator_task_id)
        result = await self.session.execute(
            update(Lab)
            .where(*conditions)
            .values(status=to_status, **(values or {}))
        )
        return result.rowcount == 1
