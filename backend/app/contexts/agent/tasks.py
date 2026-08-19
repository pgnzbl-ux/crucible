"""
Agent 漏洞分析工作流（Celery 任务）。

流程：
  1. 加载任务 → 创建/获取 TaskRun（running）
  2. 最小预检：LLM Provider / agent-runner 镜像 / 凭据
  3. 准备 host 临时目录（源码由节点 0 按表/MinIO 或 git clone 落到 {workdir}/{仓库名}）
  4. 构造 .prompt.json + 注入凭据 env
  5. 拉起 agent-runner 容器 + 流式消费 stdout JSONL → 实时落 AgentEvent + 发 Redis Pub/Sub
  6. 容器结束 → 状态分流（completed / needs_review / failed / cancelled）
  7. 持久化 reasoning + session_id
  8. 生成报告（report context）
  9. 发布 domain 事件
  10. finally：成功则清理 host_workdir；失败/取消保留目录供排查

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

from celery.exceptions import Retry
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.contexts.identity.models import User  # noqa: F401 — 注册 users 表到 metadata，保证 FK 解析
from app.contexts.lab.models import Lab  # noqa: F401
from app.contexts.project.models import Project, SourceArtifact  # noqa: F401 — 注册 projects/source_artifacts
from app.contexts.report.models import Report, Evidence  # noqa: F401 — 注册 reports/evidences 表
from app.contexts.report.repository import ReportRepository
from app.contexts.report.service import ReportService
from app.contexts.agent.task_slots import release_slot, try_acquire_slot
from app.contexts.settings.models import LlmProvider, PlatformSetting  # noqa: F401 — 注册 llm_providers / platform_settings
from app.contexts.task.models import AgentEvent, Task, TaskRun, NodeRun  # noqa: F401 — NodeRun 注册(阶段 1)
from app.contexts.agent.errors import format_agent_error, humanize_agent_error
from app.core.agent_runner import AgentRunnerError, agent_runner_manager
from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.shared.events import Event, event_bus

from .sdk_adapter import resolve_runner_env

logger = logging.getLogger(__name__)
settings = get_settings()


_CONFIRMED_VERDICTS = ("confirmed", "partial")


def report_columns_from_orch_result(orch_result: dict) -> dict:
    """从编排收尾 dict 抽出 Report 索引列。未确认判定强制无 CVSS。"""
    rd = orch_result.get("report_data") or {}
    cvss = orch_result.get("cvss") or {}
    poc = orch_result.get("poc") if isinstance(orch_result.get("poc"), dict) else {}
    intro = rd.get("product_intro") if isinstance(rd, dict) else ""
    verdict = orch_result.get("verdict")
    confirmed = verdict in _CONFIRMED_VERDICTS
    score = cvss.get("base_score") if isinstance(cvss, dict) and confirmed else None
    return {
        "verdict": verdict,
        "cvss_score": float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
        "severity": (cvss.get("severity") if isinstance(cvss, dict) and confirmed else None),
        "vulnerable_file": orch_result.get("vulnerable_file") or None,
        "summary": str(intro or "")[:500],
        "title": "漏洞验证报告" if confirmed else "漏洞验证记录",
        "poc_language": (str(poc["language"]) if confirmed and poc.get("language") else None),
        "poc_filename": (str(poc["filename"]) if confirmed and poc.get("filename") else None),
        "poc_code": (str(poc["code"]) if confirmed and poc.get("code") else None),
        "poc_usage": (str(poc["usage"]) if confirmed and poc.get("usage") else None),
    }


async def ensure_report_for_run(session: AsyncSession, report: Report) -> Report:
    """同一 run 只落一份报告；唯一约束冲突时复用已有行。"""
    existing = (
        await session.execute(select(Report).where(Report.run_id == report.run_id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            session.add(report)
            await session.flush()
        await session.refresh(report)
        return report
    except IntegrityError:
        found = (
            await session.execute(select(Report).where(Report.run_id == report.run_id))
        ).scalar_one_or_none()
        if found is None:
            raise
        return found


# ── Celery worker 专用 DB 会话（独立 NullPool 引擎） ──


def _worker_engine():
    connect_args = {}
    if "sqlite" in settings.database_url:
        connect_args["check_same_thread"] = False
    return create_async_engine(settings.database_url, poolclass=NullPool, connect_args=connect_args)


_CLAIMABLE_TASK_STATUSES = frozenset({"pending", "queued"})
_CLAIMABLE_RUN_STATUSES = frozenset({"pending", "preflight"})
_PROTECTED_TERMINAL_STATUSES = frozenset({"cancelled", "archived"})


async def _load_task(session: AsyncSession, task_id: str) -> Task | None:
    result = await session.execute(select(Task).where(Task.id == task_id))
    return result.scalar_one_or_none()


async def claim_task_run(
    session: AsyncSession, task_id: str, run_id: str
) -> tuple[Task | None, TaskRun | None, str | None]:
    """认领 pending/queued 任务。同一 run 已 running 视为 Celery 重投续跑；禁止第二条 run 抢跑。"""
    task = await _load_task(session, task_id)
    if task is None:
        return None, None, "任务不存在"

    run = await session.get(TaskRun, run_id)
    if run is not None and run.task_id != task_id:
        return None, None, "运行不属于该任务"
    if run is not None and run.status == "cancelled":
        return None, None, "运行已取消，拒绝启动"

    if task.status == "running" and run is not None and run.status == "running":
        return task, run, None

    if task.status not in _CLAIMABLE_TASK_STATUSES:
        return None, None, f"任务已是 {task.status}，拒绝启动"

    claimed = await session.execute(
        update(Task)
        .where(Task.id == task_id, Task.status.in_(tuple(_CLAIMABLE_TASK_STATUSES)))
        .values(status="running")
    )
    if claimed.rowcount == 0:
        await session.refresh(task)
        if task.status == "running" and run is not None:
            await session.refresh(run)
            if run.status == "running":
                return task, run, None
        return None, None, f"任务已是 {task.status}，拒绝启动"

    await session.refresh(task)
    if run is None:
        run = TaskRun(id=run_id, task_id=task_id, status="running", started_at=datetime.now(timezone.utc))
        session.add(run)
        await session.flush()
        await session.refresh(run)
    else:
        run_claimed = await session.execute(
            update(TaskRun)
            .where(TaskRun.id == run_id, TaskRun.status.in_(tuple(_CLAIMABLE_RUN_STATUSES)))
            .values(status="running", started_at=datetime.now(timezone.utc))
        )
        if run_claimed.rowcount == 0:
            await session.refresh(run)
            if run.status == "running":
                await session.commit()
                return task, run, None
            return None, None, f"运行已是 {run.status}，拒绝启动"
        await session.refresh(run)
    await session.commit()
    return task, run, None


async def admit_task_run(
    session: AsyncSession,
    celery_task: object,
    task_id: str,
    run_id: str,
) -> tuple[Task | None, TaskRun | None, str | None, bool]:
    """先判断可否启动，再抢槽，再 claim。无槽则 celery retry，不改 queued。"""
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    task = await _load_task(session, task_id)
    run = await session.get(TaskRun, run_id)
    if task is None:
        return None, run, "任务不存在", False
    if run is not None and run.task_id != task_id:
        return task, run, "运行不属于该任务", False
    if run is not None and run.status == "cancelled":
        return task, run, "运行已取消，拒绝启动", False
    redelivery = task.status == "running" and run is not None and run.status == "running"
    if not redelivery and task.status not in _CLAIMABLE_TASK_STATUSES:
        return task, run, f"任务已是 {task.status}，拒绝启动", False

    runtime = await SettingsService(SettingsRepository(session)).get_runtime_settings()
    if not await try_acquire_slot(run_id, runtime.max_concurrent_tasks):
        raise celery_task.retry(countdown=15)  # type: ignore[attr-defined]

    claimed_task, claimed_run, err = await claim_task_run(session, task_id, run_id)
    if err:
        await release_slot(run_id)
        return claimed_task, claimed_run, err, False
    return claimed_task, claimed_run, None, True


_SKIP_PHASE_MESSAGES = frozenset({"thinking_tokens"})


def should_persist_agent_event(event: dict) -> bool:
    """丢弃 SDK 用量心跳，避免事件流被 thinking_tokens 刷屏。"""
    if event.get("type") != "phase.updated":
        return True
    return str(event.get("message") or "") not in _SKIP_PHASE_MESSAGES


def ensure_event_timestamp(event: dict) -> dict:
    """平台自己发的事件补 timestamp。

    SDK 事件自带 epoch timestamp；平台事件（phase.updated 等）没有的话，
    前端只能退回 created_at，SSE 回放时会被打上「浏览器收到的时刻」。
    """
    if isinstance(event.get("timestamp"), (int, float)):
        return event
    return {**event, "timestamp": datetime.now(timezone.utc).timestamp()}


async def _resolve_node_run_id(session: AsyncSession, run: TaskRun, event: dict) -> str | None:
    node_run_id = event.get("node_run_id")
    if isinstance(node_run_id, str) and node_run_id:
        return node_run_id
    node_key = event.get("node_key")
    if not isinstance(node_key, str) or not node_key:
        return None
    result = await session.execute(
        select(NodeRun.id).where(NodeRun.run_id == run.id, NodeRun.node_key == node_key)
    )
    return result.scalar_one_or_none()


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
        if not should_persist_agent_event(ev):
            continue
        ev = ensure_event_timestamp(ev)
        session.add(
            AgentEvent(
                run_id=run.id,
                task_id=run.task_id,
                node_run_id=await _resolve_node_run_id(session, run, ev),
                sequence=i,
                event_type=ev.get("type", "phase.updated"),
                payload=json.dumps(ev, ensure_ascii=False, default=str),
                source="claude-agent-sdk",
            )
        )
    await session.flush()


async def _persist_single_event(session: AsyncSession, run: TaskRun, event: dict) -> None:
    """实时落单条事件（流式回调使用），并发布到 Redis Pub/Sub 以驱动 SSE 推送"""
    if not should_persist_agent_event(event):
        return
    event = ensure_event_timestamp(event)
    event_type = event.get("type", "phase.updated")
    payload_json = json.dumps(event, ensure_ascii=False, default=str)
    node_run_id = await _resolve_node_run_id(session, run, event)
    next_seq = 0
    for _ in range(8):
        result = await session.execute(
            select(AgentEvent.sequence).where(AgentEvent.run_id == run.id).order_by(AgentEvent.sequence.desc()).limit(1)
        )
        next_seq = (result.scalar_one_or_none() or 0) + 1
        try:
            async with session.begin_nested():
                session.add(
                    AgentEvent(
                        run_id=run.id,
                        task_id=run.task_id,
                        node_run_id=node_run_id,
                        sequence=next_seq,
                        event_type=event_type,
                        payload=payload_json,
                        source="claude-agent-sdk",
                    )
                )
                await session.flush()
            break
        except IntegrityError:
            continue
    else:
        logger.warning("AgentEvent sequence 冲突重试耗尽 run=%s", run.id)
        return
    await session.commit()

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


async def apply_analysis_failure(
    session: AsyncSession, task: Task | None, run: TaskRun | None, error: str
) -> None:
    """编排兜底失败：已取消/归档的终态不得改回 failed。"""
    if not session.is_active:
        await session.rollback()
    if run is not None:
        await session.refresh(run)
        if run.status not in _PROTECTED_TERMINAL_STATUSES:
            await _update_run(session, run, "failed", error[:1000])
            result = await session.execute(
                select(NodeRun).where(NodeRun.run_id == run.id, NodeRun.status == "running")
            )
            now = datetime.now(timezone.utc)
            for nr in result.scalars():
                nr.status = "failed"
                nr.error_message = (nr.error_message or error)[:1000]
                nr.finished_at = now
    if task is not None:
        await session.refresh(task)
        if task.status not in _PROTECTED_TERMINAL_STATUSES:
            task.status = "failed"
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
    logger.warning("收到 SIGTERM，清理 agent-runner 容器")
    try:
        agent_runner_manager.stop_all_active()
    except Exception:  # noqa: BLE001
        pass
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


from celery.signals import worker_ready


@worker_ready.connect
def _on_worker_ready(**_kwargs: object) -> None:
    """worker 起来立刻开巡检线程：启动扫一次，之后每 5 分钟扫孤儿容器。"""
    from app.contexts.agent.runtime_cleanup import start_background_sweeper

    start_background_sweeper()


# ── 最小预检 ──


async def _platform_preflight_minimal(session: AsyncSession) -> tuple[bool, str | None]:
    """最小预检：与创建/重试同一套平台准入（LLM + agent-runner 镜像）。

    不实现 agent-workflow.md §2.2 S0 的完整检查（MinIO / python-docx 等）——
    留作阶段化 backlog。本任务只保证 SDK 跑得起来。
    """
    from app.contexts.agent.preflight import require_platform_ready

    try:
        await require_platform_ready(session)
    except ValueError as e:
        return False, str(e)
    return True, None


# ── Celery 任务 ──


@celery_app.task(bind=True, name="agent.run_analysis", max_retries=None)
def run_analysis(self, task_id: str, run_id: str) -> dict:
    """漏洞分析主任务"""
    return asyncio.run(_run_analysis(task_id, run_id, celery_task=self))


async def _run_analysis(task_id: str, run_id: str, *, celery_task: object) -> dict:
    engine = _worker_engine()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    host_workdir = ""
    container_id: str | None = None
    summary: dict = {"task_id": task_id, "run_id": run_id}
    acquired = False

    try:
        async with session_factory() as session:
            task, run, claim_err, acquired = await admit_task_run(
                session, celery_task, task_id, run_id
            )
            if claim_err:
                summary.update(status=task.status if task else "ignored", error=claim_err)
                return summary
            assert task is not None and run is not None

            # 1. 最小预检
            ok, preflight_err = await _platform_preflight_minimal(session)
            if not ok:
                title, hint = humanize_agent_error(preflight_err)
                detail = format_agent_error(preflight_err)
                await _update_run(session, run, "failed", detail[:1000])
                task.status = "failed"
                await _persist_single_event(
                    session,
                    run,
                    {
                        "type": "agent.failed",
                        "error": preflight_err,
                        "title": title,
                        "hint": hint,
                    },
                )
                await session.commit()
                summary.update(status="failed", error=title)
                return summary

            # 2. host 临时目录（源码由节点 0 获取，按真实仓库名落地）
            host_workdir = agent_runner_manager.host_workdir_path(task_id)
            if await _run_has_completed_nodes(session, run.id):
                os.makedirs(host_workdir, exist_ok=True)
            else:
                reset_host_workdir(host_workdir)
            await _append_events(
                session, run,
                [{"type": "phase.updated", "phase": "preflight", "message": "创建工作区，源码由节点 0 获取",
                  "source": "crucible"}],
            )
            await session.commit()

            # 3. 构造 runner env（凭据从默认 Provider 解密或 settings.llm_* 兜底）
            runner_env = await resolve_runner_env(session)

            # 3.5 注入任务级凭据（P1-6 Credential Proxy）：
            # env_var 凭据合并进 runner_env；file 凭据写 host_workdir/.secrets/（600）
            secret_files: list[dict] = []
            try:
                refs_raw = getattr(task, "credential_refs", None) or "[]"
                refs = json.loads(refs_raw) if refs_raw else []
                if isinstance(refs, list) and refs:
                    from app.contexts.settings.service import SettingsService
                    from app.contexts.settings.repository import SettingsRepository
                    from app.core.credential_proxy import inject_credentials

                    creds = await SettingsService(SettingsRepository(session)).resolve_for_task(
                        task.owner_id, refs
                    )
                    secret_files = inject_credentials(creds, runner_env, host_workdir)
                    if secret_files:
                        await _append_events(
                            session, run,
                            [{"type": "phase.updated", "phase": "credentials",
                              "message": f"已注入 {len(secret_files)} 个凭据（env/file）",
                              "source": "crucible"}],
                        )
                        await session.commit()
            except Exception as e:  # noqa: BLE001 — 凭据注入失败不阻断（任务可无凭据继续）
                logger.warning(f"凭据注入失败（任务继续）: {e}")

            # 4. 6 节点编排(替代旧的单次 executor.run)
            from app.contexts.agent.orchestrator import run_orchestration

            captured: dict = {}
            loop = asyncio.get_running_loop()

            async def _persist_ai_event(event: dict) -> None:
                """独立 session，避免和编排器抢同一条 AsyncSession。"""
                async with session_factory() as ev_session:
                    ev_run = await ev_session.get(TaskRun, run.id)
                    if ev_run is None:
                        return
                    await _persist_single_event(ev_session, ev_run, event)

            def _on_ai_event(event: dict) -> None:
                """AI 节点 JSONL → 落 AgentEvent + Redis。

                docker 线程用 run_coroutine_threadsafe；事件循环线程（env_ready._emit）
                不能 .result() 死锁，改为 create_task。
                """
                try:
                    try:
                        running = asyncio.get_running_loop()
                    except RuntimeError:
                        running = None
                    if running is loop:
                        loop.create_task(_persist_ai_event(event))
                        return
                    fut = asyncio.run_coroutine_threadsafe(_persist_ai_event(event), loop)
                    fut.result(timeout=5)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "AI 事件落库失败(不影响主流程): %s %s",
                        type(e).__name__,
                        e or repr(e),
                    )

            async def _on_node_event(node_key: str, status: str, output) -> None:
                """节点状态变化 → 落 AgentEvent + Redis（SSE 步骤条与事件流共用）。"""
                event = {
                    "type": "node.updated",
                    "node_key": node_key,
                    "status": status,
                    "output": output,
                    "timestamp": datetime.now(timezone.utc).timestamp(),
                }
                if isinstance(output, dict) and status == "failed":
                    event["error"] = output.get("error") or output.get("detail")
                    event["title"] = output.get("error")
                    event["hint"] = output.get("hint")
                try:
                    await _persist_single_event(session, run, event)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"node.updated 落库失败(不影响主流程): {e}")

            try:
                orch_result = await run_orchestration(
                    task_id=task_id,
                    run_id=run_id,
                    session=session,
                    host_workdir=host_workdir,
                    source_path=host_workdir,
                    runner_env=runner_env,
                    on_node_event=_on_node_event,
                    on_ai_event=_on_ai_event,
                )
                captured["result"] = orch_result
            except Exception as e:  # noqa: BLE001
                captured["error"] = e

            # 5. 状态(orchestrator 已设 task/run/verdict;这里补 summary + 报告雏形)
            orch_result = captured.get("result")
            if orch_result is None:
                err = str(captured.get("error", "orchestrator 未返回结果"))
                title, hint = humanize_agent_error(err)
                await apply_analysis_failure(session, task, run, format_agent_error(err))
                if task.status not in _PROTECTED_TERMINAL_STATUSES:
                    await _persist_single_event(
                        session,
                        run,
                        {
                            "type": "agent.failed",
                            "error": err,
                            "title": title,
                            "hint": hint,
                        },
                    )
            else:
                # 节点 5 产出 report_data 时,落结构化 Report(阶段 4 补 docx 导出)
                report_data = orch_result.get("report_data")
                if report_data and task.status in ("completed", "needs_review"):
                    try:
                        cols = report_columns_from_orch_result(orch_result)
                        report = await ensure_report_for_run(
                            session,
                            Report(
                                task_id=task_id, run_id=run_id, owner_id=task.owner_id,
                                status="generated",
                                verdict=cols["verdict"],
                                cvss_score=cols["cvss_score"],
                                severity=cols["severity"],
                                vulnerable_file=cols["vulnerable_file"],
                                report_data=json.dumps(report_data, ensure_ascii=False, default=str),
                                title=f"{cols['title']} — {task_id[:8]}",
                                summary=cols["summary"],
                                poc_language=cols["poc_language"],
                                poc_filename=cols["poc_filename"],
                                poc_code=cols["poc_code"],
                                poc_usage=cols["poc_usage"],
                            ),
                        )
                        await session.commit()
                        # 归档插件产物(VULN-*/img/.vuln-env 等)到 MinIO + Evidence
                        try:
                            archived = await _archive_artifacts(session, report, host_workdir)
                            if archived:
                                summary["artifacts_archived"] = archived
                                await session.commit()
                        except Exception as e:  # noqa: BLE001 — 归档失败不阻断主流程
                            logger.warning(f"插件产物归档失败(不影响主流程): {e}")
                        summary["report_generated"] = True
                    except Exception as e:  # noqa: BLE001
                        summary["report_generated"] = False
                        summary["report_error"] = str(e)

            await session.commit()

            # 6. 发布 domain 事件
            try:
                await event_bus.publish_domain_event(
                    Event(
                        event_type="run.completed",
                        aggregate_id=run_id,
                        aggregate_type="task_run",
                        payload={
                            "task_id": task_id,
                            "run_status": run.status,
                            "verdict": task.verdict,
                        },
                    )
                )
                await event_bus.publish_domain_event(
                    Event(
                        event_type="task.updated",
                        aggregate_id=task_id,
                        aggregate_type="task",
                        payload={"status": task.status, "verdict": task.verdict},
                    )
                )
            except Exception:  # noqa: BLE001 — 事件总线失败不影响主流程
                pass

            summary.update(
                status=task.status,
                run_status=run.status,
                verdict=task.verdict,
                non_web=orch_result.get("non_web") if orch_result else False,
            )

    except Retry:
        raise
    except Exception as e:  # noqa: BLE001 — 兜底：任何异常都要落库并返回
        logger.exception(f"run_analysis 兜底异常: {e}")
        try:
            async with session_factory() as session:
                run = await session.get(TaskRun, run_id)
                task = await session.get(Task, task_id)
                if run:
                    await apply_analysis_failure(session, task, run, format_agent_error(str(e)))
                elif task:
                    await apply_analysis_failure(session, task, None, format_agent_error(str(e)))
                await session.commit()
        except Exception:
            pass
        summary.update(status="failed", error=str(e))
    finally:
        if acquired:
            try:
                await release_slot(run_id)
            except Exception:  # noqa: BLE001
                logger.warning("释放运行槽失败 run=%s", run_id, exc_info=True)
        # 9. 清理容器与 host_workdir（成功/失败都拆容器；失败才留目录排查）
        try:
            from app.contexts.agent.runtime_cleanup import teardown_task_runtime

            await teardown_task_runtime(task_id)
        except Exception:  # noqa: BLE001
            logger.warning("任务结束拆容器失败(best-effort)", exc_info=True)
        if container_id:
            _unregister_container(container_id)
        if should_retain_hostdir(summary.get("status")):
            # 排查价值在源码与节点产物，密钥永远不留（与凭据零落盘承诺一致）
            _purge_secrets_dir(host_workdir)
            logger.warning("任务未成功，保留 host_workdir 供排查: %s", host_workdir)
        else:
            _cleanup_hostdir(host_workdir)
        await engine.dispose()

    return summary


def should_retain_hostdir(status: str | None) -> bool:
    """失败/取消/待复核时保留工作目录，便于对照源码与节点产物。"""
    return status in ("failed", "cancelled", "needs_review")


def _purge_secrets_dir(host_workdir: str) -> None:
    """保留排查目录前删除 .secrets/（明文凭据文件，见 credential_proxy）。

    best-effort：删除失败只记日志不阻断保留逻辑（密钥目录若删不掉，
    排查价值仍大于整体丢弃目录）。
    """
    import shutil as _shutil

    secret_dir = os.path.join(host_workdir, ".secrets")
    if os.path.isdir(secret_dir):
        _shutil.rmtree(secret_dir, ignore_errors=True)


def _cleanup_hostdir(path: str) -> None:
    """删除 host 临时目录（best-effort）"""
    if not path:
        return
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"清理 host_workdir 失败 {path}: {e}")


def reset_host_workdir(path: str) -> None:
    """清空并重建工作区，避免重试吃到上一轮残留的桩目录。"""
    _cleanup_hostdir(path)
    if path:
        os.makedirs(path, exist_ok=True)


async def _run_has_completed_nodes(session: AsyncSession, run_id: str) -> bool:
    result = await session.execute(
        select(NodeRun.id).where(NodeRun.run_id == run_id, NodeRun.status == "completed").limit(1)
    )
    return result.scalar_one_or_none() is not None


def _discover_repo_dir(host_workdir: str) -> str:
    """工作区下真实仓库目录（audit-uuid/<repo_dirname>），兼容旧的 project/。"""
    if not os.path.isdir(host_workdir):
        return os.path.join(host_workdir, "project")
    skip = {".secrets", ".git"}
    named = os.path.join(host_workdir, "project")
    found: list[str] = []
    for entry in sorted(os.listdir(host_workdir)):
        if entry.startswith(".") or entry in skip:
            continue
        path = os.path.join(host_workdir, entry)
        if os.path.isdir(path):
            found.append(path)
    if named in found:
        return named
    if found:
        return found[0]
    return named


# ── 插件产物自动归档（P1：补 P0-4 手动上传的自动补充） ──

# 单文件归档上限（20MB；超大文件跳过，避免 worker 阻塞）
_ARTIFACT_MAX_BYTES = 20 * 1024 * 1024


def _classify_artifact(fname: str) -> str:
    """文件名 → evidence kind（artifact | screenshot | log | poc）"""
    fl = fname.lower()
    if fl.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "screenshot"
    if fl.endswith((".py", ".sh")):
        return "poc"
    if fl.endswith((".json", ".log", ".txt")):
        return "log"
    return "artifact"  # report.md / .docx / .pdf / Dockerfile / compose


def _content_type(fname: str) -> str:
    import mimetypes
    ct, _ = mimetypes.guess_type(fname)
    return ct or "application/octet-stream"


def _scan_artifacts(project_dir: str) -> list[tuple[bytes, str, str, str]]:
    """扫描插件产物，返回 (data, file_name, content_type, kind)。不上传。"""
    found: list[tuple[bytes, str, str, str]] = []
    if not os.path.isdir(project_dir):
        return found

    def _read_one(fpath: str, fname: str) -> None:
        try:
            size = os.path.getsize(fpath)
            if size > _ARTIFACT_MAX_BYTES:
                logger.info(f"跳过超大产物 {fname} ({size}B > {_ARTIFACT_MAX_BYTES}B)")
                return
            with open(fpath, "rb") as f:
                data = f.read()
        except OSError as e:
            logger.warning(f"读取产物失败 {fpath}: {e}")
            return
        found.append((data, fname, _content_type(fname), _classify_artifact(fname)))

    for entry in sorted(os.listdir(project_dir)):
        vuln_dir = os.path.join(project_dir, entry)
        if not (entry.startswith("VULN-") and os.path.isdir(vuln_dir)):
            continue
        for fname in sorted(os.listdir(vuln_dir)):
            fpath = os.path.join(vuln_dir, fname)
            if os.path.isfile(fpath):
                _read_one(fpath, fname)
        img_dir = os.path.join(vuln_dir, "img")
        if os.path.isdir(img_dir):
            for fname in sorted(os.listdir(img_dir)):
                fpath = os.path.join(img_dir, fname)
                if os.path.isfile(fpath):
                    _read_one(fpath, f"img/{fname}")

    env_json = os.path.join(project_dir, ".vuln-env.json")
    if os.path.isfile(env_json):
        _read_one(env_json, ".vuln-env.json")

    env_dir = os.path.join(project_dir, ".vuln-env")
    if os.path.isdir(env_dir):
        for fname in sorted(os.listdir(env_dir)):
            fpath = os.path.join(env_dir, fname)
            if os.path.isfile(fpath):
                _read_one(fpath, f".vuln-env/{fname}")

    return found


async def _archive_artifacts(session: AsyncSession, report: Report, host_workdir: str) -> int:
    """扫描仓库目录的插件产物，经 ReportService 上传并落 Evidence。

    返回归档文件数。失败仅日志（不阻塞主流程）。
    """
    from app.contexts.report.repository import ReportRepository
    from app.contexts.report.service import ReportService

    project_dir = _discover_repo_dir(host_workdir)
    items = await asyncio.to_thread(_scan_artifacts, project_dir)
    if not items:
        return 0
    svc = ReportService(ReportRepository(session))
    uploaded = 0
    for data, fname, content_type, kind in items:
        try:
            evidence, err = await svc.attach_evidence(
                report_id=report.id,
                owner_id=report.owner_id,
                file_name=fname,
                content_type=content_type,
                data=data,
                kind=kind,
                report=report,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"上传产物失败 {fname}: {e}")
            continue
        if evidence is None:
            logger.warning(f"上传产物失败 {fname}: {err}")
            continue
        uploaded += 1
    return uploaded