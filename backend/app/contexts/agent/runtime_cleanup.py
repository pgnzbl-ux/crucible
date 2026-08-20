"""任务取消 / 结束 / 巡检时拆掉 agent-runner（best-effort）。

热路径：cancel / 任务结束 / 删除调用 teardown_task_runtime。
安全网：Celery worker 启动后每 5 分钟扫一次孤儿（含超时卡住的 running）。
强杀门槛是 run 级硬顶（agent_run_hard_timeout_seconds）；单容器超时
（agent_runner_timeout_seconds）由 run_with_streaming 内的定时器负责，两层解耦。
身份：agent-runner 标签 crucible.task_id，或从 host_workdir 挂载路径反查。
发现源只认 Docker 容器，不把失败任务留下的 compose.yml 当成还活着的运行时。
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable, Iterable

from app.core.agent_runner import agent_runner_manager
from app.core.config import get_settings
from app.shared.time import utc_unix

logger = logging.getLogger(__name__)

LAB_PROJECT_PREFIX = "crucible-lab-"
SWEEP_INTERVAL_SECONDS = 300
LIVE_STATUSES = frozenset({"pending", "queued", "running"})

_sweeper_started = False


def lab_project_name(lab_id: str) -> str:
    """Docker Compose 项目名：小写、可扫、与其他靶场隔离。"""
    return f"{LAB_PROJECT_PREFIX}{(lab_id or '').lower()}"


def task_id_from_lab_project(name: str) -> str | None:
    raw = (name or "").lower()
    if raw.startswith(LAB_PROJECT_PREFIX):
        tid = raw[len(LAB_PROJECT_PREFIX) :]
        return tid or None
    return None


def task_id_from_host_path(path: str, workdir_base: str) -> str | None:
    """从宿主机路径反查 task_id（含仓库子目录挂载）。"""
    prefix = os.path.basename((workdir_base or "").rstrip("/").rstrip("\\")) + "-"
    if prefix == "-":
        return None
    current = os.path.normpath((path or "").replace("/", os.sep))
    while current and current != os.path.dirname(current):
        name = os.path.basename(current)
        if name.startswith(prefix):
            tid = name[len(prefix) :]
            return tid or None
        current = os.path.dirname(current)
    return None


def should_keep_runtime(status: str | None, age_seconds: float, timeout_seconds: int) -> bool:
    """仅保留未超时的 pending/queued/running。

    timeout_seconds 应传 run 级硬顶（agent_run_hard_timeout_seconds），
    单容器超时由 run_with_streaming 内的定时器负责，巡检不做单容器级强杀。
    """
    return bool(status) and status in LIVE_STATUSES and age_seconds < timeout_seconds


async def teardown_task_runtime(task_id: str) -> None:
    """只拆该任务的 agent-runner；共享靶场由 Lab 生命周期管理。幂等。"""
    if not task_id:
        return
    workdir = agent_runner_manager.host_workdir_path(task_id)
    try:
        agent_runner_manager.remove_for_task(task_id, workdir)
    except Exception:  # noqa: BLE001
        logger.warning("拆 agent-runner 失败(best-effort) task=%s", task_id, exc_info=True)


def schedule_teardown_task_runtime(task_id: str) -> None:
    """后台拆容器，不阻塞取消接口。"""
    if not task_id:
        return

    async def _run() -> None:
        try:
            await teardown_task_runtime(task_id)
        except Exception:  # noqa: BLE001
            logger.warning("后台拆容器失败(best-effort) task=%s", task_id, exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("无事件循环，同步拆容器 task=%s", task_id)
        asyncio.run(_run())
        return
    loop.create_task(_run())


async def sweep_orphan_runtimes(
    *,
    discovered_ids: set[str] | None = None,
    status_by_id: dict[str, tuple[str | None, float | None]] | None = None,
    now: float | None = None,
    timeout_seconds: int | None = None,
    teardown: Callable[[str], Awaitable[None]] | None = None,
) -> list[str]:
    """拆掉已结束 / 超时 / 库里没有的任务运行时。返回被拆的 task_id。

    默认门槛用 run 级硬顶（agent_run_hard_timeout_seconds，默认 7200s）；
    单容器 1800s 超时由 run_with_streaming 的定时器独立负责。
    """
    clock = time.time() if now is None else now
    timeout = (
        get_settings().agent_run_hard_timeout_seconds if timeout_seconds is None else timeout_seconds
    )
    ids = discovered_ids if discovered_ids is not None else list_managed_task_ids()
    statuses = status_by_id if status_by_id is not None else await load_task_runtime_status(ids)
    tear = teardown or teardown_task_runtime
    torn: list[str] = []
    for tid in ids:
        status, started = statuses.get(tid, (None, None))
        age = (clock - started) if started is not None else timeout + 1
        if should_keep_runtime(status, age, timeout):
            continue
        try:
            await tear(tid)
            torn.append(tid)
        except Exception:  # noqa: BLE001
            logger.warning("巡检拆容器失败 task=%s", tid, exc_info=True)
    if torn:
        logger.info("orphan runtime sweep 拆掉 %s 个任务: %s", len(torn), torn)
    return torn


async def list_lab_compose_projects() -> set[str]:
    """从 Docker 枚举所有 crucible-lab-* compose 项目。"""
    cmd = [
        "docker",
        "ps",
        "-a",
        "--format",
        '{{.Label "com.docker.compose.project"}}',
    ]
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:  # noqa: BLE001
        logger.warning("列举 Lab compose 项目失败(best-effort)", exc_info=True)
        return set()
    if result.returncode != 0:
        logger.warning(
            "列举 Lab compose 项目失败(best-effort): %s",
            (result.stderr or result.stdout or "")[:300],
        )
        return set()
    return {
        project.strip()
        for project in result.stdout.splitlines()
        if project.strip().lower().startswith(LAB_PROJECT_PREFIX)
    }


async def cleanup_legacy_lab_projects(
    projects: Iterable[str],
    known_lab_ids: set[str],
    *,
    down: Callable[[str], Awaitable[None]] | None = None,
) -> list[str]:
    """清理后缀不对应任何 labs.id 的历史 crucible-lab-{task_id}。"""
    from app.contexts.lab.docker_ops import compose_down

    known = {lab_id.lower() for lab_id in known_lab_ids}
    cleanup = down or compose_down
    removed: list[str] = []
    for project in sorted(set(projects), key=str.lower):
        raw = (project or "").strip()
        lowered = raw.lower()
        if not lowered.startswith(LAB_PROJECT_PREFIX):
            continue
        suffix = lowered[len(LAB_PROJECT_PREFIX) :]
        if not suffix or suffix in known:
            continue
        try:
            await cleanup(raw)
            removed.append(raw)
        except Exception:  # noqa: BLE001
            logger.warning(
                "清理历史 Lab compose 失败(best-effort) project=%s",
                raw,
                exc_info=True,
            )
    return removed


async def run_lab_lifecycle_phases(service) -> None:
    """隔离 TTL、僵死 creating 与历史孤儿三个巡检阶段。"""
    try:
        await service.expire_silent_labs()
    except Exception:  # noqa: BLE001
        logger.exception("Lab TTL 巡检失败")
        await service.session.rollback()

    try:
        await service.fail_stale_creating()
    except Exception:  # noqa: BLE001
        logger.exception("Lab creating 巡检失败")
        await service.session.rollback()

    try:
        await service.fail_stale_rebuilding()
    except Exception:  # noqa: BLE001
        logger.exception("Lab rebuilding 巡检失败")
        await service.session.rollback()

    try:
        await service.cleanup_terminal_runtimes()
    except Exception:  # noqa: BLE001
        logger.exception("Lab 终态运行时巡检失败")
        await service.session.rollback()

    try:
        known_lab_ids = await service.known_lab_ids()
        projects = await list_lab_compose_projects()
        removed = await cleanup_legacy_lab_projects(projects, known_lab_ids)
        if removed:
            logger.info("清理 %s 个历史 Lab compose: %s", len(removed), removed)
    except Exception:  # noqa: BLE001
        logger.exception("历史 Lab compose 巡检失败")
        await service.session.rollback()


async def sweep_lab_lifecycle() -> None:
    """用独立 DB session 执行 TTL、僵死 creating 与历史孤儿巡检。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.contexts.lab.service import LabService

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            service = LabService(session)
            await run_lab_lifecycle_phases(service)
    finally:
        await engine.dispose()


