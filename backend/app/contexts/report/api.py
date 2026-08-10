from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from .repository import ReportRepository
from .schemas import ReportDetail, ReportListResponse, ReportUpdateRequest
from .service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


async def get_report_repo(session: Annotated[AsyncSession, Depends(get_db_session)]) -> ReportRepository:
    return ReportRepository(session)


async def get_report_service(repo: Annotated[ReportRepository, Depends(get_report_repo)]) -> ReportService:
    return ReportService(repo)


@router.get("/", response_model=ReportListResponse)
async def list_reports(
    svc: Annotated[ReportService, Depends(get_report_service)],
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ReportListResponse:
    owner_id = "system"  # TODO: 从 JWT 解析
    reports, total = await svc.list_reports(owner_id, status, limit, offset)
    return ReportListResponse(items=reports, total=total, limit=limit, offset=offset)


@router.get("/task/{task_id}", response_model=ReportDetail)
async def get_report_by_task(
    task_id: str,
    svc: Annotated[ReportService, Depends(get_report_service)],
) -> ReportDetail:
    report = await svc.get_report_by_task(task_id)
    if not report:
        raise HTTPException(404, "该任务暂无报告")
    return report


@router.get("/{report_id}", response_model=ReportDetail)
async def get_report(
    report_id: str,
    svc: Annotated[ReportService, Depends(get_report_service)],
) -> ReportDetail:
    report = await svc.get_report(report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    return report


@router.post("/{report_id}/publish", response_model=ReportDetail)
async def publish_report(
    report_id: str,
    svc: Annotated[ReportService, Depends(get_report_service)],
) -> ReportDetail:
    try:
        report = await svc.publish_report(report_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not report:
        raise HTTPException(404, "报告不存在")
    return report
