"""
Agent 漏洞分析工作流（Celery 任务）。

流程：
  1. 加载任务 → 创建/获取 TaskRun（running）
  2. 最小预检：LLM Provider / agent-runner 镜像 / 凭据
  3. 准备 host 临时目录 + git clone（host 上，避免容器销毁源码丢）
  4. 构造 .prompt.json + 注入凭据 env
  5. 拉起 agent-runner 容器 + 流式消费 stdout JSONL → 实时落 AgentEvent + 发 Redis Pub/Sub
  6. 容器结束 → 状态分流（completed / needs_review / failed / cancelled）
  7. 持久化 reasoning + session_id
  8. 生成报告（report context）
  9. 发布 domain 事件
 10. finally 清理：docker rm -f 容器 + shutil.rmtree host_workdir

SDK 改造要点（v0.3.0+）：
- 不再依赖 SandboxManager；唯一容器抽象为 core/agent_runner.py::AgentRunnerManager
- 流式事件由容器内 run_one.py 翻译为统一 schema（兼容旧前端 Timeline）
- 实时落库通过 asyncio.run_coroutine_threadsafe 跨入主 loop（见 concurrency.md §2）

注意：Celery worker 为同步线程模型，但平台 DB 层为 async。
本模块通过 asyncio.run 进入异步世界，并使用独立 NullPool 引擎，
避免连接跨 event loop 复用导致的 "attached to a different loop" 错误。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.contexts.identity.models import User  # noqa: F401 — 注册 users 表到 metadata，保证 FK 解析
from app.contexts.report.models import Report, Evidence  # noqa: F401 — 注册 reports/evidences 表
from app.contexts.report.repository import ReportRepository
from app.contexts.report.service import ReportService
from app.contexts.settings.models import LlmProvider  # noqa: F401 — 注册 llm_providers 表
from app.contexts.task.models import AgentEvent, Task, TaskRun
from app.core.agent_runner import AgentRunnerError, agent_runner_manager, git_clone_to_workdir
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.shared.events import Event, event_bus

from .executor import AgentRunContext, ClaudeSdkExecutor, get_executor
from .sdk_adapter import resolve_runner_env

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Celery worker 专用 DB 会话（独立 NullPool 引擎） ──


def _worker_engine():
    connect_args = {}
    if "sqlite" in settings.database_url:
        connect_args["check_same_thread"] = False
    return create_async_engine(settings.database_url, poolclass=NullPool, connect_args=connect_args)


async def _load_task(session: AsyncSession, task_id: str) -> Task | None:
    result = await session.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()


async def _get_or_create_run(session: AsyncSession, task_id: str, run_id: str) -> TaskRun:
    """复用 API 层创建的 run（run_id），不存在则兜底创建"""
    run = await session.get(TaskRun, run_id)
    if run is None:
        run = TaskRun(task_id=task_id, status="running", started_at=datetime.now(timezone.utc))
        session.add(run)
        await session.flush()
        await session.refresh(run)
    else:
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        await session.flush()
    return run


async def _append_events(session: AsyncSession, run: TaskRun, events: list[dict]) -> None:
    """将执行器事件流持久化为 AgentEvent 行，sequence 从运行内已有事件后递增"""
    base = 0
    result = await session.execute(
        select(AgentEvent.sequence).where(AgentEvent.run_id == run.id).order_by(AgentEvent.sequence.desc()).limit(1)
    )
    latest = result.scalar_one_or_none()
    if latest is not None:
        base = latest

    for i, ev in enumerate(events, start=base + 1):
        session.add(
            AgentEvent(
                run_id=run.id,
                task_id=run.task_id,
                sequence=i,
                event_type=ev.get("type", "phase.updated"),
                payload=json.dumps(ev, ensure_ascii=False, default=str),
                source="claude-agent-sdk",
            )
        )
    await session.flush()


async def _persist_single_event(session: AsyncSession, run: TaskRun, event: dict) -> None:
    """实时落单条事件（流式回调使用），并发布到 Redis Pub/Sub 以驱动 SSE 推送"""
    # 取本 run 当前最大 sequence
    result = await session.execute(
        select(AgentEvent.sequence).where(AgentEvent.run_id == run.id).order_by(AgentEvent.sequence.desc()).limit(1)
    )
    latest = result.scalar_one_or_none()
    next_seq = (latest or 0) + 1
    event_type = event.get("type", "phase.updated")
    payload_json = json.dumps(event, ensure_ascii=False, default=str)
    session.add(
        AgentEvent(
            run_id=run.id,
            task_id=run.task_id,
            sequence=next_seq,
            event_type=event_type,
            payload=payload_json,
            source="claude-agent-sdk",
        )
    )
    await session.flush()

    # 发布到 Redis Pub/Sub — SSE 端点订阅本 task 频道后实时转发
    # 失败仅日志，不阻塞 Agent 流（与「事件总线失败不影响主流程」原则一致）
    try:
        await event_bus.publish(
            f"task.{run.task_id}.events",
            Event(
                event_type=event_type,
                aggregate_id=str(run.task_id),
                aggregate_type="task",
                payload={
                    "sequence": next_seq,
                    "run_id": str(run.id),
                    "event": event,
                },
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Redis Pub/Sub 发布失败（不影响主流程）: {e}")


async def _update_run(session: AsyncSession, run: TaskRun, status: str, error: str | None = None) -> None:
    run.status = status
    run.error_message = error
    if status in ("completed", "failed", "cancelled"):
        run.finished_at = datetime.now(timezone.utc)
    await session.flush()


# ── 取消信号钩子（保险 A）──

_known_container_ids: set[str] = set()


def _register_container(container_id: str | None) -> None:
    if container_id:
        _known_container_ids.add(container_id)


def _unregister_container(container_id: str | None) -> None:
    if container_id:
        _known_container_ids.discard(container_id)


def _on_sigterm(_signum, _frame):  # noqa: ANN001
    """Celery revoke(terminate=True) 发 SIGTERM → 先强杀 agent-runner 容器，再退出"""
    logger.warning(f"收到 SIGTERM，清理 {len(_known_container_ids)} 个 agent-runner 容器")
    for cid in list(_known_container_ids):
        try:
            agent_runner_manager.remove_by_id(cid)
        except Exception:  # noqa: BLE001
            pass
    _known_container_ids.clear()
    # 恢复默认 SIGTERM 行为（让 Celery 走正常清理）
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except (ValueError, OSError):
        pass
    os.kill(os.getpid(), signal.SIGTERM)


# 仅 Linux/Mac 注册；Windows 用 CTRL_BREAK_EVENT（run_worker.py 单独处理）
if os.name != "nt":
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        # 主线程外注册会抛 ValueError
        pass


# ── 最小预检 ──


async def _platform_preflight_minimal(session: AsyncSession) -> tuple[bool, str | None]:
    """最小预检：镜像存在 + 凭据完整。

    不实现 agent-workflow.md §2.2 S0 的完整检查（MinIO / python-docx 等）——
    留作阶段化 backlog。本任务只保证 SDK 跑得起来。
    """
    # 1. 镜像存在
    exists = await asyncio.to_thread(agent_runner_manager.image_exists)
    if not exists:
        return False, f"agent-runner 镜像不存在: {settings.agent_runner_image}（先 docker build）"

    # 2. 凭据：检查默认 Provider 或 settings.llm_api_key
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    try:
        provider = await SettingsService(SettingsRepository(session)).get_default_provider()
        if provider is not None:
            return True, None
    except Exception:  # noqa: BLE001 — DB 失败不阻断
        pass

    if settings.llm_api_key:
        return True, None
    return False, "缺少 LLM 凭据：DB 默认 Provider 与 settings.llm_api_key 都为空"


# ── Celery 任务 ──


@celery_app.task(bind=True, name="agent.run_analysis")
def run_analysis(self, task_id: str, run_id: str) -> dict:
    """漏洞分析主任务"""
    return asyncio.run(_run_analysis(task_id, run_id))


async def _run_analysis(task_id: str, run_id: str) -> dict:
    engine = _worker_engine()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    host_workdir = ""
    container_id: str | None = None
    summary: dict = {"task_id": task_id, "run_id": run_id}

    try:
        async with session_factory() as session:
            task = await _load_task(session, task_id)
            if task is None:
                summary.update(status="failed", error="任务不存在")
                return summary

            task.status = "running"
            run = await _get_or_create_run(session, task_id, run_id)

            # 1. 最小预检
            ok, preflight_err = await _platform_preflight_minimal(session)
            if not ok:
                await _update_run(session, run, "failed", f"infra_error: {preflight_err}")
                task.status = "failed"
                await session.commit()
                summary.update(status="failed", error=preflight_err)
                return summary

            # 2. host 临时目录 + git clone
            host_workdir = agent_runner_manager.host_workdir_path(task_id)
            os.makedirs(host_workdir, exist_ok=True)
            await _append_events(
                session, run,
                [{"type": "phase.updated", "phase": "preflight", "message": "在 host 上克隆源码",
                  "source": "crucible"}],
            )
            await session.commit()

            clone_ok, clone_err = await asyncio.to_thread(
                git_clone_to_workdir, host_workdir, task.project_address, task.project_ref
            )
            if not clone_ok:
                err = f"源码克隆失败: {clone_err[:500]}"
                await _update_run(session, run, "failed", err)
                task.status = "failed"
                await session.commit()
                _cleanup_hostdir(host_workdir)
                summary.update(status="failed", error=err)
                return summary

            # 3. 构造 runner env（凭据从默认 Provider 解密或 settings.llm_* 兜底）
            runner_env = await resolve_runner_env(session)

            # 4. 流式拉起 agent-runner + 实时落库
            loop = asyncio.get_running_loop()
            captured: dict = {}

            def on_event(event: dict) -> None:
                future = asyncio.run_coroutine_threadsafe(
                    _persist_single_event(session, run, event), loop
                )
                try:
                    future.result(timeout=5)
                except Exception as e:  # noqa: BLE001 — 落库失败仅日志，不阻塞流
                    logger.warning(f"实时落库失败（不影响主流程）: {e}")

            executor = ClaudeSdkExecutor()
            ctx = AgentRunContext(
                task_id=task_id,
                run_id=run_id,
                project_address=task.project_address,
                project_ref=task.project_ref,
                vulnerability_description=task.vulnerability_description,
                host_workdir=host_workdir,
                runner_env=runner_env,
            )
            try:
                result = await asyncio.to_thread(executor.run, ctx, on_event)
                captured["result"] = result
                container_id = result.container_id
                _register_container(container_id)
            except AgentRunnerError as e:
                captured["error"] = e
            except Exception as e:  # noqa: BLE001
                captured["error"] = e
            finally:
                _unregister_container(container_id)

            # 5. 状态分流 + 持久化
            result = captured.get("result")
            if result is None:
                err = str(captured.get("error", "agent_runner 未返回结果"))
                await _update_run(session, run, "failed", err[:500])
                task.status = "failed"
            elif result.conclusion == "cancelled":
                await _update_run(session, run, "cancelled", result.error_message)
                task.status = "cancelled"
            elif result.exit_code != 0 and result.exit_code != 137 and result.conclusion == "failed":
                await _update_run(session, run, "failed", result.error_message)
                task.status = "failed"
            elif result.conclusion == "exists":
                await _update_run(session, run, "completed")
                task.status = "needs_review"
            else:
                await _update_run(session, run, "completed")
                task.status = "completed"

            # 6. 持久化 reasoning + session_id
            if result is not None:
                task.vulnerability_reasoning = (result.reasoning or "")[:20000] or None
                if result.session_id:
                    run.agent_session_id = result.session_id
            await session.commit()

            # 7. 生成报告（agent 产出 → report context）
            if result is not None and task.status in ("completed", "needs_review"):
                try:
                    report_svc = ReportService(ReportRepository(session))
                    await report_svc.generate_from_agent(
                        task_id=task_id,
                        run_id=run_id,
                        owner_id=task.owner_id,
                        conclusion=result.conclusion,
                        reasoning=result.reasoning or "",
                        events=result.events,
                    )
                    await session.commit()
                    summary["report_generated"] = True
                except Exception as e:  # noqa: BLE001 — 报告生成失败不影响主流程
                    summary["report_generated"] = False
                    summary["report_error"] = str(e)

            # 8. 发布 domain 事件
            try:
                await event_bus.publish_domain_event(
                    Event(
                        event_type="run.completed",
                        aggregate_id=run_id,
                        aggregate_type="task_run",
                        payload={
                            "task_id": task_id,
                            "run_status": run.status,
                            "conclusion": result.conclusion if result else None,
                        },
                    )
                )
                await event_bus.publish_domain_event(
                    Event(
                        event_type="task.updated",
                        aggregate_id=task_id,
                        aggregate_type="task",
                        payload={"status": task.status},
                    )
                )
            except Exception:  # noqa: BLE001 — 事件总线失败不影响主流程
                pass

            summary.update(
                status=task.status,
                run_status=run.status,
                conclusion=result.conclusion if result else None,
                events_count=len(result.events) if result else 0,
                container_id=container_id,
            )

    except Exception as e:  # noqa: BLE001 — 兜底：任何异常都要落库并返回
        logger.exception(f"run_analysis 兜底异常: {e}")
        try:
            async with session_factory() as session:
                run = await session.get(TaskRun, run_id)
                task = await session.get(Task, task_id)
                if run:
                    await _update_run(session, run, "failed", str(e))
                if task:
                    task.status = "failed"
                await session.commit()
        except Exception:
            pass
        summary.update(status="failed", error=str(e))
    finally:
        # 9. 清理容器与 host_workdir
        if container_id:
            try:
                await asyncio.to_thread(agent_runner_manager.remove_by_id, container_id)
            except Exception:
                pass
            _unregister_container(container_id)
        _cleanup_hostdir(host_workdir)
        await engine.dispose()

    return summary


def _cleanup_hostdir(path: str) -> None:
    """删除 host 临时目录（best-effort）"""
    if not path:
        return
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"清理 host_workdir 失败 {path}: {e}")