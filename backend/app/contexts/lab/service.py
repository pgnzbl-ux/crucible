import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.shared.time import as_utc

from .errors import LabBusyError, LabNotFoundError
from .models import Lab
from .recipe_store import default_recipe_store, extract_recipe, pack_recipe, recipe_object_key
from .repository import LabRepository

logger = logging.getLogger(__name__)

RECLAIMABLE_LAB_STATUSES = frozenset({"failed", "expired", "destroyed"})
_ALIGN_FROZEN_STATUSES = frozenset({"creating", "rebuilding", "destroyed", "failed"})
_MANUAL_REBUILD_STALE_SECONDS = 1800
CREATING_LEASE_SECONDS = 1800
TTL_ACTIVE_STATUSES = frozenset({"ready", "stopped"})


def ttl_remaining_seconds(
    status: str,
    last_seen_at: datetime | None,
    ttl_seconds: int,
    now: datetime,
) -> int | None:
    """仅 ready/stopped 倒计时；创建中等非就绪状态返回 None。"""
    if status not in TTL_ACTIVE_STATUSES:
        return None
    if last_seen_at is None:
        return 0
    elapsed = (as_utc(now) - as_utc(last_seen_at)).total_seconds()
    return max(0, int(ttl_seconds - elapsed))


def container_runtime_kind(containers: list[dict[str, str]]) -> str:
    """把 Docker 状态收成 none / running / partial / exited。

    只有所有长期服务都正常运行（或一次性任务成功退出）才是 running；
    Restarting、unhealthy、health: starting、非零退出都属于 partial。
    """
    if not containers:
        return "none"
    running = 0
    for item in containers:
        status = item.get("status", "")
        state = item.get("state", "")
        if _container_is_running(status, state=state):
            running += 1
            continue
        text = (status or "").strip().lower()
        if text.startswith("exited (0)"):
            continue
        if (state or "").strip().lower() == "exited" and "(0)" in text:
            continue
        return "partial"
    return "running" if running > 0 else "exited"


def _container_is_running(status: str, *, state: str = "") -> bool:
    text = (status or "").strip().lower()
    state_text = (state or "").strip().lower()
    if (
        text.startswith("restarting")
        or "(unhealthy)" in text
        or "(health: starting)" in text
    ):
        return False
    if state_text and state_text != "running":
        return False
    return state_text == "running" or text.startswith("up") or text == "running"


def next_aligned_lab_status(
    db_status: str, runtime: str, *, live_task_count: int
) -> str | None:
    """按容器实际状态校正 labs.status；无需改动则返回 None。

    creating / rebuilding / failed / destroyed 不校正（进行中或用户终态）。
    容器状态只能否定 ready，不能证明应用 ready；提升必须经过 HTTP 探活。
    无 live 任务时才对完全消失的运行时标 expired。
    """
    if db_status in _ALIGN_FROZEN_STATUSES:
        return None
    if runtime in {"exited", "partial"} and db_status == "ready":
        return "stopped"
    if live_task_count > 0:
        return None
    if runtime == "none" and db_status in {"ready", "stopped"}:
        return "expired"
    if runtime == "exited" and db_status == "expired":
        return "stopped"
    return None


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


_DEFAULT_COMPOSE_PATH = ".vuln-env/docker-compose.yml"


