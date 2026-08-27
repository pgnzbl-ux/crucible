"""
报告生成服务。

职责：
- 从 Agent 分析结果生成结构化报告（结论 + 推理 + 证据摘要）
- 报告产物归档到 MinIO（JSON artifact）
- 状态机：draft → generated → published

被谁调用：
- Agent 任务完成时（celery worker 内）
- 报告 API（查询 / 发布）
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.shared.object_store import (
    ObjectRef,
    ObjectStoreError,
    UnsafeKeyError,
    get_object_store,
)
from .models import Evidence, Report
from .repository import ReportRepository
from .schemas import EvidenceResponse, ReportDetail, ReportSummary

# 中文结论标签
CONCLUSION_LABELS = {
    "exists": "漏洞确认存在",
    "not_exists": "漏洞不存在（误报）",
    "unconfirmed": "无法确认，需人工复核",
}


def _flatten_file_name(file_name: str) -> str:
    raw = (file_name or "file").replace("\\", "/")
    parts = [p for p in raw.split("/") if p and p not in {".", ".."}]
    return "_".join(parts) or "file"


class ReportService:
    def __init__(self, repo: ReportRepository):
        self.repo = repo

    # ── 序列化（避免 async lazy-load） ──

    @staticmethod
    def _to_detail(report: Report) -> ReportDetail:
        # report_data 存为 Text(JSON 字符串),解析成 dict 供前端
        report_data_dict: dict[str, Any] | None = None
        if report.report_data:
            try:
                report_data_dict = (
                    json.loads(report.report_data)
                    if isinstance(report.report_data, str)
                    else report.report_data
                )
            except (json.JSONDecodeError, TypeError):
                report_data_dict = None
        return ReportDetail(
            id=report.id,
            task_id=report.task_id,
            run_id=report.run_id,
            owner_id=report.owner_id,
            status=report.status,
            conclusion=report.conclusion,
            title=report.title,
            summary=report.summary,
            reasoning=report.reasoning,
            evidence_summary=report.evidence_summary,
            artifact_key=report.artifact_key,
            verdict=report.verdict,
            cvss_score=report.cvss_score,
            severity=report.severity,
            vulnerable_file=report.vulnerable_file,
            product_name=report.product_name,
            affected_version=report.affected_version,
            project_address=report.project_address,
            report_data=report_data_dict,
            md_artifact_key=report.md_artifact_key,
            docx_artifact_key=report.docx_artifact_key,
            poc_language=report.poc_language,
            poc_filename=report.poc_filename,
            poc_code=report.poc_code,
            poc_usage=report.poc_usage,
            published_at=report.published_at,
            created_at=report.created_at,
            updated_at=report.updated_at,
            evidence=[ReportService._to_evidence_detail(e, with_url=True) for e in report.evidence],
        )

    @staticmethod
    def _to_summary(report: Report, task_context: dict[str, Any] | None = None) -> ReportSummary:
        document_kind: str | None = None
        try:
            payload = json.loads(report.report_data or "{}")
            if isinstance(payload, dict) and isinstance(payload.get("document_kind"), str):
                document_kind = payload["document_kind"]
        except (json.JSONDecodeError, TypeError):
            pass
        context = task_context or {}
        return ReportSummary.model_validate(report).model_copy(update={
            "project_address": context.get("project_address"),
            "project_ref": context.get("project_ref"),
            "task_type": context.get("task_type"),
            "document_kind": document_kind,
        })

    @staticmethod
    def _to_evidence_detail(ev: Evidence, *, with_url: bool = False) -> EvidenceResponse:
        """Evidence ORM → EvidenceResponse，可选附预签名下载 URL"""
        download_url: str | None = None
        if with_url:
            try:
                download_url = get_object_store().presign(
                    ObjectRef(kind="evidence", bucket=ev.bucket, key=ev.object_key)
                )
            except Exception:
                download_url = None
        return EvidenceResponse(
            id=ev.id,
            object_key=ev.object_key,
            bucket=ev.bucket,
            file_name=ev.file_name,
            content_type=ev.content_type,
            size_bytes=ev.size_bytes,
            kind=ev.kind,
            created_at=ev.created_at,
            download_url=download_url,
        )

    # ── 生成 ──

    # 6 节点编排后在 tasks.py 内联建 Report，此方法遗留无调用方。
    async def generate_from_agent(
        self,
        *,
        task_id: str,
        run_id: str,
        owner_id: str,
        conclusion: str,
        reasoning: str,
        events: list[dict[str, Any]],
    ) -> Report:
        """Agent 分析完成后生成报告（在 celery worker 中调用）"""
        report = Report(
            task_id=task_id,
            run_id=run_id,
            owner_id=owner_id,
            conclusion=conclusion if conclusion in CONCLUSION_LABELS else "unconfirmed",
            title=f"安全分析报告 — {task_id[:8]}",
            summary=CONCLUSION_LABELS.get(conclusion, conclusion),
            reasoning=reasoning[:50000] or None,
            evidence_summary=json.dumps(
                {"events_count": len(events), "conclusion": conclusion},
                ensure_ascii=False,
            ),
            status="generated",
        )
        report = await self.repo.create(report)

        # 归档报告产物到 MinIO
        try:
            artifact = {
                "report_id": report.id,
                "task_id": task_id,
                "run_id": run_id,
                "conclusion": conclusion,
                "reasoning": reasoning,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            payload = json.dumps(artifact, ensure_ascii=False).encode("utf-8")
            ref = get_object_store().put(
                "report",
                owner_id,
                payload,
                content_type="application/json",
                task_id=task_id,
                report_id=report.id,
            )
            report.artifact_key = ref.key
            await self.repo.session.flush()
        except Exception:
            pass  # 归档失败不影响报告生成

        return report

    # ── 查询 ──

    async def get_report(
        self, report_id: str, owner_id: str
    ) -> ReportDetail | None:
        report = await self.repo.get_by_id(report_id, owner_id)
        return self._to_detail(report) if report else None

    async def get_report_by_task(
        self, task_id: str, owner_id: str
    ) -> ReportDetail | None:
        report = await self.repo.get_by_task(task_id, owner_id)
        return self._to_detail(report) if report else None

    async def list_reports(
        self,
        owner_id: str,
        status: str | None = None,
        verdict: str | None = None,
        query: str | None = None,
        task_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ReportSummary], int]:
        rows, total = await self.repo.list_by_owner(
            owner_id, status, verdict, query, task_type=task_type, limit=limit, offset=offset,
        )
        contexts: dict[str, dict[str, Any]] = {}
        if rows:
            from sqlalchemy import select

            from app.contexts.task.models import Task

            result = await self.repo.session.execute(
                select(Task.id, Task.project_address, Task.project_ref, Task.task_type).where(
                    Task.id.in_([row.task_id for row in rows]),
                    Task.owner_id == owner_id,
                )
            )
            contexts = {
                task_id: {
                    "project_address": project_address,
                    "project_ref": project_ref,
                    "task_type": task_type_val,
                }
                for task_id, project_address, project_ref, task_type_val in result.all()
            }
        return [self._to_summary(r, contexts.get(r.task_id)) for r in rows], total

    async def list_audit_tasks(
        self,
        owner_id: str,
        *,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        from sqlalchemy import case, func, or_, select

        from app.contexts.finding.models import AlertGroup
        from app.contexts.report.schemas import AuditTaskSummary
        from app.contexts.task.models import Task
        from app.contexts.report.models import Report

        session = self.repo.session
        confirmed_expr = func.coalesce(func.sum(case(
            (AlertGroup.resolution == "confirmed", 1), else_=0,
        )), 0)
        reachable_expr = func.coalesce(func.sum(case(
            (AlertGroup.resolution == "code_reachable", 1), else_=0,
        )), 0)
        vuln_expr = func.coalesce(func.sum(case(
            (AlertGroup.vuln_report.is_not(None), 1), else_=0,
        )), 0)
        latest_report_id = (
            select(Report.id)
            .where(Report.task_id == Task.id)
            .order_by(Report.created_at.desc(), Report.id.desc())
            .limit(1)
            .correlate(Task)
            .scalar_subquery()
        )

        base = (
            select(
                Task.id.label("task_id"),
                Task.project_id,
                Task.project_address,
                Task.project_ref,
                Task.status.label("task_status"),
                Task.created_at,
                Task.updated_at,
                Report.id.label("report_id"),
                Report.status.label("report_status"),
                Report.published_at,
                confirmed_expr.label("confirmed_count"),
                reachable_expr.label("code_reachable_count"),
                vuln_expr.label("vuln_report_count"),
            )
            .outerjoin(Report, Report.id == latest_report_id)
            .outerjoin(AlertGroup, AlertGroup.task_id == Task.id)
            .where(Task.owner_id == owner_id, Task.task_type == "discovery")
            .group_by(
                Task.id, Task.project_id, Task.project_address, Task.project_ref,
                Task.status, Task.created_at, Task.updated_at,
                Report.id, Report.status, Report.published_at,
            )
        )
        if query:
            pattern = f"%{query.strip()}%"
            base = base.where(or_(
                Task.project_address.ilike(pattern),
                Task.project_ref.ilike(pattern),
                Task.id.ilike(pattern),
            ))
        # 有任务级 Report 或至少一份单漏洞报告才出现在列表
        having = or_(Report.id.is_not(None), vuln_expr > 0)
        base = base.having(having)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0
        rows = (
            await session.execute(
                base.order_by(Task.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
        items = [
            AuditTaskSummary(
                task_id=r.task_id,
                project_id=r.project_id,
                project_address=r.project_address,
                project_ref=r.project_ref,
                task_status=r.task_status,
                report_id=r.report_id,
                report_status=r.report_status,
                confirmed_count=int(r.confirmed_count or 0),
                code_reachable_count=int(r.code_reachable_count or 0),
                vuln_report_count=int(r.vuln_report_count or 0),
                created_at=r.created_at,
                updated_at=r.updated_at,
                published_at=r.published_at,
            )
            for r in rows
        ]
        return items, total

    async def get_audit_task(self, task_id: str, owner_id: str):
        items, _ = await self.list_audit_tasks(owner_id, query=None, limit=200, offset=0)
        for item in items:
            if item.task_id == task_id:
                return item
        # 无 Report/vuln 时仍允许按 owner 查任务本身（详情页眉）
        from sqlalchemy import select

        from app.contexts.finding.models import AlertGroup
        from app.contexts.report.schemas import AuditTaskSummary
        from app.contexts.task.models import Task

        task = await self.repo.session.scalar(
            select(Task).where(Task.id == task_id, Task.owner_id == owner_id, Task.task_type == "discovery")
        )
        if task is None:
            return None
        groups = list((await self.repo.session.execute(
            select(AlertGroup).where(AlertGroup.task_id == task_id)
        )).scalars().all())
        report = await self.repo.get_by_task(task_id, owner_id)
        return AuditTaskSummary(
            task_id=task.id,
            project_id=task.project_id,
            project_address=task.project_address,
            project_ref=task.project_ref,
            task_status=task.status,
            report_id=report.id if report else None,
            report_status=report.status if report else None,
            confirmed_count=sum(1 for g in groups if g.resolution == "confirmed"),
            code_reachable_count=sum(1 for g in groups if g.resolution == "code_reachable"),
            vuln_report_count=sum(1 for g in groups if isinstance(g.vuln_report, dict)),
            created_at=task.created_at,
            updated_at=task.updated_at,
            published_at=report.published_at if report else None,
        )

    async def list_vuln_reports_for_task(self, task_id: str, owner_id: str):
        from sqlalchemy import select

        from app.contexts.finding.models import AlertGroup
        from app.contexts.report.schemas import VulnReportSummary
        from app.contexts.task.models import Task

        task = await self.repo.session.scalar(
            select(Task).where(Task.id == task_id, Task.owner_id == owner_id, Task.task_type == "discovery")
        )
        if task is None:
            return None
        groups = list((await self.repo.session.execute(
            select(AlertGroup)
            .where(AlertGroup.task_id == task_id, AlertGroup.vuln_report.is_not(None))
            .order_by(AlertGroup.updated_at.desc())
        )).scalars().all())
        items: list[VulnReportSummary] = []
        for g in groups:
            report = g.vuln_report if isinstance(g.vuln_report, dict) else {}
            items.append(VulnReportSummary(
                alert_group_id=g.id,
                task_id=task_id,
                summary=str(report.get("summary") or g.file_path or "漏洞报告"),
                final_verdict=report.get("final_verdict") or g.resolution,
                verification_basis=report.get("verification_basis") or g.verification_basis,
                primary_engine=report.get("primary_engine") or ((g.engine_set or [None])[0]),
                cwe=g.cwe,
                file_path=g.file_path,
                generated_at=report.get("generated_at"),
            ))
        return items

    async def get_vuln_report(self, task_id: str, group_id: str, owner_id: str) -> dict[str, Any] | None:
        from sqlalchemy import select

        from app.contexts.finding.models import AlertGroup
        from app.contexts.task.models import Task

        task = await self.repo.session.scalar(
            select(Task).where(Task.id == task_id, Task.owner_id == owner_id, Task.task_type == "discovery")
        )
        if task is None:
            return None
        group = await self.repo.session.scalar(
            select(AlertGroup).where(AlertGroup.id == group_id, AlertGroup.task_id == task_id)
        )
        if group is None or not isinstance(group.vuln_report, dict):
            return None
        return group.vuln_report

    # ── 发布 ──

    async def publish_report(
        self, report_id: str, owner_id: str
    ) -> ReportDetail | None:
        report = await self.repo.get_by_id(report_id, owner_id)
        if not report:
            return None
        if report.status == "draft":
            raise ValueError("草稿报告不能发布")
        report.status = "published"
        report.published_at = datetime.now(timezone.utc)
        await self.repo.session.flush()
        return self._to_detail(report)

    # ── 证据（Evidence） ──

    async def attach_evidence(
        self,
        *,
        report_id: str,
        owner_id: str,
        file_name: str,
        content_type: str,
        data: bytes,
        kind: str = "artifact",
        report: Report | None = None,
    ) -> tuple[EvidenceResponse | None, str | None]:
        """给报告追加一个证据文件：上传 MinIO + 落 evidences 表。

        返回 (evidence, error)。report 不存在或越权返回 (None, error)。
        归档循环可传入已加载的 report，避免每文件再查一次。
        """
        if report is None:
            report = await self.repo.get_by_id(report_id, owner_id)
        elif report.id != report_id or report.owner_id != owner_id:
            return None, "报告不存在"
        if not report:
            return None, "报告不存在"
        if report.status == "published":
            return None, "报告已发布，不能追加证据"
        # kind 白名单（防任意值落库）
        if kind not in ("artifact", "log", "screenshot", "poc"):
            return None, "非法证据类型"

        safe_name = _flatten_file_name(file_name)
        evidence_id = str(uuid.uuid4())
        try:
            ref = get_object_store().put(
                "evidence",
                owner_id,
                data,
                content_type=content_type,
                task_id=report.task_id,
                evidence_id=evidence_id,
                file_name=safe_name,
            )
        except (ObjectStoreError, UnsafeKeyError) as e:
            return None, str(e)

        evidence = Evidence(
            id=evidence_id,
            report_id=report_id,
            task_id=report.task_id,
            object_key=ref.key,
            bucket=ref.bucket,
            file_name=safe_name[:255],
            content_type=content_type[:128],
            size_bytes=len(data),
            kind=kind,
        )
        evidence = await self.repo.add_evidence(evidence)
        return self._to_evidence_detail(evidence, with_url=True), None

    async def list_evidence(
        self, report_id: str, owner_id: str
    ) -> list[EvidenceResponse] | None:
        evidences = await self.repo.list_evidence(report_id, owner_id)
        if evidences is None:
            return None
        return [self._to_evidence_detail(e, with_url=True) for e in evidences]
