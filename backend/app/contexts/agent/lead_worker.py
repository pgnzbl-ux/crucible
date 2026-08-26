"""LeadWorker：消费 Redis 终认队列，单线索 audit→reproduce(discovery-spec §4.4)。

同任务并发由平台运行时设置限制；每条 job 禁止拼多线索进同一次 prompt。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contexts.agent.ai_runner import authoritative_verdict
from app.contexts.agent.lead_queue import (
    claim_lead,
    clear_task_queue,
    complete_lead,
    is_drained,
)
from app.contexts.finding.models import AlertGroup, LeadRun
from app.contexts.finding.service import FindingService
from app.core.config import get_settings

logger = logging.getLogger(__name__)

OnEvent = Callable[[dict], None] | Callable[[dict], Awaitable[None]] | None

# 孤儿回收轮次上限：正常一两轮收敛；确定性失败的线索转 failed 后不再是孤儿
_MAX_RECLAIM_ROUNDS = 3


async def _emit(on_event: OnEvent, event: dict) -> None:
    if on_event is None:
        return
    result = on_event(event)
    if asyncio.iscoroutine(result):
        await result


async def _reconcile_group(session: AsyncSession, group_id: str, verdict: str | None) -> None:
    """按 LeadRun 六档回写 AlertGroup（经 finding service）。"""
    group = await session.get(AlertGroup, group_id)
    if group is None:
        return
    svc = FindingService(session)
    if verdict in ("confirmed", "partial"):
        await svc.mark_resolved(group, "confirmed")
    elif verdict == "code_reachable":
        await svc.mark_resolved(group, "code_reachable")
    elif verdict == "false_positive":
        await svc.discard_false_positive(group)
    else:
        # code_smell/not_reproduced/needs_review，以及 None
        await svc.mark_needs_review(group)


def whitebox_only_verdict(audit: dict[str, Any] | None) -> str | None:
    """无靶场时，audit pass 表示代码路径已闭环，但未做动态确认。"""
    if (audit or {}).get("gate_verdict") == "pass":
        return "code_reachable"
    return authoritative_verdict(None, audit)


async def process_one_lead(
    *,
    session: AsyncSession,
    lead_run_id: str,
    host_workdir: str,
    source_path: str,
    runner_env: dict[str, str],
    source: dict[str, Any] | None = None,
    profile: dict[str, Any],
    env_ready: dict[str, Any] | None,
    on_event: OnEvent = None,
    task_id: str | None = None,
    lab_id: str | None = None,
) -> LeadRun:
    """对单条 LeadRun 跑 audit（必）+ reproduce（gate 允许且 env_ready 有靶场时）。

    终认工位单一实现（discovery-spec §5.8）：复用 AuditNode/ReproduceNode +
    typed Input，与 DAG 验证路径同构——容器内 source_path、target_url 宿主机
    IP:port、lab touch 等行为自动对齐，不再各写一份。
    """
    from app.contexts.agent.contracts import AuditInput, ReproduceInput
    from app.contexts.agent.contracts.outputs import (
        DispatchHandoff,
        EnvReadyHandoff,
        ProfileHandoff,
        SourceHandoff,
        audit_for_reproduce,
    )
    from app.contexts.agent.nodes.audit import AuditNode
    from app.contexts.agent.nodes.base import NodeContext
    from app.contexts.agent.nodes.reproduce import ReproduceNode

    lead = await session.get(LeadRun, lead_run_id)
    if lead is None:
        raise RuntimeError(f"LeadRun 不存在: {lead_run_id}")
    lead.status = "running"
    await session.flush()
    await _emit(on_event, {
        "type": "phase.updated",
        "phase": "lead_verify",
        "message": f"终认线索 {lead.queue_position + 1}: {lead.alert_group_id[:8]}",
        "lead_run_id": lead.id,
    })

    def _ctx_on_event(event: dict) -> None:
        """NodeContext 期望同步回调；异步回调挂到当前事件循环（fire-and-forget）。"""
        if on_event is None:
            return
        result = on_event(event)
        if asyncio.iscoroutine(result):
            asyncio.get_running_loop().create_task(result)

    try:
        source_handoff = SourceHandoff.model_validate(source or {})
        ctx = NodeContext(
            task_id=task_id or lead.task_id, run_id=lead.run_id,
            host_workdir=host_workdir, source_path=source_path or host_workdir,
            vulnerability_description="", project_address="", project_ref=None,
            runner_env=runner_env, on_event=_ctx_on_event, db_session=session,
            lab_id=lab_id,
            previous_outputs={
                "source": source or {},
                "profile": profile or {},
                "env_ready": env_ready or {},
            },
        )
        # 线索描述经 DispatchHandoff.lead_description 注入（与 DAG audit 输入同构）
        audit_out = await AuditNode().execute(ctx, AuditInput(
            source=source_handoff,
            profile=ProfileHandoff.model_validate(profile),
            vulnerability_description="",
            dispatch=DispatchHandoff(lead_description=lead.lead_description),
        ))
        lead.audit_output = audit_out
        gate = str(audit_out.get("gate_verdict") or "")
        lead.gate_verdict = gate or None

        repro_out: dict[str, Any] | None = None
        if gate in ("fail", "uncertain"):
            lead.verdict = (
                "false_positive" if gate == "fail" else "needs_review"
            )
            if gate == "uncertain":
                lead.verdict = None  # 任务级 needs_review；组退回人工
        elif env_ready and env_ready.get("target_url"):
            repro_out = await ReproduceNode().execute(ctx, ReproduceInput(
                source=source_handoff,
                env_ready=EnvReadyHandoff.model_validate(env_ready),
                audit=audit_for_reproduce(audit_out),
                vulnerability_description=lead.lead_description,
            ))
            lead.reproduce_output = repro_out
            lead.verdict = authoritative_verdict(repro_out, audit_out)
            # 动态复现中复活靶场：回写共享 env_ready，供后续线索与聚合报告使用
            revived = ctx.updated_handoffs.get("env_ready")
            if revived and isinstance(env_ready, dict):
                env_ready.clear()
                env_ready.update(revived)
        else:
            # 无靶场：LeadWorker 不调用动态复现，仅保留白盒结论。
            lead.verdict = whitebox_only_verdict(audit_out)

        lead.status = "completed"
        basis = "lab" if repro_out is not None else "code_path"
        lead.verification_basis = basis
        await _reconcile_group(session, lead.alert_group_id, lead.verdict)
        from app.contexts.finding.vuln_report import is_vuln_report_verdict

        if is_vuln_report_verdict(lead.verdict):
            group = await session.get(AlertGroup, lead.alert_group_id)
            if group is not None:
                await FindingService(session).attach_vuln_report(
                    group=group, lead=lead, verification_basis=basis,
                )
        await session.flush()
        return lead
    except Exception as e:  # noqa: BLE001
        lead.status = "failed"
        lead.error = str(e)[:8000]
        # 失败转人工（spec §1.3：未审≠误报，网关/runner 失败入 needs_review）
        await _reconcile_group(session, lead.alert_group_id, "needs_review")
        await session.flush()
        logger.exception("LeadRun 失败 %s", lead_run_id)
        raise


async def _reclaim_orphan_leads(session_factory, task_id: str) -> int:
    """孤儿回收：DB queued/running 但不在 Redis 队列的 LeadRun 重新入队；
    并清掉不属于任何未完成 LeadRun 的 inflight 残留（前次进程崩溃遗留）。

    覆盖两类线索丢失：commit 后入队前崩溃（dispatch 侧另有补偿）；claim 后
    进程崩溃导致条目滞留 inflight。终态线索不动。
    """
    from sqlalchemy import select

    from app.contexts.agent.lead_queue import (
        complete_lead,
        inflight_lead_ids,
        queued_lead_ids,
        requeue_lead,
    )
    from app.contexts.finding.models import LeadRun

    async with session_factory() as session:
        rows = (await session.execute(
            select(LeadRun).where(
                LeadRun.task_id == task_id,
                LeadRun.status.in_(("queued", "running")),
            )
        )).scalars().all()
        nonterminal = {
            lr.id: {"lead_run_id": lr.id, "group_id": lr.alert_group_id, "run_id": lr.run_id}
            for lr in rows
        }
    actions = 0
    if nonterminal:
        redis_ids = await queued_lead_ids(task_id)
        for lead_run_id, item in nonterminal.items():
            if lead_run_id in redis_ids:
                continue  # 仍在队列，会被正常消费
            await requeue_lead(task_id, item)  # SREM inflight 残留 + LPUSH 回队
            actions += 1
    # inflight 里已无对应未完成 LeadRun 的条目：无主残留，直接清
    for stale_id in (await inflight_lead_ids(task_id)) - set(nonterminal):
        await complete_lead(task_id, stale_id)
        actions += 1
    return actions


async def _terminalize_unclaimed_leads(
    session_factory, task_id: str, *, reason: str,
) -> int:
    """把已停止消费的线索收敛到可观察终态。

    必须在 worker 全部 join 后调用，此时 queued/running 均已没有合法消费者。
    """
    async with session_factory() as session:
        rows = (await session.execute(
            select(LeadRun).where(
                LeadRun.task_id == task_id,
                LeadRun.status.in_(("queued", "running")),
            )
        )).scalars().all()
        for lead in rows:
            lead.status = "skipped"
            lead.verdict = None
            lead.error = reason
            await _reconcile_group(session, lead.alert_group_id, "needs_review")
        await session.commit()
    await clear_task_queue(task_id)
    return len(rows)


async def drain_lead_queue(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    task_id: str,
    host_workdir: str,
    source_path: str,
    runner_env: dict[str, str],
    source: dict[str, Any] | None = None,
    profile: dict[str, Any],
    env_ready: dict[str, Any] | None,
    on_event: OnEvent = None,
    lab_id: str | None = None,
    allow_reclaim: bool = True,
    concurrency: int | None = None,
) -> list[str]:
    """有限并发消费本任务队列，直到 drained（含孤儿回收）。返回完成的 lead_run_id 列表。

    allow_reclaim=False 用于降级工厂（与主 session 共享单连接的 sqlite 场景）：
    额外 session 的开启/回滚会破坏主 session 事务，只能跳过回收；
    生产路径 tasks.py 恒提供独立会话工厂，回收始终开启。
    """
    # 生产编排显式传 DB 运行时设置；None 仅保留给旧调用方/单测兼容。
    concurrency = max(1, int(
        concurrency if concurrency is not None else get_settings().lead_verify_per_task
    ))
    done_ids: list[str] = []
    lock = asyncio.Lock()
    budget_stop = asyncio.Event()

    async def _worker() -> None:
        from app.contexts.agent.nodes.base import task_run_cancelled

        while True:
            if allow_reclaim:
                # 取消穿透 + token 预算软停：停止领取新线索。仅生产路径
                # (独立会话工厂)检查——降级工厂与主 session 共享单连接，
                # 开额外 session 会破坏主事务（见函数 docstring）
                async with session_factory() as s:
                    if await task_run_cancelled(s, task_id):
                        return
                    from app.contexts.agent.usage_ledger import budget_state

                    exhausted, spent, budget = await budget_state(s, task_id)
                    if exhausted:
                        budget_stop.set()
                        if on_event:
                            on_event({
                                "type": "phase.updated", "phase": "lead_verify",
                                "message": (
                                    f"token 预算耗尽（{spent}/{budget}），"
                                    "停止领取新终认线索"
                                ),
                            })
                        return
            item = await claim_lead(task_id)
            if item is None:
                return
            lead_run_id = item["lead_run_id"]
            async with session_factory() as session:
                try:
                    await process_one_lead(
                        session=session,
                        lead_run_id=lead_run_id,
                        host_workdir=host_workdir,
                        source_path=source_path,
                        runner_env=runner_env,
                        source=source,
                        profile=profile,
                        env_ready=env_ready,
                        on_event=on_event,
                        task_id=task_id,
                        lab_id=lab_id,
                    )
                    await session.commit()
                except Exception:  # noqa: BLE001
                    await session.rollback()
                    async with session_factory() as s2:
                        lead = await s2.get(LeadRun, lead_run_id)
                        if lead and lead.status != "failed":
                            lead.status = "failed"
                            await _reconcile_group(s2, lead.alert_group_id, "needs_review")
                            await s2.commit()
                finally:
                    await complete_lead(task_id, lead_run_id)
                    async with lock:
                        done_ids.append(lead_run_id)

    workers = [asyncio.create_task(_worker()) for _ in range(concurrency)]
    await asyncio.gather(*workers, return_exceptions=True)
    # 队列残留再消费 + 孤儿回收，直到队列与 DB 均无未完成线索（有界轮次防死循环）
    for _ in range(_MAX_RECLAIM_ROUNDS):
        if budget_stop.is_set():
            break
        reclaimed = (
            await _reclaim_orphan_leads(session_factory, task_id) if allow_reclaim else 0
        )
        if reclaimed == 0 and await is_drained(task_id):
            break
        extra = [asyncio.create_task(_worker()) for _ in range(concurrency)]
        await asyncio.gather(*extra, return_exceptions=True)
    if budget_stop.is_set():
        skipped = await _terminalize_unclaimed_leads(
            session_factory, task_id, reason="budget_exhausted",
        )
        if skipped:
            await _emit(on_event, {
                "type": "phase.updated",
                "phase": "lead_verify",
                "message": f"预算耗尽，{skipped} 条未终认线索已转人工复核",
            })
    return done_ids


def build_discovery_report_from_leads(
    leads: list[LeadRun],
    *,
    denoise: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """聚合审计报告；只有 confirmed/partial 进入已确认漏洞清单。"""
    from collections import Counter

    from app.contexts.agent.ai_runner import REPORT_SECTION_KEYS

    confirmed = [lr for lr in leads if lr.verdict in ("confirmed", "partial")]
    reachable = [lr for lr in leads if lr.verdict == "code_reachable"]
    verdict_counts = Counter((lr.verdict or "needs_review") for lr in leads)

    findings_md = []
    for i, lr in enumerate(confirmed, 1):
        audit = lr.audit_output or {}
        repro = lr.reproduce_output or {}
        findings_md.append(
            f"### 发现 {i}（{lr.verdict}）\n\n"
            f"{lr.lead_description}\n\n"
            f"**Kill chain**: {audit.get('kill_chain') or '—'}\n\n"
            f"**复现**: {repro.get('verdict') or lr.verdict}\n"
        )
    for i, lr in enumerate(reachable, 1):
        audit = lr.audit_output or {}
        findings_md.append(
            f"### 代码可达 {i}\n\n"
            f"{lr.lead_description}\n\n"
            f"**Kill chain**: {audit.get('kill_chain') or '—'}\n\n"
            "靶场未就绪，仅白盒结论：代码路径可达。\n"
        )
    body = "\n".join(findings_md) or "本轮终认未确认可进入漏洞清单的发现。"
    n = len(confirmed)
    reachable_n = len(reachable)
    total = len(leads)
    needs_review = verdict_counts["needs_review"] + verdict_counts["code_smell"] + verdict_counts["not_reproduced"]
    denoise = denoise or {}
    dropped_c = int(denoise.get("dropped_c_count") or 0)
    finding_count = denoise.get("finding_count")
    group_count = denoise.get("group_count")
    funnel_bits = []
    if finding_count is not None:
        funnel_bits.append(f"原始告警 {finding_count}")
    if dropped_c:
        funnel_bits.append(f"C 档降噪 {dropped_c}")
    if group_count is not None:
        funnel_bits.append(f"复核组 {group_count}")
    funnel_line = ("；".join(funnel_bits) + "。") if funnel_bits else ""
    extra_intro = f" 代码可达 {reachable_n} 条。" if reachable_n else ""
    report_data = {
        "document_kind": "code_audit_report",
        "product_intro": (
            f"仓库代码审计报告：终认 {total} 条线索，确认 {n} 条漏洞。"
            + extra_intro
            + (f" 降噪漏斗：{funnel_line}" if funnel_line else "")
        ),
        "vulnerability": body,
        "impact": "见各确认发现描述与 kill chain；未确认项不作为漏洞结论。" if n else "本轮没有已确认漏洞影响项。",
        "details": body,
        "reproduction": "见各发现复现结论与 LeadRun 输出。",
        "poc_commands": "见已确认 LeadRun 的 reproduce.poc（若有）。",
        "fix_suggestions": "按严重度优先修复已确认发现。" if n else "保持依赖与规则库更新，并复核未充分终认的高价值线索。",
        "reporting_decision": (
            f"本轮确认 {n} / 终认执行 {total} 条；代码可达 {reachable_n}；{needs_review} 条未形成确认结论。"
            if n or reachable_n else f"本轮终认执行 {total} 条，未确认漏洞；{needs_review} 条未形成确认结论。"
        ),
        "audit_summary": {
            "lead_count": total,
            "confirmed_count": n,
            "code_reachable_count": reachable_n,
            "needs_review_count": needs_review,
            "verdict_counts": dict(verdict_counts),
            "denoise_funnel": {
                "finding_count": finding_count,
                "dropped_c_count": dropped_c,
                "dropped_c_by_engine": denoise.get("dropped_c_by_engine") or {},
                "group_count": group_count,
                "bypass_count": denoise.get("bypass_count"),
            },
        },
    }
    for key in REPORT_SECTION_KEYS:
        report_data.setdefault(key, "—")
    final = (
        "confirmed" if any(lr.verdict == "confirmed" for lr in confirmed)
        else "partial" if confirmed else None
    )
    first = confirmed[0] if confirmed else None
    repro0 = (first.reproduce_output or {}) if first else {}
    return {
        "report_data": report_data,
        "final_verdict": final,
        "vulnerable_file": repro0.get("vulnerable_file"),
        "cvss": repro0.get("cvss"),
        "poc": repro0.get("poc"),
        "empty_aggregate": not confirmed,
        "authored_by": "discovery_aggregate",
    }


async def load_lead_runs(session: AsyncSession, run_id: str) -> list[LeadRun]:
    rows = await session.execute(
        select(LeadRun).where(LeadRun.run_id == run_id).order_by(LeadRun.queue_position.asc())
    )
    return list(rows.scalars().all())