async def sweep_stale_queued_dispatches() -> None:
    """用独立 DB session 把滞留 queued 按原 run.id 再投递。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            await TaskService(TaskRepository(session)).redispatch_stale_queued()
    finally:
        await engine.dispose()


async def sweep_all_runtimes() -> None:
    """先扫 agent-runner，再巡检共享 Lab 生命周期，最后回收滞留 queued。"""
    from app.contexts.agent.task_slots import reconcile_slots, try_sweeper_lock

    try:
        locked = await try_sweeper_lock()
    except Exception:  # noqa: BLE001
        logger.exception("巡检抢锁失败，跳过本轮")
        return
    if not locked:
        return
    await sweep_orphan_runtimes()
    await sweep_lab_lifecycle()
    try:
        await sweep_stale_queued_dispatches()
    except Exception:  # noqa: BLE001
        logger.exception("滞留 queued 再投递失败")
    try:
        await reconcile_slots(await load_running_run_ids())
    except Exception:  # noqa: BLE001
        logger.exception("运行槽调和失败")


async def load_running_run_ids() -> set[str]:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.contexts.task.models import TaskRun

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = (
                await session.execute(select(TaskRun.id).where(TaskRun.status == "running"))
            ).scalars().all()
            return set(rows)
    finally:
        await engine.dispose()


def collect_task_ids_from_containers(containers: Iterable, workdir_base: str) -> set[str]:
    """从任务标签、挂载路径收集 task_id；忽略共享 Lab 的 compose 项目名。"""
    ids: set[str] = set()
    for container in containers:
        labels = getattr(container, "labels", None) or {}
        tid = labels.get("crucible.task_id")
        if tid:
            ids.add(tid)
        mounts = (getattr(container, "attrs", None) or {}).get("Mounts") or []
        for mount in mounts:
            from_mount = task_id_from_host_path(mount.get("Source") or "", workdir_base)
            if from_mount:
                ids.add(from_mount)
    return ids


def list_managed_task_ids() -> set[str]:
    """从 Docker 容器收集 task_id。残留 host_workdir / compose.yml 不算运行时。"""
    try:
        client = agent_runner_manager._client_or_connect()  # noqa: SLF001 — 平台能力内部枚举
        containers = client.containers.list(all=True)
    except Exception:  # noqa: BLE001
        logger.warning("列举托管容器失败(best-effort)", exc_info=True)
        return set()
    return collect_task_ids_from_containers(
        containers, get_settings().agent_runner_workdir_base
    )


async def load_task_runtime_status(
    task_ids: Iterable[str],
) -> dict[str, tuple[str | None, float | None]]:
    """task_id → (status, started_at unix)。缺行则不出现在 dict 里。"""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.contexts.task.models import Task, TaskRun

    ids = [tid for tid in task_ids if tid]
    if not ids:
        return {}
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result: dict[str, tuple[str | None, float | None]] = {}
    try:
        async with factory() as session:
            tasks = (await session.execute(select(Task).where(Task.id.in_(ids)))).scalars().all()
            for task in tasks:
                started = utc_unix(task.created_at)
                result[task.id] = (task.status, started)
            runs = (
                (
                    await session.execute(
                        select(TaskRun)
                        .where(TaskRun.task_id.in_(ids))
                        .order_by(TaskRun.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            seen: set[str] = set()
            for run in runs:
                if run.task_id in seen:
                    continue
                seen.add(run.task_id)
                status, created = result.get(run.task_id, (None, None))
                ts = utc_unix(run.started_at) if run.started_at else created
                result[run.task_id] = (status, ts)
    finally:
        await engine.dispose()
    return result


def start_background_sweeper() -> None:
    """Celery worker 内守护线程：启动先扫一次，之后每 5 分钟。"""
    global _sweeper_started
    if _sweeper_started:
        return
    _sweeper_started = True

    def _loop() -> None:
        import asyncio

        while True:
            try:
                asyncio.run(sweep_all_runtimes())
            except Exception:  # noqa: BLE001
                logger.exception("orphan runtime sweep 失败")
            time.sleep(SWEEP_INTERVAL_SECONDS)

    threading.Thread(target=_loop, name="crucible-runtime-sweeper", daemon=True).start()
    logger.info("runtime sweeper 已启动 interval=%ss", SWEEP_INTERVAL_SECONDS)
