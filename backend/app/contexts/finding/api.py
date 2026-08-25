"""finding context API — 复核台(discovery-spec §9.1)。JWT 鉴权、owner 隔离。

组详情读取时做惰性对账(§4.4 丢事件兜底)：组仍 dispatched 且关联 Task 已有
终态 verdict → 当场回写。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.shared.deps import CurrentUserId

from app.contexts.task.models import Task

from .classification import infer_cwe, vulnerability_title
from .models import Adjudication, AlertGroup, LeadRun, RawFinding
from .presentation import screening_presentation
from .repository import FindingRepository
from .schemas import (
    AdjudicationDetail,
    AlertGroupDetail,
    AlertGroupIdsRequest,
    AlertGroupIdsResponse,
    AlertGroupListRequest,
    AlertGroupListResponse,
    AlertGroupSummary,
    BatchDeleteGroupsRequest,
    BatchDeleteGroupsResponse,
    FindingSummary,
    FindingStatsResponse,
    LeadRunSummary,
    ManualDispatchRequest,
    ManualDispatchResponse,
    ReviewActionDetail,
    ReviewRequest,
    ReviveResponse,
)
from .service import FindingService, numeric_usage

router = APIRouter(prefix="/findings", tags=["findings"])


def _finding_summary(f: RawFinding) -> FindingSummary:
    return FindingSummary(
        id=f.id, engine=f.engine, rule_id=f.rule_id, cwe=f.cwe,
        severity=f.severity, file_path=f.file_path, line_start=f.line_start,
        line_end=f.line_end, message=(f.message or "")[:500],
        source_to_sink=f.source_to_sink, code_snippet=f.code_snippet,
        raw=f.raw if isinstance(f.raw, dict) else None,
    )


async def _get_repo(session: Annotated[AsyncSession, Depends(get_db_session)]) -> FindingRepository:
    return FindingRepository(session)


async def _owner_task_ids(session: AsyncSession, user_id: str, task_id: str | None) -> list[str] | None:
    """owner 隔离：指定 task_id 也必须与当前用户取交集。"""
    if task_id:
        owned = await session.scalar(
            select(Task.id).where(Task.id == task_id, Task.owner_id == user_id)
        )
        return [owned] if owned else ["__none__"]
    rows = await session.execute(select(Task.id).where(Task.owner_id == user_id))
    return [r for r in rows.scalars().all()] or ["__none__"]


def _summary(
    g: AlertGroup,
    task: Task | None = None,
    representative: RawFinding | None = None,
    adjudication: Adjudication | None = None,
) -> AlertGroupSummary:
    engine = representative.engine if representative else ((g.engine_set or [None])[0])
    rule_id = representative.rule_id if representative else ""
    message = representative.message if representative else ""
    effective_cwe = infer_cwe(
        cwe=g.cwe, rule_id=rule_id, message=message, engine=engine or "",
    )
    cwe_source = (
        "scanner" if representative and representative.cwe
        else "inferred" if effective_cwe
        else "missing"
    )
    screening = screening_presentation(g, adjudication)
    return AlertGroupSummary(
        id=g.id, task_id=g.task_id, cwe=effective_cwe, cwe_source=cwe_source,
        file_path=g.file_path,
        vulnerability_title=vulnerability_title(
            cwe=effective_cwe, rule_id=rule_id, message=message, engine=engine or "",
        ),
        representative_rule_id=rule_id or None,
        representative_message=(message or "")[:500] or None,
        severity=representative.severity if representative else None,
        primary_engine=engine,
        screening_status=screening.status,
        screening_summary=screening.summary,
        # 判决 why 是 agent 自由输出，存量行可能混非 str；列表端点同样要能读
        screening_reasons=[str(r) for r in screening.reasons],
        project_id=task.project_id if task else None,
        project_address=task.project_address if task else None,
        project_ref=task.project_ref if task else None,
        audit_created_at=task.created_at if task else None,
        function_symbol=g.function_symbol, line_span=g.line_span,
        member_count=g.member_count or 1, engine_set=g.engine_set or [],
        status=g.status, clue_grade=g.clue_grade, ai_verdict=g.ai_verdict,
        ai_confidence=g.ai_confidence, priority=g.priority, resolution=g.resolution,
        verdict_source=getattr(g, "verdict_source", None),
        created_at=g.created_at, updated_at=g.updated_at,
    )


def _adjudication_detail(a: Adjudication) -> AdjudicationDetail:
    """判决行 → 响应模型。why/evidence/need 是 agent 自由输出，存量行可能
    与 schema 类型不符；此处收敛，避免详情页被单条脏行打挂(原始输出在
    response_text 审计链可查)。"""
    return AdjudicationDetail(
        id=a.id, attempt=a.attempt, verdict=a.verdict,
        confidence=float(a.confidence) if a.confidence is not None else None,
        why=[str(w) for w in (a.why or [])],
        evidence=[e if isinstance(e, dict) else {"detail": str(e)} for e in (a.evidence or [])],
        need=[str(n) for n in (a.need or [])],
        prompt_text=a.prompt_text, response_text=a.response_text,
        usage=numeric_usage(a.usage), created_at=a.created_at,
    )


@router.get("/stats", response_model=FindingStatsResponse)
async def finding_stats(
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> FindingStatsResponse:
    task_ids = await _owner_task_ids(repo.session, user_id, None) or ["__none__"]
    by_status, by_resolution = await repo.group_stats(task_ids)
    return FindingStatsResponse(
        total=sum(by_status.values()),
        by_status=by_status,
        by_resolution=by_resolution,
        by_queue=await repo.group_queue_stats(task_ids),
    )


@router.get("/groups", response_model=AlertGroupListResponse)
async def list_groups(
    req: Annotated[AlertGroupListRequest, Depends()],
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> AlertGroupListResponse:
    total, groups = await repo.list_groups(
        task_id=req.task_id, status=req.status, resolution=req.resolution, cwe=req.cwe,
        ai_verdict=req.ai_verdict, engine=req.engine, clue_grade=req.clue_grade,
        scope=req.scope or "workbench", q=req.q,
        limit=req.limit, offset=req.offset,
        owner_task_ids=await _owner_task_ids(repo.session, user_id, req.task_id),
    )
    task_ids = {g.task_id for g in groups}
    representative_ids = {g.representative_finding_id for g in groups}
    tasks = {}
    representatives = {}
    adjudications = {}
    if task_ids:
        rows = await repo.session.execute(
            select(Task).where(Task.id.in_(task_ids), Task.owner_id == user_id)
        )
        tasks = {task.id: task for task in rows.scalars().all()}
    if representative_ids:
        rows = await repo.session.execute(
            select(RawFinding).where(RawFinding.id.in_(representative_ids))
        )
        representatives = {finding.id: finding for finding in rows.scalars().all()}
    group_ids = {g.id for g in groups}
    if group_ids:
        rows = await repo.session.execute(
            select(Adjudication)
            .where(Adjudication.alert_group_id.in_(group_ids))
            .order_by(Adjudication.alert_group_id, Adjudication.attempt.desc())
        )
        for adjudication in rows.scalars().all():
            adjudications.setdefault(adjudication.alert_group_id, adjudication)
    return AlertGroupListResponse(
        total=total,
        items=[
            _summary(
                g, tasks.get(g.task_id), representatives.get(g.representative_finding_id),
                adjudications.get(g.id),
            )
            for g in groups
        ],
    )


@router.get("/groups/ids", response_model=AlertGroupIdsResponse)
async def list_group_ids(
    req: Annotated[AlertGroupIdsRequest, Depends()],
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> AlertGroupIdsResponse:
    """当前筛选下全部告警组 id（跨页全选）。超出上限返回 400。"""
    try:
        total, ids = await repo.list_group_ids(
            task_id=req.task_id, status=req.status, resolution=req.resolution,
            cwe=req.cwe, ai_verdict=req.ai_verdict, engine=req.engine,
            clue_grade=req.clue_grade, scope=req.scope or "workbench", q=req.q,
            owner_task_ids=await _owner_task_ids(repo.session, user_id, req.task_id),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return AlertGroupIdsResponse(total=total, ids=ids)


@router.post("/groups/batch-delete", response_model=BatchDeleteGroupsResponse)
async def batch_delete_groups(
    request: BatchDeleteGroupsRequest,
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> BatchDeleteGroupsResponse:
    """批量物理删除告警组。非 owner / 不存在 → skipped.not_found；终认进行中 → skipped.in_progress。"""
    svc = FindingService(repo.session)
    deleted, skipped = await svc.delete_groups(request.ids, owner_id=user_id)
    await repo.session.commit()
    return BatchDeleteGroupsResponse(deleted=deleted, skipped=skipped)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> None:
    """物理删除单条告警组（级联判决/复核/LeadRun；保留引擎原始发现）。"""
    svc = FindingService(repo.session)
    deleted, skipped = await svc.delete_groups([group_id], owner_id=user_id)
    if deleted:
        await repo.session.commit()
        return
    reason = skipped[0]["reason"] if skipped else "not_found"
    if reason == "in_progress":
        raise HTTPException(409, "该线索正在终认中，请待结束后再删")
    raise HTTPException(404, "告警组不存在")


@router.get("/groups/{group_id}", response_model=AlertGroupDetail)
async def get_group_detail(
    group_id: str,
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> AlertGroupDetail:
    svc = FindingService(repo.session)
    group = await svc.get_group(group_id)
    if group is None:
        raise HTTPException(404, "告警组不存在")
    task = await repo.session.get(Task, group.task_id)
    if task is None or task.owner_id != user_id:
        raise HTTPException(404, "告警组不存在")

    # 惰性对账(§4.4)：dispatched 组按关联验证 Task 最新状态当场回写
    verification_task = None
    if getattr(task, "source_alert_group_id", None) == group.id:
        verification_task = task
    else:
        rows = await repo.session.execute(
            select(Task).where(Task.source_alert_group_id == group.id)
        )
        candidates = [t for t in rows.scalars().all() if t.owner_id == user_id]
        verification_task = candidates[0] if candidates else None
    if verification_task is not None and group.status == "dispatched":
        await svc.reconcile_from_task(verification_task)
        await repo.session.commit()

    members = await repo.list_group_members(group)
    rep = await repo.get_representative(group)
    adjudications = await svc.list_adjudications(group.id)
    reviews = await repo.list_reviews(group.id)
    lead_rows = await repo.session.execute(
        select(LeadRun)
        .where(LeadRun.alert_group_id == group.id)
        .order_by(LeadRun.created_at.desc())
    )
    lead_runs = list(lead_rows.scalars().all())
    return AlertGroupDetail(
        **_summary(group, task, rep, adjudications[-1] if adjudications else None).model_dump(),
        members=[_finding_summary(f) for f in members],
        representative=_finding_summary(rep) if rep else None,
        adjudications=[_adjudication_detail(a) for a in adjudications],
        reviews=[
            ReviewActionDetail(
                id=r.id, action=r.action, reason_tags=r.reason_tags or [],
                reason_text=r.reason_text, user_id=r.user_id, created_at=r.created_at,
            )
            for r in reviews
        ],
        lead_runs=[
            LeadRunSummary(
                id=lead.id, status=lead.status, verdict=lead.verdict,
                gate_verdict=lead.gate_verdict, error=lead.error,
                created_at=lead.created_at, updated_at=lead.updated_at,
            )
            for lead in lead_runs
        ],
        verification_task_id=verification_task.id if verification_task else None,
        verification_verdict=getattr(verification_task, "verdict", None) if verification_task else None,
    )


@router.post("/groups/{group_id}/review", response_model=AlertGroupSummary)
async def review_group(
    group_id: str,
    request: ReviewRequest,
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> AlertGroupSummary:
    """人工复核：动作即标注数据，实时入案例库(KNW-01)。"""
    svc = FindingService(repo.session)
    group = await svc.get_group(group_id)
    if group is None:
        raise HTTPException(404, "告警组不存在")
    task = await repo.session.get(Task, group.task_id)
    if task is None or task.owner_id != user_id:
        raise HTTPException(404, "告警组不存在")

    await svc.record_review_action(
        group_id=group.id, user_id=user_id, action=request.action,
        reason_tags=request.reason_tags, reason_text=request.reason_text,
    )
    if request.action == "confirm":
        await svc.mark_resolved(group, "confirmed")
    elif request.action == "reject":
        await svc.mark_resolved(group, "false_positive")
    elif request.action == "revise_cwe":
        group.cwe = request.cwe
    elif request.action == "adjust_confidence":
        group.ai_confidence = request.confidence
    await repo.session.commit()
    return _summary(group, task, await repo.get_representative(group))


@router.post("/groups/{group_id}/revive", response_model=ReviveResponse)
async def revive_group(
    group_id: str,
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> ReviveResponse:
    """FP 误杀护栏：AI 判 FP 的组一键复活回复核队列。"""
    svc = FindingService(repo.session)
    group = await svc.get_group(group_id)
    if group is None:
        raise HTTPException(404, "告警组不存在")
    task = await repo.session.get(Task, group.task_id)
    if task is None or task.owner_id != user_id:
        raise HTTPException(404, "告警组不存在")
    await svc.record_review_action(
        group_id=group.id, user_id=user_id, action="revive",
    )
    await svc.revive(group)
    await repo.session.commit()
    return ReviveResponse(id=group.id, status=group.status)


@router.post("/groups/{group_id}/dispatch", response_model=ManualDispatchResponse)
async def manual_dispatch(
    group_id: str,
    request: ManualDispatchRequest,
    repo: Annotated[FindingRepository, Depends(_get_repo)],
    user_id: CurrentUserId,
) -> ManualDispatchResponse:
    """人工发起定向验证：另开 task_type=verify(任务派生，非自动路径)。

    B 级允许人工放行(§9.1)；描述复用 dispatch 节点模板；勾选才追加引擎原文。
    """
    from app.contexts.finding.hypothesis import build_lead_description
    from app.contexts.finding.models import Adjudication
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.schemas import TaskCreateRequest
    from app.contexts.task.service import TaskDispatchError, TaskService

    svc = FindingService(repo.session)
    group = await svc.get_group(group_id)
    if group is None:
        raise HTTPException(404, "告警组不存在")
    task = await repo.session.get(Task, group.task_id)
    if task is None or task.owner_id != user_id:
        raise HTTPException(404, "告警组不存在")
    if group.status == "dispatched":
        # 幂等守卫：重复点击会创建第二个 verify 任务并覆盖 source_alert_group 指针
        raise HTTPException(409, "该告警组已投递验证任务，请勿重复投递")
    rep = await repo.get_representative(group)
    if rep is None:
        raise HTTPException(409, "组缺少代表成员，无法组装验证描述")
    adj_rows = await repo.session.execute(
        select(Adjudication)
        .where(Adjudication.alert_group_id == group.id)
        .order_by(Adjudication.attempt.desc())
    )
    adj = adj_rows.scalars().first()
    description = build_lead_description(group=group, representative=rep, adjudication=adj)
    if request.include_engine_conclusion:
        description += f"\n【引擎线索】{rep.rule_id}: {(rep.message or '')[:500]}"

    try:
        detail = await TaskService(TaskRepository(repo.session)).create_task(
            TaskCreateRequest(
                project_address=task.project_address,
                project_ref=task.project_ref,
                project_ref_type=task.project_ref_type,
                clone_depth=task.clone_depth,
                source_type=task.source_type,
                task_type="verify",
                vulnerability_description=description,
                priority=task.priority or "medium",
                credential_refs=[],
            ),
            user_id,
        )
    except TaskDispatchError:
        raise HTTPException(502, "验证任务已创建但投递失败，请稍后重试")
    except ValueError as e:
        raise HTTPException(409, str(e))

    await svc.record_review_action(
        group_id=group.id, user_id=user_id, action="dispatch",
        reason_text=f"manual_dispatch→{detail.id}",
    )
    await svc.mark_dispatched(group)
    from app.contexts.task.service import TaskService as _TS

    await _TS(TaskRepository(repo.session)).set_source_alert_group(detail.id, group.id)
    await repo.session.commit()
    return ManualDispatchResponse(group_id=group.id, verification_task_id=detail.id)
