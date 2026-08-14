import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.task.models import Task
from app.core.config import get_settings

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
            last_seen = (
                self._as_utc(lab.last_seen_at) if lab.last_seen_at is not None else None
            )
            ttl = lab.ttl_seconds if lab.ttl_seconds is not None else 3600
            if last_seen is not None and clock < last_seen + timedelta(seconds=ttl):
                continue
            if await self.live_task_ids(lab.id):
                continue
            await docker_ops.compose_down(lab.compose_project)
            lab.status = "expired"
            await self.session.commit()
            expired.append(lab.id)
        return expired

    async def fail_stale_creating(self) -> list[str]:
        """标记无 live 任务的 creating Lab 失败，并 best-effort 清理 compose。"""
        from . import docker_ops

        result = await self.session.execute(select(Lab).where(Lab.status == "creating"))
        stale = [
            lab for lab in result.scalars().all() if not await self.live_task_ids(lab.id)
        ]
        for lab in stale:
            lab.status = "failed"
        if stale:
            await self.session.commit()
        for lab in stale:
            try:
                await docker_ops.compose_down(lab.compose_project)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "清理僵死 creating Lab 失败(best-effort) lab=%s",
                    lab.id,
                    exc_info=True,
                )
        return [lab.id for lab in stale]

    async def known_lab_ids(self) -> set[str]:
        """返回现存 Lab ID，供历史 compose 孤儿识别。"""
        result = await self.session.execute(select(Lab.id))
        return {lab_id.lower() for lab_id in result.scalars().all()}

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
