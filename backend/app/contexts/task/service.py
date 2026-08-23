from datetime import datetime, timedelta, timezone
from typing import Any
import json
import logging

from sqlalchemy import select, update

from app.shared.time import iso_utc

from .models import Task, TaskRun, AgentEvent
from .repository import TaskRepository
from .schemas import TaskCreateRequest, TaskDetail, TaskListResponse, TaskStatsResponse, TaskSummary, RunSummary

logger = logging.getLogger(__name__)


class TaskDispatchError(RuntimeError):
    """任务已落库，但无法投递给 Agent worker。"""


# 节点顺序 → 索引(从 registry 派生，discovery-spec §4.2.4 后 DEFAULT_PIPELINE 是 12 节点)。
# 单节点重试不允许从 source/profile 起步——它们便宜且确定性，想重跑直接整轮 retry(from_node=None)。
from app.contexts.agent.contracts import DEFAULT_PIPELINE as _PIPELINE
from app.contexts.agent.contracts import SkipWhen as _SkipWhen

_NODE_INDEX = {spec.key: spec.index for spec in _PIPELINE}
_RETRYABLE_FROM_NODES = tuple(
    spec.key for spec in _PIPELINE if spec.key not in ("source", "profile")
)
# verify 任务会被 VERIFY_MODE skip 的节点：旧 run 缺行可容忍(新 run 重新 skip)
_VERIFY_MODE_SKIP_KEYS = frozenset(
    spec.key for spec in _PIPELINE if _SkipWhen.VERIFY_MODE in spec.skip_when
)
# Lab 占用：pending/queued/running。needs_review 不算 live（不续 TTL）。
LIVE_TASK_STATUSES = frozenset({"pending", "queued", "running"})
# 前置节点视为"可续跑"的终态:completed(有产出可复用)或 skipped(分支出口跳过)。
_RESUMABLE_STATUSES = ("completed", "skipped")