class LabService:
    def __init__(self, session: AsyncSession, *, recipe_store=None) -> None:
        self.session = session
        self.repository = LabRepository(session)
        self.recipe_store = recipe_store or default_recipe_store()

    def _task_service(self):
        from app.contexts.task.repository import TaskRepository
        from app.contexts.task.service import TaskService

        return TaskService(TaskRepository(self.session))

    async def download_recipe(
        self,
        *,
        owner_id: str,
        project_id: str,
        commit_sha: str,
        dest_workdir: str,
    ) -> dict | None:
        object_key = recipe_object_key(owner_id, project_id, commit_sha)
        fd, archive_path = tempfile.mkstemp(suffix=".tar.gz")
        os.close(fd)
        extract_dir = tempfile.mkdtemp(prefix="lab-recipe-")
        try:
            try:
                self.recipe_store.download(object_key, archive_path)
                meta = extract_recipe(archive_path, extract_dir)
            except FileNotFoundError:
                logger.warning("配方未命中 key=%s", object_key)
                return None
            except Exception:
                logger.warning("配方下载或解压失败 key=%s", object_key, exc_info=True)
                return None

            hit = self._recipe_meta_with_defaults(meta)
            compose_rel = hit["compose_path"].lstrip("/").replace("\\", "/")
            compose_file = Path(extract_dir) / compose_rel
            if not compose_file.is_file():
                logger.warning(
                    "配方缺少 compose 文件 dest=%s path=%s",
                    dest_workdir,
                    hit["compose_path"],
                )
                return None
            self._commit_extracted_recipe(extract_dir, dest_workdir)
            return hit
        finally:
            Path(archive_path).unlink(missing_ok=True)
            shutil.rmtree(extract_dir, ignore_errors=True)

    async def upload_recipe(
        self,
        *,
        owner_id: str,
        project_id: str,
        commit_sha: str,
        lab_workdir: str,
        compose_path: str,
        transport_shape: dict,
        initial_creds: dict,
        started_containers: list | None = None,
    ) -> bool:
        """打包 .vuln-env 上传 MinIO。失败吞错返回 False（靶场已就绪不该连坐），
        调用方据此发事件告知用户「本次未缓存，rebuild 需重跑 AI」。"""
        vuln_env = Path(lab_workdir) / ".vuln-env"
        if not vuln_env.is_dir():
            logger.warning("上传配方跳过：缺少 .vuln-env 目录 workdir=%s", lab_workdir)
            return False
        object_key = recipe_object_key(owner_id, project_id, commit_sha)
        meta = {
            "compose_path": compose_path,
            "transport_shape": transport_shape,
            "initial_creds": initial_creds,
            "started_containers": list(started_containers or []),
        }
        fd, archive_path = tempfile.mkstemp(suffix=".tar.gz")
        os.close(fd)
        try:
            pack_recipe(lab_workdir, archive_path, meta)
            self.recipe_store.upload(object_key, archive_path)
        except Exception:
            logger.error("上传配方失败 key=%s", object_key, exc_info=True)
            return False
        finally:
            Path(archive_path).unlink(missing_ok=True)
        return True

    @staticmethod
    def _commit_extracted_recipe(extract_dir: str, dest_workdir: str) -> None:
        dest = Path(dest_workdir)
        dest.mkdir(parents=True, exist_ok=True)
        src_env = Path(extract_dir) / ".vuln-env"
        dst_env = dest / ".vuln-env"
        if src_env.is_dir():
            if dst_env.exists():
                shutil.rmtree(dst_env)
            shutil.move(str(src_env), str(dst_env))
        src_meta = Path(extract_dir) / "recipe-meta.json"
        if src_meta.is_file():
            shutil.move(str(src_meta), str(dest / "recipe-meta.json"))

    @staticmethod
    def _recipe_meta_with_defaults(meta: dict) -> dict:
        compose_path = meta.get("compose_path")
        if not isinstance(compose_path, str) or not compose_path.strip():
            compose_path = _DEFAULT_COMPOSE_PATH
        transport_shape = meta.get("transport_shape")
        if not isinstance(transport_shape, dict):
            transport_shape = {}
        initial_creds = meta.get("initial_creds")
        if not isinstance(initial_creds, dict):
            initial_creds = {}
        started_containers = meta.get("started_containers")
        if not isinstance(started_containers, list):
            started_containers = []
        return {
            "compose_path": compose_path,
            "transport_shape": transport_shape,
            "initial_creds": initial_creds,
            "started_containers": started_containers,
        }

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
            workdir_base = Path(
                get_settings().agent_runner_workdir_base.rstrip("/\\")
            )
            from app.core.agent_runner import normalize_host_workdir

            lab_root = Path(normalize_host_workdir(str(workdir_base))) / "labs"
            lab = Lab(
                id=lab_id,
                owner_id=owner_id,
                project_id=project_id,
                commit_sha=commit_sha,
                status="creating",
                compose_project=f"crucible-lab-{lab_id.lower()}",
                workdir=str(lab_root / lab_id),
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
            creator_id = lab.creator_task_id
            live_ids = set(await self.live_task_ids(lab.id))
            stale_before = self._now() - timedelta(seconds=CREATING_LEASE_SECONDS)
            stale = (
                lab.last_seen_at is None
                or self._as_utc(lab.last_seen_at) <= stale_before
            )
            creator_live = bool(creator_id and creator_id in live_ids)
            if not creator_live or stale:
                taken = await self.repository.cas_takeover_creating(
                    lab.id,
                    previous_creator_task_id=creator_id,
                    creator_task_id=task_id,
                    stale_before=stale_before if creator_live else None,
                )
                if taken:
                    await self.session.refresh(lab)
                    return "create"
                await self.session.refresh(lab)
            return "wait"
        if lab.status in {"ready", "stopped"}:
            if not await self._compose_project_present(lab.compose_project):
                await self.reclaim_gone_runtime(lab.id, task_id)
                await self.session.refresh(lab)
                return "create"
            lab.last_seen_at = self._now()
            return "reuse" if lab.status == "ready" else "start"
        raise ValueError(f"不支持 acquire Lab 状态: {lab.status}")

    async def reclaim_gone_runtime(self, lab_id: str, task_id: str) -> None:
        """ready/stopped 但 Docker 已无该项目时，标 expired 再 CAS 收回为 creating。"""
        lab = await self._require_lab(lab_id)
        claimed = await self.repository.cas_status(
            lab.id, {"ready", "stopped"}, "expired"
        )
        if claimed:
            await self.session.commit()
        reclaimed = await self.repository.cas_reclaim(
            lab.id, RECLAIMABLE_LAB_STATUSES, task_id
        )
        if not reclaimed:
            await self.session.refresh(lab)
            if lab.status == "creating" and lab.creator_task_id == task_id:
                lab.last_seen_at = self._now()
                await self.session.commit()
                return
            raise RuntimeError("靶场容器已不存在，且无法重新创建")
        await self.session.refresh(lab)
        lab.last_seen_at = self._now()
        lab.error_message = None
        await self.session.commit()

    async def touch(self, lab_id: str) -> None:
        lab = await self._require_lab(lab_id)
        lab.last_seen_at = self._now()
        await self.session.commit()

    async def heartbeat_creation(self, lab_id: str, task_id: str) -> bool:
        """仅当前 creating owner 可续租，防止旧创建者覆盖接管者。"""
        result = await self.session.execute(
            update(Lab)
            .where(
                Lab.id == lab_id,
                Lab.status == "creating",
                Lab.creator_task_id == task_id,
            )
            .values(last_seen_at=self._now())
        )
        await self.session.commit()
        return result.rowcount == 1

    async def align_runtime_status(self, lab_id: str) -> str:
        """按 compose 实际容器回写 labs.status。复现拉起后管理页不再卡 expired。"""
        from . import docker_ops

        lab = await self._require_lab(lab_id)
        live_count = len(await self.live_task_ids(lab.id))
        try:
            containers = await docker_ops.list_containers(lab.compose_project)
        except Exception:  # noqa: BLE001
            logger.warning("对齐 Lab 运行时状态失败 lab=%s", lab_id, exc_info=True)
            return lab.status
        if self._apply_aligned_status(lab, containers, live_task_count=live_count):
            await self.session.commit()
        return lab.status

    def _apply_aligned_status(
        self,
        lab: Lab,
        containers: list[dict[str, str]],
        *,
        live_task_count: int,
    ) -> bool:
        nxt = next_aligned_lab_status(
            lab.status,
            container_runtime_kind(containers),
            live_task_count=live_task_count,
        )
        if nxt is None or nxt == lab.status:
            return False
        lab.status = nxt
        if nxt == "ready":
            lab.error_message = None
            lab.last_seen_at = self._now()
        return True

    async def _probe_lab_ready(
        self,
        lab: Lab,
        *,
        retries: int = 30,
        settle_seconds: float = 1,
    ) -> tuple[bool, str]:
        """用与 env_ready 相同的 Compose + HTTP gate 判定应用就绪。"""
        from app.contexts.agent.nodes.env_ready import health

        raw = str(lab.target_url or "")
        if not raw:
            return False, "缺少 target_url"
        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname or "127.0.0.1"
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        health_result = await health.health_check(
            [port],
            host_ips=[host],
            preferred_scheme=parsed.scheme or None,
            probe_path=path,
            compose_project=lab.compose_project,
            retries=retries,
            settle_seconds=settle_seconds,
        )
        ok, _, scheme = health_result
        if ok and scheme in {"http", "https"}:
            lab.target_url = parsed._replace(scheme=scheme).geturl()
            shape = self._load_dict(lab.transport_shape)
            shape["protocol"] = scheme
            lab.transport_shape = json.dumps(shape)
        return ok, "" if ok else health.failure_reason(health_result)

    async def _refresh_target_url_from_runtime(self, lab: Lab) -> tuple[bool, str]:
        """重建后按 Docker 实际绑定刷新 target_url（兼容动态宿主端口）。"""
        from app.contexts.agent import target_url as target_url_mod
        from app.contexts.agent.nodes.env_ready import ports

        try:
            bindings = await ports.load_runtime_web_bindings(lab.compose_project)
        except Exception as exc:  # noqa: BLE001
            return False, f"读取 Docker 实际发布端口失败: {exc}"
        usable = ports.publishable_runtime_bindings(
            bindings,
            target_url_mod.host_advertise_ip(),
        )
        if not usable:
            return False, "无可供复现容器访问的 TCP Web 绑定"
        raw = str(lab.target_url or "")
        parsed = urlparse(raw if "://" in raw else f"http://{raw}") if raw else None
        previous_port = parsed.port if parsed else None
        binding = next(
            (
                item
                for item in usable
                if previous_port is not None
                and int(item["host_port"]) == previous_port
            ),
            usable[0],
        )
        scheme = parsed.scheme if parsed and parsed.scheme in {"http", "https"} else "http"
        refreshed = target_url_mod.publish_target_url(
            int(binding["host_port"]),
            str(binding["public_host"]),
            scheme=scheme,
        )
        if parsed:
            suffix = parsed.path or ""
            if parsed.query:
                suffix += f"?{parsed.query}"
            if suffix and suffix != "/":
                refreshed = f"{refreshed.rstrip('/')}{suffix}"
        lab.target_url = refreshed
        return True, ""

    async def mark_ready(
        self,
        lab_id: str,
        *,
        target_url: str,
        compose_path: str,
        transport_shape: dict,
        initial_creds: dict,
        expected_statuses: set[str] | None = None,
        expected_creator_task_id: str | None = None,
    ) -> bool:
        values = {
            "target_url": target_url,
            "compose_path": compose_path,
            "transport_shape": json.dumps(transport_shape),
            "initial_creds": json.dumps(initial_creds),
            "last_seen_at": self._now(),
            "error_message": None,
        }
        if expected_statuses is not None or expected_creator_task_id is not None:
            transitioned = await self.repository.cas_transition(
                lab_id,
                from_statuses=expected_statuses or {"creating"},
                to_status="ready",
                creator_task_id=expected_creator_task_id,
                values=values,
            )
            await self.session.commit()
            return transitioned
        lab = await self._require_lab(lab_id)
        lab.status = "ready"
        for field, value in values.items():
            setattr(lab, field, value)
        await self.session.commit()
        return True

    async def mark_failed(
        self,
        lab_id: str,
        error: str,
        *,
        expected_statuses: set[str] | None = None,
        expected_creator_task_id: str | None = None,
    ) -> bool:
        if expected_statuses is not None or expected_creator_task_id is not None:
            transitioned = await self.repository.cas_transition(
                lab_id,
                from_statuses=expected_statuses or {"creating"},
                to_status="failed",
                creator_task_id=expected_creator_task_id,
                values={"error_message": error},
            )
            await self.session.commit()
            return transitioned
        lab = await self._require_lab(lab_id)
        lab.status = "failed"
        lab.error_message = error
        await self.session.commit()
        return True

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
        await self._task_service().bind_lab(task_id, lab_id, commit=commit)

    async def live_task_ids(self, lab_id: str) -> list[str]:
        return await self._task_service().list_live_ids(lab_id)

    async def list_grouped(self, owner_id: str) -> list[dict]:
        from app.contexts.project.repository import ProjectRepository
        from app.contexts.project.service import ProjectService

        from . import docker_ops

        labs = await self.repository.list_by_owner(owner_id)
        if not labs:
            return []
        live_map = await self._task_service().list_live_ids_by_lab_ids(
            [lab.id for lab in labs]
        )
        project_names = await ProjectService(
            ProjectRepository(self.session)
        ).names_by_ids(list({lab.project_id for lab in labs}), owner_id)
        containers_by_lab = await asyncio.gather(
            *[docker_ops.list_containers(lab.compose_project) for lab in labs]
        )
        grouped: dict[str, dict] = {}
        now = self._now()
        aligned = False
        for lab, containers in zip(labs, containers_by_lab, strict=True):
            live_count = len(live_map.get(lab.id, []))
            if self._apply_aligned_status(lab, containers, live_task_count=live_count):
                aligned = True
            group = grouped.get(lab.project_id)
            if group is None:
                group = {
                    "project_id": lab.project_id,
                    "project_name": project_names.get(lab.project_id, lab.project_id),
                    "labs": [],
                }
                grouped[lab.project_id] = group
            group["labs"].append(
                await self._management_dict(
                    lab,
                    now=now,
                    live_task_count=live_count,
                    containers=containers,
                )
            )
        if aligned:
            await self.session.commit()
        return list(grouped.values())

    async def get_detail(self, lab_id: str, *, owner_id: str) -> dict:
        from . import docker_ops

        lab = await self._require_owned_lab(lab_id, owner_id)
        live_ids = await self.live_task_ids(lab.id)
        containers = await docker_ops.list_containers(lab.compose_project)
        aligned = self._apply_aligned_status(
            lab, containers, live_task_count=len(live_ids)
        )
        if lab.status in TTL_ACTIVE_STATUSES:
            lab.last_seen_at = self._now()
            await self.session.commit()
        elif aligned:
            await self.session.commit()
        return await self._management_dict(
            lab,
            now=self._now(),
            live_task_count=len(live_ids),
            containers=containers,
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
        self._require_status(lab, {"stopped", "expired"}, "start")
        await self._confirm_not_busy(lab.id)
        containers = await docker_ops.list_containers(lab.compose_project)
        if not containers:
            if lab.status == "stopped":
                claimed = await self.repository.cas_status(lab.id, {"stopped"}, "expired")
                if claimed:
                    lab.status = "expired"
                    await self._touch_and_commit(lab)
            raise ValueError("靶场容器已不存在，请重建")
        if container_runtime_kind(containers) != "running":
            if not await docker_ops.compose_start(lab.compose_project):
                raise RuntimeError("靶场 compose start 失败")
        ready, detail = await self._probe_lab_ready(lab)
        if not ready:
            try:
                await docker_ops.compose_stop(lab.compose_project)
            except Exception:  # noqa: BLE001
                logger.warning("启动探活失败后停止 Lab 失败 lab=%s", lab.id, exc_info=True)
            lab.status = "stopped"
            lab.error_message = f"启动后探活失败: {detail}"[:500]
            await self._touch_and_commit(lab)
            raise RuntimeError(lab.error_message)
        lab.status = "ready"
        lab.error_message = None
        await self._touch_and_commit(lab)
        return lab.status

    async def rebuild_lab(self, lab_id: str, *, owner_id: str) -> str:
        """重建 = 源码 + 配方 + 镜像全重来（与创建路径同构）。

        就地执行契约（2026-08-18）：compose 在 {workdir}/{repo}/.vuln-env。
        先按新旧两种布局找现成 compose（有则免 clone）；找不到才
        clone 源码 + MinIO 拉配方，再 compose up --build。
        """
        from . import docker_ops

        lab = await self._require_writable_lab(lab_id, owner_id)
        self._require_status(
            lab,
            {"ready", "stopped", "failed", "expired", "destroyed", "creating"},
            "rebuild",
        )
        if not lab.compose_path:
            raise ValueError("缺少配方，请从验证任务重新创建")
        await self._confirm_not_busy(lab.id)

        workdir_root = str(self._resolve_lab_workdir(lab.workdir))

        compose_file = self._locate_rebuild_compose(lab, workdir_root)
        if compose_file is None:
            compose_file, error = await self._rebuild_fetch_missing(lab, workdir_root)
            if error:
                lab.status = "failed"
                lab.error_message = error
                await self.session.commit()
                raise ValueError(error)

        previous_status = lab.status
        lab.status = "rebuilding"
        lab.error_message = None
        lab.last_seen_at = self._now()
        await self.session.commit()
        try:
            await self._confirm_not_busy(lab.id)
        except LabBusyError:
            lab.status = previous_status
            await self.session.commit()
            raise
        try:
            await docker_ops.compose_up_build(
                lab.compose_project, compose_file, workdir_root
            )
        except Exception as exc:
            lab.status = "failed"
            lab.error_message = str(exc)
            await self.session.commit()
            raise
        mapped, mapping_error = await self._refresh_target_url_from_runtime(lab)
        if not mapped:
            lab.status = "failed"
            lab.error_message = f"重建后端口解析失败: {mapping_error}"[:500]
            await self.session.commit()
            raise RuntimeError(lab.error_message)
        ready, detail = await self._probe_lab_ready(lab, retries=5, settle_seconds=0)
        if not ready:
            lab.status = "failed"
            lab.error_message = f"重建后探活失败: {detail}"[:500]
            await self.session.commit()
            raise RuntimeError(lab.error_message)
        lab.status = "ready"
        lab.error_message = None
        await self._touch_and_commit(lab)
        return lab.status

    @staticmethod
    def _resolve_lab_workdir(workdir: str) -> Path:
        from app.core.agent_runner import normalize_host_workdir

        root = Path(normalize_host_workdir(workdir))
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _is_safe_repo_dirname(name: str) -> bool:
        if not name or name in {".", ".."}:
            return False
        if name.endswith(":"):
            return False
        if any(ch in name for ch in '<>:"|?*/\\'):
            return False
        return True

    @staticmethod
    def _rebuild_repo_dirname(lab: Lab, git_url: str) -> str:
        from app.core.agent_runner import _dirname_from_url

        rel = (lab.compose_path or "").replace("\\", "/").strip("/")
        first_seg = rel.split("/", 1)[0] if rel else ""
        if (
            rel
            and "/" in rel
            and not first_seg.startswith(".")
            and LabService._is_safe_repo_dirname(first_seg)
        ):
            return first_seg
        return _dirname_from_url(git_url)

    @staticmethod
    def _locate_rebuild_compose(lab: Lab, workdir_root: str) -> str | None:
        """按新（{repo}/.vuln-env）→ 旧（workdir 直下）顺序找现成 compose 文件。"""
        rel = (lab.compose_path or "").replace("\\", "/").lstrip("/")
        candidates = [rel]
        if "/" in rel:
            candidates.append(rel.split("/", 1)[1])
        root = Path(workdir_root)
        for cand in candidates:
            path = root / cand
            if path.is_file():
                return str(path)
        return None

    async def _rebuild_fetch_missing(
        self, lab: Lab, workdir_root: str
    ) -> tuple[str, str | None]:
        """compose 文件缺失：clone 源码 + MinIO 拉配方，返回 (compose_file, error)。"""
        repo_dirname, clone_error = await self._ensure_rebuild_source(lab, workdir_root)
        if clone_error:
            return "", clone_error

        compose_rel = (lab.compose_path or "").replace("\\", "/").lstrip("/")
        repo_prefix = f"{repo_dirname}/" if repo_dirname else ""
        if repo_prefix and compose_rel.startswith(repo_prefix):
            compose_rel = compose_rel[len(repo_prefix):]
        compose_file = str(
            Path(workdir_root) / repo_prefix / compose_rel.lstrip("/")
        )
        if os.path.isfile(compose_file):
            return compose_file, None

        recipe_hit = await self.download_recipe(
            owner_id=lab.owner_id,
            project_id=lab.project_id,
            commit_sha=lab.commit_sha,
            dest_workdir=str(Path(workdir_root) / repo_dirname)
            if repo_dirname
            else workdir_root,
        )
        if recipe_hit is None or not os.path.isfile(compose_file):
            return "", "缺少配方，请从验证任务重新创建"
        return compose_file, None

    async def _ensure_rebuild_source(
        self, lab: Lab, workdir_root: str
    ) -> tuple[str | None, str | None]:
        """lab.workdir/{repo} 没有源码时：Git shallow clone；上传项目从 MinIO 解开。"""
        import asyncio

        from sqlalchemy import select as sa_select

        from app.contexts.project.models import Project
        from app.core.agent_runner import git_clone_to_workdir

        result = await self.session.execute(
            sa_select(Project).where(Project.id == lab.project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            return None, "项目记录不存在，无法重建源码，请从验证任务重新创建"

        repo_dirname = self._rebuild_repo_dirname(lab, project.git_url)
        repo_dir = Path(workdir_root) / repo_dirname
        if repo_dir.is_dir() and any(
            p for p in repo_dir.iterdir() if p.name != ".vuln-env"
        ):
            return repo_dirname, None

        if getattr(project, "source_type", "git") == "local_upload":
            from app.contexts.project.repository import ProjectRepository
            from app.contexts.project.service import ProjectService
            from app.contexts.project.source_acquire import acquire_uploaded_source

            proj_svc = ProjectService(ProjectRepository(self.session))
            cached = await proj_svc.find_cached_source(
                project.git_url, None, lab.owner_id, ref_type="upload"
            )
            acquired = await asyncio.to_thread(
                acquire_uploaded_source,
                workdir_root,
                cached=cached,
            )
            if not acquired.ok:
                return (
                    repo_dirname,
                    acquired.error or "源码解包失败: 未找到已上传的源码包",
                )
            return acquired.repo_dirname or repo_dirname, None

        ok, err = await asyncio.to_thread(
            git_clone_to_workdir,
            workdir_root,
            project.git_url,
            lab.commit_sha,
            repo_dirname,
            ref_type="commit",
        )
        if not ok:
            return repo_dirname, err
        return repo_dirname, None

    async def destroy_lab(self, lab_id: str, *, owner_id: str) -> str:
        from . import docker_ops

        lab = await self._require_owned_lab(lab_id, owner_id)
        self._require_status(
            lab,
            {"ready", "stopped", "failed", "expired", "creating", "rebuilding"},
            "destroy",
        )
        if lab.status in {"creating", "rebuilding"}:
            for task_id in await self.live_task_ids(lab.id):
                try:
                    await self._task_service().cancel_task(task_id, owner_id)
                except ValueError:
                    pass
            await self.session.refresh(lab)
        else:
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
        containers = await docker_ops.list_containers(lab.compose_project)
        self._apply_aligned_status(lab, containers, live_task_count=0)
        if action in {"stop", "rm"}:
            # 显式停掉/删除任一 Compose 服务后，不得因“剩余容器都在跑”继续显示 ready。
            lab.status = "expired" if not containers else "stopped"
        if action in {"start", "restart"}:
            ready, detail = await self._probe_lab_ready(lab, retries=10)
            if not ready:
                lab.status = "stopped"
                lab.error_message = f"容器操作后探活失败: {detail}"[:500]
                await self._touch_and_commit(lab)
                raise RuntimeError(lab.error_message)
            lab.status = "ready"
            lab.error_message = None
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

    async def fail_stale_rebuilding(
        self, *, now: datetime | None = None
    ) -> list[str]:
        """标记长时间未完成的手动 rebuilding Lab 失败，并 best-effort 清理 compose。"""
        from . import docker_ops

        clock = self._as_utc(now or self._now())
        result = await self.session.execute(
            select(Lab).where(Lab.status == "rebuilding")
        )
        failed: list[str] = []
        for lab in result.scalars().all():
            if await self.live_task_ids(lab.id):
                continue
            last_seen = (
                self._as_utc(lab.last_seen_at) if lab.last_seen_at is not None else None
            )
            if (
                last_seen is not None
                and clock < last_seen + timedelta(seconds=_MANUAL_REBUILD_STALE_SECONDS)
            ):
                continue
            claimed = await self.repository.cas_status(lab.id, {"rebuilding"}, "failed")
            if not claimed:
                continue
            lab.error_message = "手动重建超时"
            await self.session.commit()
            if await self.live_task_ids(lab.id):
                await self.repository.cas_status(lab.id, {"failed"}, "rebuilding")
                await self.session.commit()
                continue
            await self.session.refresh(lab)
            if lab.status != "failed":
                continue
            try:
                await docker_ops.compose_down(lab.compose_project)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "清理超时 rebuilding Lab 失败(best-effort) lab=%s",
                    lab.id,
                    exc_info=True,
                )
            failed.append(lab.id)
        return failed

    async def fail_stale_creating(self) -> list[str]:
        """标记创建者已终态或租约过期的 creating Lab，并清理 compose。

        等待复用的任务也绑定在 Lab 上，不能用“任意 live task”阻止回收。
        """
        from . import docker_ops

        result = await self.session.execute(select(Lab).where(Lab.status == "creating"))
        failed: list[str] = []
        stale_before = self._now() - timedelta(seconds=CREATING_LEASE_SECONDS)
        for lab in result.scalars().all():
            live_ids = set(await self.live_task_ids(lab.id))
            creator_live = bool(
                lab.creator_task_id and lab.creator_task_id in live_ids
            )
            stale = (
                lab.last_seen_at is None
                or self._as_utc(lab.last_seen_at) <= stale_before
            )
            if creator_live and not stale:
                continue
            claimed = await self.repository.cas_status(lab.id, {"creating"}, "failed")
            if not claimed:
                continue
            await self.session.commit()
            # 仅“原创建者重新活跃且租约并未过期”才能撤销本次回收；等待者不算。
            live_after = set(await self.live_task_ids(lab.id))
            if (
                not stale
                and lab.creator_task_id
                and lab.creator_task_id in live_after
            ):
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

    async def cleanup_terminal_runtimes(self) -> list[str]:
        """回收已终态但补偿清理未完成的 Lab Compose 资源。"""
        from . import docker_ops

        result = await self.session.execute(
            select(Lab).where(Lab.status.in_({"failed", "destroyed", "expired"}))
        )
        cleaned: list[str] = []
        for lab in result.scalars().all():
            if await self.live_task_ids(lab.id):
                continue
            if lab.status == "rebuilding":
                continue
            if lab.status == "expired":
                try:
                    containers = await docker_ops.list_containers(lab.compose_project)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "列举过期 Lab 容器失败(best-effort) lab=%s",
                        lab.id,
                        exc_info=True,
                    )
                    containers = []
                if self._apply_aligned_status(lab, containers, live_task_count=0):
                    await self.session.commit()
                    continue
            try:
                await docker_ops.compose_down(lab.compose_project)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "清理终态 Lab 运行时失败(best-effort) lab=%s",
                    lab.id,
                    exc_info=True,
                )
                continue
            cleaned.append(lab.id)
        return cleaned

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
        ttl_seconds = lab.ttl_seconds if lab.ttl_seconds is not None else 3600
        ttl_remaining = ttl_remaining_seconds(
            lab.status, lab.last_seen_at, ttl_seconds, now
        )
        # list_containers 的 dict 带内部字段 state（供 _container_is_running 用），
        # 响应模型按契约只暴露 4 个字段且 extra=forbid，组装时必须剥掉。
        contract_containers = [
            {key: container.get(key, "") for key in ("name", "status", "ports", "image")}
            for container in containers
        ]
        return {
            "id": lab.id,
            "project_id": lab.project_id,
            "commit_sha": lab.commit_sha,
            "status": lab.status,
            "target_url": lab.target_url,
            "ttl_remaining_seconds": ttl_remaining,
            "containers": contract_containers,
            "live_task_count": live_task_count,
            "error_message": lab.error_message,
        }

    async def _require_lab(self, lab_id: str) -> Lab:
        lab = await self.repository.get(lab_id)
        if lab is None:
            raise LookupError(f"Lab 不存在: {lab_id}")
        return lab

    @staticmethod
    async def _compose_project_present(compose_project: str) -> bool:
        from . import docker_ops

        return bool(await docker_ops.list_containers(compose_project))

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
        return as_utc(value)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
