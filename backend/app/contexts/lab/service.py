import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.project.repository import ProjectRepository
from app.contexts.project.service import ProjectService
from app.contexts.task.models import Task
from app.core.config import get_settings

from .errors import LabBusyError, LabNotFoundError
from .models import Lab
from .repository import LabRepository

logger = logging.getLogger(__name__)

LIVE_TASK_STATUSES = frozenset({"pending", "queued", "running"})
RECLAIMABLE_LAB_STATUSES = frozenset({"failed", "expired", "destroyed"})


@dataclass(frozen=True)
class AcquireResult:
    lab_id: str
    role: str
    status: str
    workdir: str
    compose_project: str
    target_url: str | None
    compose_path: str | None
    transport_shape: dict
    initial_creds: dict
    reused: bool


class LabService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = LabRepository(session)

    async def acquire(
        self,
        *,
        owner_id: str,
        project_id: str,
        commit_sha: str,
        task_id: str,
    ) -> AcquireResult:
        lab = await self.repository.get_by_key(owner_id, project_id, commit_sha)
        if lab is None:
            lab_id = str(uuid.uuid4())
            workdir_base = (
                get_settings().agent_runner_workdir_base.rstrip("/\\").replace("\\", "/")
            )
            lab = Lab(
                id=lab_id,
                owner_id=owner_id,
                project_id=project_id,
                commit_sha=commit_sha,
                status="creating",
                compose_project=f"crucible-lab-{lab_id.lower()}",
                workdir=f"{workdir_base}/labs/{lab_id}",
                creator_task_id=task_id,
                last_seen_at=self._now(),
            )
            try:
                await self.repository.add(lab)
            except IntegrityError:
                await self.session.rollback()
                lab = await self.repository.get_by_key(
                    owner_id, project_id, commit_sha
                )
                if lab is None:
                    raise
            else:
                await self.bind_task(task_id, lab.id, commit=False)
                await self.session.commit()
                return self._result(lab, "create")

        role = await self._role_for_existing(lab, task_id)
        await self.bind_task(task_id, lab.id, commit=False)
        await self.session.commit()
        return self._result(lab, role)

    async def _role_for_existing(self, lab: Lab, task_id: str) -> str:
        if lab.status in RECLAIMABLE_LAB_STATUSES:
            reclaimed = await self.repository.cas_reclaim(
                lab.id, RECLAIMABLE_LAB_STATUSES, task_id
            )
            if reclaimed:
                await self.session.refresh(lab)
                lab.last_seen_at = self._now()
                lab.error_message = None
                return "create"
            await self.session.refresh(lab)

        if lab.status == "creating":
            if lab.creator_task_id == task_id:
                lab.last_seen_at = self._now()
                return "create"
            return "wait"
        if lab.status == "ready":
            lab.last_seen_at = self._now()
            return "reuse"
        if lab.status == "stopped":
            lab.last_seen_at = self._now()
            return "start"
        raise ValueError(f"不支持 acquire Lab 状态: {lab.status}")

    async def touch(self, lab_id: str) -> None:
        lab = await self._require_lab(lab_id)
        lab.last_seen_at = self._now()
        await self.session.commit()

    async def mark_ready(
        self,
        lab_id: str,
        *,
        target_url: str,
        compose_path: str,
        transport_shape: dict,
        initial_creds: dict,
    ) -> None:
        lab = await self._require_lab(lab_id)
        lab.status = "ready"
        lab.target_url = target_url
        lab.compose_path = compose_path
        lab.transport_shape = json.dumps(transport_shape)
        lab.initial_creds = json.dumps(initial_creds)
        lab.last_seen_at = self._now()
        lab.error_message = None
        await self.session.commit()

    async def mark_failed(self, lab_id: str, error: str) -> None:
        lab = await self._require_lab(lab_id)
        lab.status = "failed"
        lab.error_message = error
        await self.session.commit()

    async def mark_creator_cancelled(self, task_id: str) -> None:
        result = await self.session.execute(
            select(Lab).where(
                Lab.creator_task_id == task_id,
                Lab.status == "creating",
            )
        )
        lab = result.scalar_one_or_none()
        if lab is not None:
            lab.status = "failed"
            await self.session.commit()

    async def bind_task(
        self, task_id: str, lab_id: str, *, commit: bool = True
    ) -> None:
        task = await self.session.get(Task, task_id)
        if task is None:
            raise LookupError(f"Task 不存在: {task_id}")
        task.lab_id = lab_id
        if commit:
            await self.session.commit()

    async def live_task_ids(self, lab_id: str) -> list[str]:
        result = await self.session.execute(
            select(Task.id)
            .where(
                Task.lab_id == lab_id,
                Task.status.in_(LIVE_TASK_STATUSES),
            )
            .order_by(Task.id)
        )
        return list(result.scalars().all())

    async def list_grouped(self, owner_id: str) -> list[dict]:
        from . import docker_ops

        labs = await self.repository.list_by_owner(owner_id)
        grouped: dict[str, dict] = {}
        project_service = ProjectService(ProjectRepository(self.session))
        now = self._now()
        for lab in labs:
            group = grouped.get(lab.project_id)
            if group is None:
                project = await project_service.get_project(lab.project_id, owner_id)
                group = {
                    "project_id": lab.project_id,
                    "project_name": project.name if project else lab.project_id,
                    "labs": [],
                }
                grouped[lab.project_id] = group
            live_ids = await self.live_task_ids(lab.id)
            group["labs"].append(
                await self._management_dict(
                    lab,
                    now=now,
                    live_task_count=len(live_ids),
                    containers=await docker_ops.list_containers(lab.compose_project),
                )
            )
        return list(grouped.values())

    async def get_detail(self, lab_id: str, *, owner_id: str) -> dict:
        from . import docker_ops

        lab = await self._require_owned_lab(lab_id, owner_id)
        await self.touch(lab.id)
        live_ids = await self.live_task_ids(lab.id)
        return await self._management_dict(
            lab,
            now=self._now(),
            live_task_count=len(live_ids),
            containers=await docker_ops.list_containers(lab.compose_project),
        )

    async def stop_lab(self, lab_id: str, *, owner_id: str) -> str:
        from . import docker_ops

        lab = await self._require_writable_lab(lab_id, owner_id)
        self._require_status(lab, {"ready"}, "stop")
        await self._confirm_not_busy(lab.id)
        await docker_ops.compose_stop(lab.compose_project)
        lab.status = "stopped"
        await self._touch_and_commit(lab)
        return lab.status

    async def start_lab(self, lab_id: str, *, owner_id: str) -> str:
        from . import docker_ops

        lab = await self._require_writable_lab(lab_id, owner_id)
        self._require_status(lab, {"stopped"}, "start")
        await self._confirm_not_busy(lab.id)
        if not await docker_ops.compose_start(lab.compose_project):
            raise RuntimeError("靶场 compose start 失败")
        lab.status = "ready"
        await self._touch_and_commit(lab)
        return lab.status

    async def rebuild_lab(self, lab_id: str, *, owner_id: str) -> str:
        from . import docker_ops

        lab = await self._require_writable_lab(lab_id, owner_id)
        self._require_status(
            lab,
            {"ready", "stopped", "failed", "expired", "destroyed"},
            "rebuild",
        )
        if not lab.compose_path:
            raise ValueError("缺少配方，请从验证任务重新创建")
        compose_file = (
            f"{lab.workdir.rstrip('/')}/{lab.compose_path.lstrip('/')}"
            .replace("\\", "/")
        )
        if not os.path.isfile(compose_file):
            raise ValueError("缺少配方，请从验证任务重新创建")

        await self._confirm_not_busy(lab.id)
        lab.status = "creating"
        await self.session.commit()
        try:
            await docker_ops.compose_up_build(
                lab.compose_project, compose_file, lab.workdir
            )
        except Exception as exc:
            lab.status = "failed"
            lab.error_message = str(exc)
            await self.session.commit()
            raise
        lab.status = "ready"
        lab.error_message = None
        await self._touch_and_commit(lab)
        return lab.status

    async def destroy_lab(self, lab_id: str, *, owner_id: str) -> str:
        from . import docker_ops

        lab = await self._require_writable_lab(lab_id, owner_id)
        self._require_status(
            lab, {"ready", "stopped", "failed", "expired"}, "destroy"
        )
        await self._confirm_not_busy(lab.id)
        await docker_ops.compose_down(lab.compose_project)
        lab.status = "destroyed"
        await self._touch_and_commit(lab)
        return lab.status

    async def container_action(
        self,
        lab_id: str,
        name: str,
        *,
        action: str,
        owner_id: str,
    ) -> str:
        from . import docker_ops

        lab = await self._require_writable_lab(lab_id, owner_id)
        self._require_status(lab, {"ready", "stopped"}, f"container {action}")
        operations = {
            "stop": docker_ops.container_stop,
            "start": docker_ops.container_start,
            "restart": docker_ops.container_restart,
            "rm": docker_ops.container_rm,
        }
        operation = operations.get(action)
        if operation is None:
            raise ValueError(f"不支持的容器操作: {action}")
        await self._confirm_not_busy(lab.id)
        await operation(name, lab.compose_project)
        await self._touch_and_commit(lab)
        return lab.status

    async def expire_silent_labs(
        self, *, now: datetime | None = None
    ) -> list[str]:
        """销毁超过 TTL 且没有 live 任务占用的 ready/stopped Lab。"""
        from . import docker_ops

        clock = self._as_utc(now or self._now())
        result = await self.session.execute(
            select(Lab).where(Lab.status.in_({"ready", "stopped"}))
        )
        expired: list[str] = []
        for lab in result.scalars().all():
            original_status = lab.status
            last_seen = (
                self._as_utc(lab.last_seen_at) if lab.last_seen_at is not None else None
            )
            ttl = lab.ttl_seconds if lab.ttl_seconds is not None else 3600
            if last_seen is not None and clock < last_seen + timedelta(seconds=ttl):
                continue
            if await self.live_task_ids(lab.id):
                continue
            claimed = await self.repository.cas_status(
                lab.id, {original_status}, "expired"
            )
            if not claimed:
                continue
            await self.session.commit()
            if await self.live_task_ids(lab.id):
                await self.repository.cas_status(lab.id, {"expired"}, original_status)
                await self.session.commit()
                continue
            await self.session.refresh(lab)
            if lab.status != "expired":
                continue
            try:
                await docker_ops.compose_down(lab.compose_project)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "TTL 清理 Lab 失败(best-effort) lab=%s",
                    lab.id,
                    exc_info=True,
                )
            expired.append(lab.id)
        return expired

    async def fail_stale_creating(self) -> list[str]:
        """标记无 live 任务的 creating Lab 失败，并 best-effort 清理 compose。"""
        from . import docker_ops

        result = await self.session.execute(select(Lab).where(Lab.status == "creating"))
        failed: list[str] = []
        for lab in result.scalars().all():
            if await self.live_task_ids(lab.id):
                continue
            claimed = await self.repository.cas_status(lab.id, {"creating"}, "failed")
            if not claimed:
                continue
            await self.session.commit()
            if await self.live_task_ids(lab.id):
                await self.repository.cas_status(lab.id, {"failed"}, "creating")
                await self.session.commit()
                continue
            await self.session.refresh(lab)
            if lab.status != "failed":
                continue
            try:
                await docker_ops.compose_down(lab.compose_project)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "清理僵死 creating Lab 失败(best-effort) lab=%s",
                    lab.id,
                    exc_info=True,
                )
            failed.append(lab.id)
        return failed

    async def known_lab_ids(self) -> set[str]:
        """返回现存 Lab ID，供历史 compose 孤儿识别。"""
        result = await self.session.execute(select(Lab.id))
        return {lab_id.lower() for lab_id in result.scalars().all()}

    async def _require_owned_lab(self, lab_id: str, owner_id: str) -> Lab:
        lab = await self.repository.get(lab_id)
        if lab is None or lab.owner_id != owner_id:
            raise LabNotFoundError(f"Lab 不存在: {lab_id}")
        return lab

    async def _require_writable_lab(self, lab_id: str, owner_id: str) -> Lab:
        lab = await self._require_owned_lab(lab_id, owner_id)
        await self._confirm_not_busy(lab.id)
        return lab

    async def _confirm_not_busy(self, lab_id: str) -> None:
        task_ids = await self.live_task_ids(lab_id)
        if task_ids:
            raise LabBusyError(task_ids)

    @staticmethod
    def _require_status(lab: Lab, allowed: set[str], action: str) -> None:
        if lab.status not in allowed:
            raise ValueError(f"Lab 当前状态 {lab.status} 不允许执行 {action}")

    async def _touch_and_commit(self, lab: Lab) -> None:
        lab.last_seen_at = self._now()
        await self.session.commit()

    async def _management_dict(
        self,
        lab: Lab,
        *,
        now: datetime,
        live_task_count: int,
        containers: list[dict[str, str]],
    ) -> dict:
        if lab.last_seen_at is None:
            ttl_remaining = 0
        else:
            elapsed = (
                self._as_utc(now) - self._as_utc(lab.last_seen_at)
            ).total_seconds()
            ttl_remaining = max(0, int(lab.ttl_seconds - elapsed))
        return {
            "id": lab.id,
            "project_id": lab.project_id,
            "commit_sha": lab.commit_sha,
            "status": lab.status,
            "target_url": lab.target_url,
            "ttl_remaining_seconds": ttl_remaining,
            "containers": containers,
            "live_task_count": live_task_count,
            "error_message": lab.error_message,
        }

    async def _require_lab(self, lab_id: str) -> Lab:
        lab = await self.repository.get(lab_id)
        if lab is None:
            raise LookupError(f"Lab 不存在: {lab_id}")
        return lab

    @staticmethod
    def _result(lab: Lab, role: str) -> AcquireResult:
        return AcquireResult(
            lab_id=lab.id,
            role=role,
            status=lab.status,
            workdir=lab.workdir,
            compose_project=lab.compose_project,
            target_url=lab.target_url,
            compose_path=lab.compose_path,
            transport_shape=LabService._load_dict(lab.transport_shape),
            initial_creds=LabService._load_dict(lab.initial_creds),
            reused=role in {"reuse", "start"},
        )

    @staticmethod
    def _load_dict(value: str) -> dict:
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
