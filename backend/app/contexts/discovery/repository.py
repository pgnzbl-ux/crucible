"""discovery context repository — ScanRun 存取。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.discovery.models import ScanRun


class DiscoveryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_scan_run(self, scan_run: ScanRun) -> ScanRun:
        self.session.add(scan_run)
        await self.session.flush()
        return scan_run

    async def get_scan_run(self, scan_run_id: str) -> ScanRun | None:
        return await self.session.get(ScanRun, scan_run_id)

    async def list_scan_runs(self, run_id: str) -> list[ScanRun]:
        result = await self.session.execute(
            select(ScanRun).where(ScanRun.run_id == run_id).order_by(ScanRun.created_at)
        )
        return list(result.scalars().all())

    async def list_scan_runs_for_task(self, task_id: str) -> list[ScanRun]:
        result = await self.session.execute(
            select(ScanRun).where(ScanRun.task_id == task_id).order_by(ScanRun.created_at)
        )
        return list(result.scalars().all())
