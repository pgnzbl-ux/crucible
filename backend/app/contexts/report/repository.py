from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Report, Evidence


class ReportRepository:
    """报告数据访问层"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, report: Report) -> Report:
        self.session.add(report)
        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def get_by_id(self, report_id: str) -> Report | None:
        result = await self.session.execute(
            select(Report)
            .where(Report.id == report_id)
            .options(selectinload(Report.evidence))
        )
        return result.scalar_one_or_none()

    async def get_by_task(self, task_id: str) -> Report | None:
        result = await self.session.execute(
            select(Report)
            .where(Report.task_id == task_id)
            .options(selectinload(Report.evidence))
        )
        return result.scalar_one_or_none()

    async def list_by_owner(
        self,
        owner_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Report], int]:
        stmt = select(Report).where(Report.owner_id == owner_id)
        count_stmt = select(func.count(Report.id)).where(Report.owner_id == owner_id)
        if status:
            stmt = stmt.where(Report.status == status)
            count_stmt = count_stmt.where(Report.status == status)
        stmt = stmt.order_by(Report.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        count_result = await self.session.execute(count_stmt)
        return list(result.scalars().all()), count_result.scalar() or 0

    async def update_status(self, report: Report, new_status: str) -> Report:
        report.status = new_status
        await self.session.flush()
        return report

    async def add_evidence(self, evidence: Evidence) -> Evidence:
        self.session.add(evidence)
        await self.session.flush()
        await self.session.refresh(evidence)
        return evidence

    async def list_evidence(self, report_id: str) -> list[Evidence]:
        result = await self.session.execute(
            select(Evidence).where(Evidence.report_id == report_id).order_by(Evidence.created_at)
        )
        return list(result.scalars().all())