def _parse_refs(refs_raw: str | None) -> list[str]:
    """credential_refs JSON 字符串 → list（容错）"""
    if not refs_raw:
        return []
    try:
        v = json.loads(refs_raw)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _parse_node_output(raw: str | None) -> dict[str, Any]:
    """NodeRun.output_json → dict；坏 JSON 给空对象，前端仍能画步骤。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serialize_node_run(nr: Any) -> dict[str, Any]:
    return {
        "id": nr.id,
        "node_index": nr.node_index,
        "node_key": nr.node_key,
        "status": nr.status,
        "attempt": nr.attempt,
        "error_message": nr.error_message,
        "started_at": iso_utc(nr.started_at),
        "finished_at": iso_utc(nr.finished_at),
        "output": _parse_node_output(nr.output_json),
    }


class TaskService:
    """任务业务逻辑编排"""

    def __init__(self, repo: TaskRepository):
        self.repo = repo
        self.session = repo.session
    @staticmethod
    def _to_detail(task: Task, runs: list[TaskRun] | None = None) -> TaskDetail:
        """安全地将 Task ORM 对象转为 TaskDetail，避免 async lazy-load 问题"""
        return TaskDetail(
            id=task.id,
            project_address=task.project_address,
            project_id=getattr(task, "project_id", None),
            project_ref=task.project_ref,
            project_ref_type=getattr(task, "project_ref_type", None),
            clone_depth=getattr(task, "clone_depth", None),
            source_type=task.source_type,
            task_type=getattr(task, "task_type", None) or "verify",
            source_alert_group_id=getattr(task, "source_alert_group_id", None),
            vulnerability_description=task.vulnerability_description or "",
            vulnerability_reasoning=task.vulnerability_reasoning,
            status=task.status,
            verdict=getattr(task, "verdict", None),
            priority=task.priority,
            owner_id=task.owner_id,
            credential_refs=_parse_refs(getattr(task, "credential_refs", None)),
            created_at=task.created_at,
            updated_at=task.updated_at,
            runs=[RunSummary.model_validate(r) for r in (runs or [])],
        )

    async def _require_platform_ready(self) -> None:
        from app.contexts.agent.preflight import require_platform_ready

        await require_platform_ready(self.session)

    async def create_task(self, request: TaskCreateRequest, owner_id: str) -> TaskDetail:
        from app.contexts.project.git_url import parse_git_url
        from app.contexts.project.repository import ProjectRepository
        from app.contexts.project.service import ProjectService
        from app.contexts.project.source_upload import parse_upload_locator

        source_type = request.source_type or "git"
        project_svc = ProjectService(ProjectRepository(self.repo.session))
        if source_type == "local_upload":
            parsed = parse_upload_locator(request.project_address)
            stored_address = parsed.normalized
            project = await project_svc.repo.get_by_git_url(stored_address, owner_id)
            if project is None or getattr(project, "source_type", "git") != "local_upload":
                raise ValueError("上传源码不存在，请重新上传源码包")
            cached = await project_svc.find_cached_source(
                stored_address, None, owner_id, ref_type="upload"
            )
            if cached is None:
                raise ValueError("上传源码包尚未入库，请重新上传")
            await self._require_platform_ready()
        else:
            stored_address = parse_git_url(request.project_address).normalized
            await self._require_platform_ready()
            project = await project_svc.upsert_by_git_url(
                git_url=stored_address,
                owner_id=owner_id,
                default_ref=request.project_ref,
            )

        task = Task(
            project_address=stored_address,
            project_id=project.id,
            project_ref=request.project_ref,
            project_ref_type=request.project_ref_type,
            clone_depth=request.clone_depth,
            source_type=request.source_type,
            task_type=request.task_type,
            vulnerability_description=request.vulnerability_description,
            vulnerability_reasoning=request.vulnerability_reasoning,
            priority=request.priority,
            status="queued",
            owner_id=owner_id,
            credential_refs=json.dumps(request.credential_refs or [], ensure_ascii=False),
        )
        task = await self.repo.create(task)

        # 创建首次运行记录并异步提交给 Agent worker
        run = TaskRun(task_id=task.id, status="pending")
        run = await self.repo.create_run(run)
        await self.repo.session.commit()

        # 触发 Celery 任务（agent context）— 不阻塞创建响应
        # 用 run.id 作为 celery task_id，cancel 时可精确 revoke（terminate → SIGTERM → 容器销毁）
        from app.core.celery_app import celery_app

        try:
            celery_app.send_task("agent.run_analysis", args=[task.id, run.id], task_id=run.id)
        except Exception as exc:
            task.status = "failed"
            run.status = "failed"
            run.error_message = f"任务投递失败: {exc}"[:1000]
            run.finished_at = datetime.now(timezone.utc)
            await self.repo.session.commit()
            raise TaskDispatchError("任务已创建，但投递 Agent worker 失败") from exc

        return self._to_detail(task, [run])

    async def create_task_from_upload(
        self,
        *,
        owner_id: str,
        filename: str,
        data: bytes,
        vulnerability_description: str | None,
        task_type: str = "verify",
        name: str | None = None,
        priority: str = "medium",
        vulnerability_reasoning: str | None = None,
        credential_refs: list[str] | None = None,
    ) -> TaskDetail:
        """上传源码包并创建审计或定向验证任务。同名项目已存在则 409。"""
        from app.contexts.project.repository import ProjectRepository
        from app.contexts.project.service import ProjectService

        request = TaskCreateRequest(
            project_address="upload://local/pending",
            source_type="local_upload",
            task_type=task_type,
            vulnerability_description=vulnerability_description,
            vulnerability_reasoning=vulnerability_reasoning,
            priority=priority,
            credential_refs=credential_refs or [],
            clone_depth=None,
        )

        await self._require_platform_ready()
        project, _result = await ProjectService(
            ProjectRepository(self.repo.session)
        ).ingest_uploaded_source(
            owner_id=owner_id,
            filename=filename,
            data=data,
            name=name,
        )
        request = request.model_copy(update={"project_address": project.git_url})
        return await self.create_task(request, owner_id)

    async def redispatch_stale_queued(
        self, *, min_age_seconds: int = 60, now: datetime | None = None
    ) -> list[str]:
        """commit 成功但 send_task 未发出时，按原 run.id 再投递。"""
        clock = now or datetime.now(timezone.utc)
        cutoff = clock - timedelta(seconds=min_age_seconds)
        result = await self.repo.session.execute(
            select(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                Task.status == "queued",
                TaskRun.status == "pending",
                TaskRun.created_at <= cutoff,
            )
        )
        runs = list(result.scalars().all())
        if not runs:
            return []

        from app.core.celery_app import celery_app

        dispatched: list[str] = []
        for run in runs:
            try:
                celery_app.send_task(
                    "agent.run_analysis", args=[run.task_id, run.id], task_id=run.id
                )
                dispatched.append(run.id)
            except Exception:
                logger.warning("滞留 queued 再投递失败 run=%s", run.id, exc_info=True)
        if dispatched:
            logger.info("滞留 queued 再投递 %s 个 run: %s", len(dispatched), dispatched)
        return dispatched

    async def get_task(self, task_id: str, owner_id: str) -> TaskDetail | None:
        from app.contexts.agent.usage_ledger import task_usage_summary

        task = await self.repo.get_by_id_with_runs(task_id, owner_id)
        if not task:
            return None
        detail = self._to_detail(task, task.runs)
        detail.usage = await task_usage_summary(self.repo.session, task_id)
        return detail

    async def list_tasks(
        self, owner_id: str, status: str | None = None,
        priority: str | None = None, limit: int = 50, offset: int = 0,
        q: str | None = None, date_from: str | None = None, date_to: str | None = None,
        task_type: str | None = None,
    ) -> TaskListResponse:
        tasks, total = await self.repo.list_by_owner(
            owner_id, status, priority, limit, offset, q=q, date_from=date_from,
            date_to=date_to, task_type=task_type,
        )
        task_ids = [task.id for task in tasks]
        finding_counts: dict[str, tuple[int, int, int]] = {}
        report_statuses: dict[str, str] = {}
        if task_ids:
            from sqlalchemy import case, func, select

            from app.contexts.finding.models import AlertGroup
            from app.contexts.report.models import Report

            finding_rows = await self.session.execute(
                select(
                    AlertGroup.task_id,
                    func.count(AlertGroup.id),
                    func.sum(case((AlertGroup.status == "needs_review", 1), else_=0)),
                    func.sum(case((AlertGroup.resolution == "confirmed", 1), else_=0)),
                )
                .where(AlertGroup.task_id.in_(task_ids))
                .group_by(AlertGroup.task_id)
            )
            finding_counts = {
                task_id: (int(total_count or 0), int(pending or 0), int(confirmed or 0))
                for task_id, total_count, pending, confirmed in finding_rows.all()
            }
            report_rows = await self.session.execute(
                select(Report.task_id, Report.status)
                .where(Report.task_id.in_(task_ids), Report.owner_id == owner_id)
                .order_by(Report.created_at.desc())
            )
            for task_id, report_status in report_rows.all():
                report_statuses.setdefault(task_id, report_status)

        summaries = []
        for task in tasks:
            total_findings, pending_review, confirmed = finding_counts.get(task.id, (0, 0, 0))
            summaries.append(TaskSummary.model_validate(task).model_copy(update={
                "finding_count": total_findings,
                "pending_review_count": pending_review,
                "confirmed_count": confirmed,
                "report_status": report_statuses.get(task.id),
            }))
        return TaskListResponse(
            items=summaries,
            total=total, limit=limit, offset=offset,
        )

    async def task_stats(self, owner_id: str) -> TaskStatsResponse:
        by_status = await self.repo.count_by_status(owner_id)
        return TaskStatsResponse(total=sum(by_status.values()), by_status=by_status)

    async def cancel_task(self, task_id: str, owner_id: str) -> TaskDetail | None:
        """取消任务：状态机校验 → 标 cancelled 并提交 → revoke → 后台拆容器。

        HTTP 不能等 `docker compose down`（build 中可能卡住数分钟），否则前端取消按钮
        一直转圈，且 cancelled 未提交时 worker 会把状态写回 running/failed。
        revoke(terminate=True) 发送 SIGTERM；后台 teardown 先拆 agent-runner 停 AI，编排器刷新到 cancelled 后停后续节点。
        """
        task = await self.repo.get_by_id_with_runs(task_id, owner_id)
        if not task:
            return None
        if task.status not in ("pending", "queued", "running"):
            raise ValueError(f"状态为 {task.status} 的任务不能取消")

        # 1. 标记所有活跃 run 为 cancelled（并取出列表用于 revoke）
        cancelled_runs = await self.repo.cancel_active_runs(task_id)
        await self.repo.cancel_incomplete_nodes([run.id for run in cancelled_runs])

        # 2. revoke 对应 celery 任务（task_id = run.id，见 create_task）
        from app.core.celery_app import celery_app

        for run in cancelled_runs:
            try:
                celery_app.control.revoke(run.id, terminate=True, signal="SIGTERM")
            except Exception:
                # broker 不可用不能阻断 DB 状态更新（任务已标 cancelled，worker 侧 SIGTERM 钩子兜底）
                pass

        # 3. 任务整体状态 → cancelled，先提交让 worker 刷新能看见
        await self.repo.update_status(task, "cancelled")
        await self.repo.session.commit()

        # 4. 创建中的 Lab 若失去创建者，标记失败供其他任务回收
        from app.contexts.lab.service import LabService

        await LabService(self.session).mark_creator_cancelled(task_id)

        # 5. 只拆 agent-runner（后台；HTTP 立刻返回）
        from app.contexts.agent.runtime_cleanup import schedule_teardown_task_runtime

        schedule_teardown_task_runtime(task_id)

        return self._to_detail(task, task.runs)

    async def get_task_events(
        self, task_id: str, owner_id: str, limit: int = 1000
    ) -> list[dict[str, Any]] | None:
        """当前 run 的 Agent 事件流（前端进度展示用；不含历史重试）"""
        if await self.repo.get_by_id_with_runs(task_id, owner_id) is None:
            return None
        events = await self.repo.get_events_for_task(task_id, limit)
        result: list[dict[str, Any]] = []
        for ev in events:
            try:
                import json as _json

                payload = _json.loads(ev.payload)
            except (ValueError, TypeError):
                payload = {}
            result.append(
                {
                    "id": ev.id,
                    "run_id": ev.run_id,
                    "sequence": ev.sequence,
                    "event_type": ev.event_type,
                    "payload": payload,
                    "source": ev.source,
                    "created_at": iso_utc(ev.created_at),
                }
            )
        return result

    async def list_live_ids(self, lab_id: str) -> list[str]:
        mapping = await self.list_live_ids_by_lab_ids([lab_id])
        return mapping.get(lab_id, [])

    async def list_live_ids_by_lab_ids(self, lab_ids: list[str]) -> dict[str, list[str]]:
        return await self.repo.list_live_ids_by_lab_ids(lab_ids, LIVE_TASK_STATUSES)

    async def bind_lab(self, task_id: str, lab_id: str, *, commit: bool = True) -> None:
        task = await self.repo.get_by_id(task_id)
        if task is None:
            raise LookupError(f"Task 不存在: {task_id}")
        task.lab_id = lab_id
        if commit:
            await self.session.commit()

    async def retry_task(
        self, task_id: str, owner_id: str, from_node: str | None = None
    ) -> str:
        """重试任务:新建 TaskRun，返回新 run_id。

        from_node 为空:从节点 0（源码获取）整条重跑，不拷贝上一 run 的 NodeRun。
        from_node 指定(env_ready/audit/reproduce/report):把上一 run 里该节点**之前**的
        completed/skipped NodeRun 原样拷进新 run，编排器据此断点续跑，只重跑该节点及之后。
        上一 run 的节点/事件保留作历史；工作区是否重置由 worker 按新 run 是否已有 completed 节点决定。
        """
        task = await self.repo.get_by_id_with_runs(task_id, owner_id)
        if not task:
            from app.shared.exceptions import NotFoundError

            raise NotFoundError("任务不存在", code="TASK_NOT_FOUND", details={"task_id": task_id})
        if task.status not in ("failed", "completed", "cancelled", "needs_review"):
            raise ValueError(f"状态为 {task.status} 的任务不能重试")
        await self._require_platform_ready()

        source_run = task.runs[0] if task.runs else None
        original_status = task.status
        claimed = await self.repo.session.execute(
            update(Task)
            .where(
                Task.id == task_id,
                Task.owner_id == owner_id,
                Task.status.in_(("failed", "completed", "cancelled", "needs_review")),
            )
            .values(status="queued")
        )
        if claimed.rowcount == 0:
            await self.repo.session.refresh(task)
            raise ValueError(f"状态为 {task.status} 的任务不能重试")
        await self.repo.session.refresh(task)

        new_run = TaskRun(task_id=task_id, status="pending")
        new_run = await self.repo.create_run(new_run)

        try:
            if from_node is not None:
                await self._copy_prior_nodes_for_resume(
                    task, new_run.id, from_node, source_run=source_run
                )
        except Exception:
            task.status = original_status
            new_run.status = "failed"
            new_run.error_message = "重试准备失败"
            new_run.finished_at = datetime.now(timezone.utc)
            await self.repo.session.commit()
            raise

        await self.repo.session.flush()
        await self.repo.session.commit()

        # 投 Celery(用新 run.id 作 celery task_id,cancel 时可精确 revoke)
        from app.core.celery_app import celery_app

        try:
            celery_app.send_task(
                "agent.run_analysis", args=[task_id, new_run.id], task_id=new_run.id
            )
        except Exception as exc:
            task.status = "failed"
            new_run.status = "failed"
            new_run.error_message = f"任务投递失败: {exc}"[:1000]
            new_run.finished_at = datetime.now(timezone.utc)
            await self.repo.session.commit()
            raise TaskDispatchError("重试记录已创建，但投递 Agent worker 失败") from exc

        return new_run.id

    async def set_source_alert_group(self, task_id: str, alert_group_id: str) -> None:
        """发现侧→验证侧唯一溯源指针(discovery-spec §5.1)。

        自动终认：dispatch 经此方法把本审计任务指向主线索组(溯源自指)；
        人工放行：finding service 创建 verify Task 时写新任务。无物理 FK。
        """
        task = await self.repo.session.get(Task, task_id)
        if task is None:
            raise ValueError(f"任务不存在: {task_id}")
        task.source_alert_group_id = alert_group_id
        await self.repo.session.flush()

    async def _copy_prior_nodes_for_resume(
        self, task: Task, new_run_id: str, from_node: str, *, source_run: TaskRun | None
    ) -> None:
        """把 source_run 里 from_node 之前的可续跑节点拷进新 run,供编排器断点续跑。

        按节点 key 匹配并**重写为新拓扑 node_index**(旧 run 的 index 与新
        DEFAULT_PIPELINE 不一致，直接拷 index 会让编排器找不到行)。
        verify 任务里被 VERIFY_MODE skip 的节点(扫描/聚类/二审/调度)在旧 run
        可能没有行——缺行不视为失败，新 run 会重新 skip。
        """
        from .models import NodeRun

        if from_node not in _RETRYABLE_FROM_NODES:
            raise ValueError(f"不支持的重试起点: {from_node}")
        target_index = _NODE_INDEX[from_node]

        if source_run is None:
            raise ValueError("任务尚无运行记录，无法从指定节点重试")

        task_type = getattr(task, "task_type", None) or "verify"
        prior_nodes = await self.repo.get_node_runs(source_run.id)
        by_key = {nr.node_key: nr for nr in prior_nodes}

        for spec in _PIPELINE:
            if spec.index >= target_index:
                continue
            src = by_key.get(spec.key)
            if src is not None and src.status in _RESUMABLE_STATUSES:
                continue
            # 缺行/不可续跑：verify 任务里本就会被 VERIFY_MODE skip 的节点放行
            if (
                task_type == "verify"
                and _VERIFY_MODE_SKIP_KEYS
                and spec.key in _VERIFY_MODE_SKIP_KEYS
            ):
                continue
            raise ValueError(
                f"前置节点未完成，无法从 {from_node} 重试（缺 {spec.key}）"
            )

        for spec in _PIPELINE:
            if spec.index >= target_index:
                continue
            src = by_key.get(spec.key)
            if src is None or src.status not in _RESUMABLE_STATUSES:
                continue  # VERIFY_MODE 容忍的缺行
            self.repo.session.add(
                NodeRun(
                    run_id=new_run_id,
                    task_id=task.id,
                    node_index=spec.index,
                    node_key=spec.key,
                    status=src.status,
                    input_json=src.input_json,
                    output_json=src.output_json,
                    attempt=src.attempt,
                    agent_session_id=src.agent_session_id,
                    started_at=src.started_at,
                    finished_at=src.finished_at,
                    error_message=src.error_message,
                )
            )
        await self.repo.session.flush()

    async def delete_task(
        self, task_id: str, owner_id: str, *, hard: bool = False
    ) -> bool:
        """删除任务。默认软删(status=archived),hard=True 物理删除。

        running 中的任务不能删(先 cancel)。
        """
        task = await self.repo.get_by_id_with_runs(task_id, owner_id)
        if not task:
            return False
        if task.status == "running":
            raise ValueError("运行中的任务不能删除,请先取消")
        if task.status == "archived" and not hard:
            raise ValueError("任务已归档")

        if task.status in ("pending", "queued"):
            from app.core.celery_app import celery_app

            revoked = await self.repo.cancel_active_runs(task_id)
            await self.repo.cancel_incomplete_nodes([run.id for run in revoked])
            for run in revoked:
                try:
                    celery_app.control.revoke(run.id, terminate=True, signal="SIGTERM")
                except Exception:
                    pass

        try:
            from app.contexts.agent.runtime_cleanup import teardown_task_runtime

            await teardown_task_runtime(task_id)
        except Exception:  # noqa: BLE001
            logger.warning("删除任务时拆容器失败(best-effort) task=%s", task_id, exc_info=True)

        if hard:
            await self.repo.delete_hard(task)
        else:
            task.status = "archived"
            await self.repo.session.flush()
        return True

    async def get_run_nodes(
        self, task_id: str, run_id: str, owner_id: str
    ) -> list[dict] | None:
        """获取某 run 的 6 节点状态(前端步骤条数据源)。"""
        from app.contexts.task.models import NodeRun
        from sqlalchemy import select as sa_select

        if await self.repo.get_by_id_with_runs(task_id, owner_id) is None:
            return None
        result = await self.repo.session.execute(
            sa_select(NodeRun)
            .where(NodeRun.run_id == run_id, NodeRun.task_id == task_id)
            .order_by(NodeRun.node_index)
        )
        return [_serialize_node_run(nr) for nr in result.scalars().all()]

    async def record_node_run_failure(
        self,
        *,
        owner_id: str,
        task_id: str,
        run_id: str,
        node_run_id: str,
        node_key: str,
        error_class: str,
        failed_stage: str | None,
        language: str | None,
        attempt_count: int,
        bundle: bytes,
    ) -> None:
        """put node_run 包并写索引。上传失败只打日志，不抛给调用方。"""
        from app.contexts.task.models import NodeRunFailure
        from app.shared.object_store import get_object_store

        try:
            ref = get_object_store().put(
                "node_run",
                owner_id,
                bundle,
                content_type="application/gzip",
                task_id=task_id,
                run_id=run_id,
                node_key=node_key,
            )
        except Exception:
            logger.warning(
                "node_run 对象上传失败 node=%s task=%s",
                node_key,
                task_id,
                exc_info=True,
            )
            return
        self.session.add(
            NodeRunFailure(
                owner_id=owner_id,
                task_id=task_id,
                run_id=run_id,
                node_run_id=node_run_id,
                node_key=node_key,
                error_class=error_class,
                failed_stage=failed_stage,
                language=language,
                attempt_count=attempt_count,
                bundle_key=ref.key,
                bucket=ref.bucket,
            )
        )
        try:
            await self.session.flush()
        except Exception:
            logger.warning(
                "node_run_failures 索引写入失败 node=%s task=%s",
                node_key,
                task_id,
                exc_info=True,
            )
