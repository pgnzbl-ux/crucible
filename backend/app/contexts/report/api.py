from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.shared.deps import CurrentUserId
from app.shared.time import iso_utc

from .repository import ReportRepository
from .schemas import EvidenceResponse, ReportDetail, ReportListResponse
from .service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


async def get_report_repo(session: Annotated[AsyncSession, Depends(get_db_session)]) -> ReportRepository:
    return ReportRepository(session)


async def get_report_service(repo: Annotated[ReportRepository, Depends(get_report_repo)]) -> ReportService:
    return ReportService(repo)


@router.get("/", response_model=ReportListResponse)
async def list_reports(
    svc: Annotated[ReportService, Depends(get_report_service)],
    user_id: CurrentUserId,
    status: str | None = Query(None),
    verdict: str | None = Query(None, max_length=64),
    query: str | None = Query(None, alias="q", max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ReportListResponse:
    reports, total = await svc.list_reports(
        user_id,
        status=status,
        verdict=verdict,
        query=query,
        limit=limit,
        offset=offset,
    )
    return ReportListResponse(items=reports, total=total, limit=limit, offset=offset)


@router.get("/task/{task_id}", response_model=ReportDetail)
async def get_report_by_task(
    task_id: str,
    svc: Annotated[ReportService, Depends(get_report_service)],
    user_id: CurrentUserId,
) -> ReportDetail:
    report = await svc.get_report_by_task(task_id, user_id)
    if not report:
        raise HTTPException(404, "该任务暂无报告")
    return report


@router.get("/{report_id}", response_model=ReportDetail)
async def get_report(
    report_id: str,
    svc: Annotated[ReportService, Depends(get_report_service)],
    user_id: CurrentUserId,
) -> ReportDetail:
    report = await svc.get_report(report_id, user_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    return report


@router.post("/{report_id}/publish", response_model=ReportDetail)
async def publish_report(
    report_id: str,
    svc: Annotated[ReportService, Depends(get_report_service)],
    user_id: CurrentUserId,
) -> ReportDetail:
    try:
        report = await svc.publish_report(report_id, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not report:
        raise HTTPException(404, "报告不存在")
    return report


# ── 证据（Evidence）上传 / 列表（P0-4） ──

@router.post("/{report_id}/evidences", response_model=EvidenceResponse, status_code=201)
async def upload_evidence(
    report_id: str,
    user_id: CurrentUserId,
    svc: Annotated[ReportService, Depends(get_report_service)],
    file: UploadFile = File(..., description="证据文件（日志/截图/PoC）"),
    kind: Annotated[str, Form(description="artifact | log | screenshot | poc")] = "artifact",
) -> EvidenceResponse:
    """上传证据文件 → MinIO → 落 evidences 表。返回带预签名下载 URL 的记录。"""
    # 流式读入并限制 50MB（防滥用；超大证据建议走对象存储直传，P1 再做）
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > 50 * 1024 * 1024:
            raise HTTPException(413, "单文件超过 50MB 限制")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "文件为空")
    evidence, err = await svc.attach_evidence(
        report_id=report_id,
        owner_id=user_id,
        file_name=file.filename or "evidence",
        content_type=file.content_type or "application/octet-stream",
        data=data,
        kind=kind,
    )
    if err:
        if "不存在" in err:
            raise HTTPException(404, err)
        if "非法" in err:
            raise HTTPException(400, err)
        if "已发布" in err:
            raise HTTPException(409, err)
        raise HTTPException(503, err)
    return evidence


@router.get("/{report_id}/evidences", response_model=list[EvidenceResponse])
async def list_evidences(
    report_id: str,
    svc: Annotated[ReportService, Depends(get_report_service)],
    user_id: CurrentUserId,
) -> list[EvidenceResponse]:
    """列出报告的所有证据（含预签名下载 URL）"""
    evidences = await svc.list_evidence(report_id, user_id)
    if evidences is None:
        raise HTTPException(404, "报告不存在")
    return evidences


@router.get("/{report_id}/export")
async def export_report(
    report_id: str,
    svc: Annotated[ReportService, Depends(get_report_service)],
    user_id: CurrentUserId,
    format: str = Query("json", pattern="^(json|md)$"),
):
    """导出报告。format=json 返回结构化 report_data;format=md 返回渲染后的 markdown。"""
    from fastapi.responses import JSONResponse, PlainTextResponse

    report = await svc.get_report(report_id, user_id)
    if not report:
        raise HTTPException(404, "报告不存在")

    if format == "md":
        from app.contexts.report.renderer import render_report_md
        if not report.report_data:
            raise HTTPException(400, "报告尚无结构化数据,无法导出 markdown")
        md = render_report_md(report.report_data)
        return PlainTextResponse(
            md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="report-{report_id[:8]}.md"'},
        )
    # json
    return JSONResponse(
        content={
            "report_id": report_id,
            "task_id": report.task_id,
            "verdict": report.verdict,
            "cvss_score": report.cvss_score,
            "severity": report.severity,
            "vulnerable_file": report.vulnerable_file,
            "report_data": report.report_data,
            "created_at": iso_utc(report.created_at),
        },
        headers={"Content-Disposition": f'attachment; filename="report-{report_id[:8]}.json"'},
    )
