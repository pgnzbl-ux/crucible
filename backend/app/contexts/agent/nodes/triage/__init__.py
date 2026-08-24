"""triage 节点 — 分级收敛二审。

管线：跳过 F/降权 → T0 携带 → T1 规则前置 → T2 快模型首审 →
T3 同根因族代表并行 agent 审议 + 族内传播。每层判决落 Adjudication
审计行并标记 verdict_source；取消信号在层间与每个代表前穿透。
triage_cascade_enabled=False 时回退为逐组全价 agent 串行审议（旧路径）。

流式派单（triage_stream_dispatch_enabled）：达终认门槛(tp+高置信+A 级+web)
的组在 triage 过程中立即建 LeadRun 入队并后台并发排空——不等整个节点
跑完，首批确认漏洞的到达时间大幅提前；dispatch 节点幂等兼容只做收尾。
Skill 经 -v 挂入 /node-skill，不进 base 镜像。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from sqlalchemy import select

from ..base import NodeContext, emit_phase, task_run_cancelled
from . import cascade
from .adjudicate import adjudicate_group
from .queue import order_groups, should_skip_llm


class _LeadStreamer:
    """triage 流式派单：达门槛的组判完即入终认队列，后台并发排空。

    门槛与 dispatch 节点完全一致(tp + confidence≥high + grade A + is_web)；
    poll() 以库内最新状态为准，天然幂等(dispatched/resolved 组不会重复建)。
    """

    def __init__(self, ctx: NodeContext, settings):
        self.ctx = ctx
        self.settings = settings
        self.high = float(getattr(settings, "triage_high_confidence", 0.8))
        profile = (ctx.previous_outputs or {}).get("profile") or {}
        self.is_web = profile.get("is_web") is True
        self.drain_task: asyncio.Task | None = None
        self.enqueued = 0

    def _qualified(self, g) -> bool:
        return (
            g.ai_verdict == "tp"
            and float(g.ai_confidence or 0) >= self.high
            and (g.clue_grade or "B") == "A"
            and self.is_web
            and g.status == "adjudicated"
        )

    async def poll(self) -> int:
        """库内全量扫一遍达门槛未派发的组建 LeadRun 入队（幂等）。"""
        from app.contexts.finding.models import AlertGroup

        rows = (await self.ctx.db_session.execute(
            select(AlertGroup).where(
                AlertGroup.task_id == self.ctx.task_id,
                AlertGroup.status == "adjudicated",
                AlertGroup.ai_verdict == "tp",
            ).order_by(AlertGroup.ai_confidence.desc()).execution_options(
                populate_existing=True,
            )
        )).scalars().all()
        return await self._enqueue([g for g in rows if self._qualified(g)])

    async def offer(self, groups: list) -> int:
        """对刚判完的组（内存态已刷新）做入队判断，避免全量重查。"""
        return await self._enqueue([g for g in groups if self._qualified(g)])

    async def _enqueue(self, qualified: list) -> int:
        if not qualified:
            return 0
        from app.contexts.agent.lead_queue import enqueue_leads
        from app.contexts.agent.nodes.dispatch import _get_or_create_lead_run
        from app.contexts.finding.hypothesis import build_lead_description
        from app.contexts.finding.service import FindingService

        ctx = self.ctx
        svc = FindingService(ctx.db_session)
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for pos, g in enumerate(qualified):
            if g.id in seen:
                continue  # members+代表 可能同组出现
            seen.add(g.id)
            rep = await svc.representative_of(g)
            adj = await svc.latest_adjudication(g.id)
            lead_run = await _get_or_create_lead_run(
                ctx.db_session,
                task_id=ctx.task_id, run_id=ctx.run_id,
                alert_group_id=g.id, queue_position=pos,
                description=build_lead_description(
                    group=g, representative=rep, adjudication=adj,
                ),
            )
            if lead_run.status != "queued":
                continue  # 已在队列/处理中，不重复入队
            await svc.mark_dispatched(g)
            items.append({
                "lead_run_id": lead_run.id,
                "group_id": g.id,
                "run_id": ctx.run_id,
            })
        if not items:
            return 0
        # 溯源指针与 dispatch 节点同语义：首个入队线索回写 source_alert_group
        # （全流式路径下 dispatch 不再新建线索，指针只能在这里落；
        # 仅在为空时写，避免后续批次来回改写）
        from app.contexts.task.models import Task as _TaskModel
        from app.contexts.task.repository import TaskRepository
        from app.contexts.task.service import TaskService

        task_row = await ctx.db_session.get(_TaskModel, ctx.task_id)
        if task_row is not None and not getattr(task_row, "source_alert_group_id", None):
            await TaskService(TaskRepository(ctx.db_session)).set_source_alert_group(
                ctx.task_id, items[0]["group_id"],
            )
        await ctx.db_session.commit()
        try:
            pushed = await enqueue_leads(ctx.task_id, items)
        except Exception:
            # Redis 不可达：回退本批 dispatched 标记，让下轮 poll/重跑
            # 能重新入队（否则组停在 dispatched 而队列无条目，永久悬挂）
            from sqlalchemy import update as _update

            from app.contexts.finding.models import AlertGroup

            await ctx.db_session.execute(
                _update(AlertGroup)
                .where(AlertGroup.id.in_([i["group_id"] for i in items]))
                .values(status="adjudicated")
            )
            await ctx.db_session.commit()
            emit_phase(ctx, "终认队列入队失败，本轮流式派单回退", phase="triage")
            return 0
        # pushed<len 是去重命中（本就在队列）；真丢条目由回收路径补偿
        self.enqueued += pushed or len(items)
        emit_phase(
            ctx, f"流式入队 {len(items)} 条终认线索（累计 {self.enqueued}）",
            phase="triage",
        )
        self._ensure_drain()
        return len(items)

    def _ensure_drain(self) -> None:
        """后台排空终认队列（与 triage 剩余审议并发）。

        首个 drain 把队列排空退出后，后续入队要能重新拉起——否则
        传播阶段的新线索只能等 dispatch 后的正式排空，流式失去意义。
        """
        if (
            self.drain_task is not None
            and not self.drain_task.done()
        ) or self.ctx.session_factory is None:
            return
        from app.contexts.agent.lead_worker import drain_lead_queue

        prev = self.ctx.previous_outputs or {}
        ctx = self.ctx

        async def _drain():
            from app.contexts.settings.repository import SettingsRepository
            from app.contexts.settings.service import SettingsService

            # 后台任务不得碰主会话：运行时设置用独立会话读取
            async with ctx.session_factory() as s:
                runtime = await SettingsService(
                    SettingsRepository(s)
                ).get_runtime_settings()
            await drain_lead_queue(
                session_factory=ctx.session_factory,
                task_id=ctx.task_id,
                host_workdir=ctx.host_workdir,
                source_path=ctx.source_path,
                runner_env=ctx.runner_env or {},
                source=prev.get("source"),
                profile=prev.get("profile") or {},
                env_ready=prev.get("env_ready") or None,
                on_event=ctx.on_event,
                lab_id=ctx.lab_id,
                concurrency=runtime.lead_verify_per_task,
            )

        self.drain_task = asyncio.create_task(_drain())

    async def join(self) -> None:
        """triage 收尾：等待流式验证完成（重叠执行后的残余等待）。

        drain 失败不让 triage 节点失败——dispatch 节点之后的正式排空会重试。
        """
        import logging

        if self.drain_task is not None:
            try:
                await self.drain_task
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "流式排空失败(交由 dispatch 后排空重试): %s", e,
                )

    def cancel(self) -> None:
        if self.drain_task is not None and not self.drain_task.done():
            self.drain_task.cancel()

    async def cancel_and_reap(self) -> None:
        """取消并收尸：detached 任务会在编排器返回后短暂残留（engine 已
        dispose，只会产生日志噪音），这里等它真正退出。"""
        self.cancel()
        if self.drain_task is not None:
            try:
                await self.drain_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


class TriageNode:
    node_key = "triage"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "triage",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from app.contexts.finding.models import RawFinding
        from app.contexts.finding.service import FindingService
        from app.core.config import get_settings

        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp
        settings = get_settings()
        svc = FindingService(ctx.db_session)

        groups = await svc.list_groups(ctx.task_id, status="clustered")
        rep_ids = [g.representative_finding_id for g in groups if g.representative_finding_id]
        sev_map: dict[str, str] = {}
        if rep_ids:
            rows = (await ctx.db_session.execute(
                select(RawFinding.id, RawFinding.severity).where(RawFinding.id.in_(rep_ids))
            )).all()
            sev_map = {rid: (sev or "") for rid, sev in rows}

        ordered = order_groups(groups, severity_of=lambda g: sev_map.get(g.representative_finding_id, ""))
        queue = [g for g in ordered if not should_skip_llm(g)]
        skipped_llm = [g for g in groups if should_skip_llm(g)]
        for g in skipped_llm:
            await svc.mark_needs_review(g)

        candidate_ids = [g.id for g in queue]
        stats = cascade.TierStats()
        emit_phase(
            ctx,
            f"待审 {len(queue)} 组（跳过 LLM {len(skipped_llm)}）",
            phase=self.node_key,
        )

        streamer = (
            _LeadStreamer(ctx, settings)
            if getattr(settings, "triage_stream_dispatch_enabled", True)
            else None
        )
        try:
            cancelled = await self._run_pipeline(
                ctx, svc, queue, settings, stats, streamer=streamer,
            )
        except BaseException:
            if streamer is not None:
                await streamer.cancel_and_reap()
            raise

        if cancelled:
            if streamer is not None:
                await streamer.cancel_and_reap()
            # 已完成的判决保留；未审的保持 clustered 原状，不扫成 needs_review。
            # 编排器在 execute 返回后会复查取消并把 NodeRun 标为 cancelled。
            await ctx.db_session.commit()
            return self._output(stats, len(skipped_llm), 0, cancelled=True)

        if streamer is not None:
            await streamer.poll()  # 收尾兜底：快审等来源的达门槛组
            if streamer.enqueued:
                # join 前显式分段：triage 的 NodeRun 会等流式终认排空才完成，
                # 不播报的话事件流又回到"看不懂它在干什么"
                emit_phase(
                    ctx,
                    f"二审完成，等待 {streamer.enqueued} 条流式终认线索排空"
                    "（验证与报告随后就绪）",
                    phase=self.node_key,
                )
            await streamer.join()
        await ctx.db_session.commit()
        # 兜底：仍 clustered 的组转复核（正常路径应为空）
        leftover = await svc.mark_unaudited_for_review(ctx.task_id)
        await ctx.db_session.commit()
        verdict_counts = await self._verdict_counts(ctx, candidate_ids)
        emit_phase(ctx, f"完成：{stats.summary()} · 跳过 {len(skipped_llm)} · 残留 {leftover}", phase=self.node_key)
        return {
            **self._output(stats, len(skipped_llm), leftover, cancelled=False),
            **verdict_counts,
        }

    # ── 管线 ─────────────────────────────────────────────

    async def _run_pipeline(
        self, ctx: NodeContext, svc, queue: list, settings, stats: cascade.TierStats,
        *, streamer: _LeadStreamer | None = None,
    ) -> bool:
        """跑完级联。返回 True 表示中途取消。"""
        from app.contexts.finding.models import AlertGroup

        if not getattr(settings, "triage_cascade_enabled", False):
            return await self._adjudicate_all_serial(ctx, queue, settings, stats)

        cancelled = await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id)
        if cancelled:
            return True

        # T0 同项目同指纹携带
        queue, stats.carried = await cascade.apply_carryover(
            svc, groups=queue, project_id=ctx.project_id, settings=settings,
        )
        await ctx.db_session.commit()

        # T1 规则历史 FP 率前置
        queue, stats.rule = await cascade.apply_rule_preverdict(
            svc, groups=queue, settings=settings,
        )
        await ctx.db_session.commit()

        # T2 快模型首审
        if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
            return True
        queue, stats.fast = await cascade.fast_screen(
            ctx, svc, groups=queue, settings=settings,
        )
        await ctx.db_session.commit()
        stats.escalated = len(queue)
        if streamer is not None:
            await streamer.poll()  # 快审定案的达门槛组立即入队

        # T3 同根因族：代表并行亲审，族内传播
        rule_of, _ = await cascade.rule_by_rep(
            ctx.db_session, [g.representative_finding_id for g in queue]
        )
        if getattr(settings, "triage_family_enabled", True):
            families = [f for f in cascade.group_families(queue, rule_of) if f.members]
        else:
            families = [
                cascade.Family(key=g.group_key, members=[g]) for g in queue
            ]
        stats.families = len(families)
        emit_phase(
            ctx,
            f"级联收敛：{stats.summary()} · 升级 {len(queue)} 组 / {len(families)} 族",
            phase=self.node_key,
        )
        if await self._adjudicate_representatives(ctx, families, settings, stats):
            return True

        # 传播准备：family_key 目前只在内存对象上，先 flush 落库；再用
        # populate_existing 定向重载全部候选组——代表是在并行子会话更新
        # 的，主会话身份映射中的对象是陈旧的（get/select 命中缓存返回旧值，
        # 不能用 expire_all：异步 ORM 的懒加载会直接炸）
        await ctx.db_session.flush()
        await ctx.db_session.execute(
            select(AlertGroup)
            .where(AlertGroup.id.in_([g.id for g in queue]))
            .execution_options(populate_existing=True)
        )

        # 传播折扣按历史验证一致率自校准（验证真值回流）
        factor = await cascade.calibrated_propagate_factor(
            ctx.db_session,
            default_factor=float(getattr(settings, "triage_propagate_confidence_factor", 0.85)),
            min_verified=int(getattr(settings, "triage_feedback_min_verified", 10)),
            project_id=ctx.project_id,
        )

        # 传播（expire_all 后 get/select 都会重载最新库值）；
        # 每族判完即流式派单，验证与剩余审议并发
        for family in families:
            rep = await ctx.db_session.get(AlertGroup, family.representative.id)
            if rep is None or rep.status != "adjudicated":
                continue
            propagated, review = await cascade.propagate_family_verdicts(
                svc, family=family, rep=rep, settings=settings, factor=factor,
            )
            stats.propagated += propagated
            stats.propagated_review += review
            if streamer is not None:
                await streamer.offer(family.members + [rep])
        await ctx.db_session.commit()
        return False

    async def _adjudicate_all_serial(
        self, ctx: NodeContext, queue: list, settings, stats: cascade.TierStats,
    ) -> bool:
        """旧路径回退：逐组全价 agent，串行。返回 True 表示取消。"""
        for i, group in enumerate(queue):
            if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
                emit_phase(ctx, "任务已取消，中止二审", phase=self.node_key)
                return True
            from app.contexts.agent.usage_ledger import budget_state

            exhausted, spent, budget = await budget_state(ctx.db_session, ctx.task_id)
            if exhausted:
                stats.budget_exhausted = True
                emit_phase(
                    ctx,
                    f"token 预算耗尽（{spent}/{budget}），停止剩余 agent 审议，未审组转人工",
                    phase=self.node_key,
                )
                return False
            label = f"{group.cwe or '?'} {group.file_path or ''}".strip()
            emit_phase(ctx, f"二审 {i + 1}/{len(queue)}：{label}", phase=self.node_key)
            if await adjudicate_group(ctx, group, settings):
                stats.agent += 1
                await self._emit_progress(ctx, stats, pending=len(queue) - i - 1)
            await ctx.db_session.commit()
        return False

    async def _adjudicate_representatives(
        self, ctx: NodeContext, families: list, settings, stats: cascade.TierStats,
    ) -> bool:
        """族代表并行全价 agent 审议。返回 True 表示取消。

        有 session_factory 时每个代表独立会话（并行安全）；降级路径退回
        串行共用主会话。单组瞬时 AgentRunnerError 由 adjudicate_group 降级为
        needs_review；平台级 LLM API 失败（余额不足等）中止整节点。
        """
        from app.contexts.agent.llm_errors import is_llm_api_failure
        from app.contexts.finding.models import AlertGroup
        from app.core.agent_runner import AgentRunnerError

        factory = getattr(ctx, "session_factory", None)
        concurrency = max(1, int(getattr(settings, "triage_concurrency", 4) or 1))
        if factory is None:
            return await self._adjudicate_all_serial(
                ctx, [f.representative for f in families], settings, stats,
            )

        sem = asyncio.Semaphore(concurrency)
        done = 0
        started = 0
        total = len(families)
        llm_stop = asyncio.Event()
        llm_fatal: list[str] = []
        emit_phase(
            ctx,
            f"族级代表审议启动：{total} 族 · 并发 {concurrency}"
            f"（每族只审 1 个代表，其余成员传播判决）",
            phase=self.node_key,
        )

        async def _one(family) -> str:
            nonlocal done, started
            async with sem:
                if llm_stop.is_set():
                    return "llm_skip"
                rep_gid = family.representative.id
                label = f"{family.representative.cwe or '?'} {family.representative.file_path or ''}".strip()
                async with factory() as ws:
                    # 取消/预算自查必须在 worker 自己的会话上做——
                    # 并发协程共享主会话会 greenlet 互锁并滞留事务锁
                    if await task_run_cancelled(ws, ctx.task_id, ctx.run_id):
                        return "cancelled"
                    from app.contexts.agent.usage_ledger import budget_state

                    exhausted, spent, budget = await budget_state(ws, ctx.task_id)
                    if exhausted:
                        return "budget"
                    if llm_stop.is_set():
                        return "llm_skip"
                    group = await ws.get(AlertGroup, rep_gid)
                    if group is None:
                        return "missing"
                    # 领号即递增：并发下每个代表拿到唯一序号
                    started += 1
                    emit_phase(
                        ctx,
                        f"二审 {started}/{total}：{label}（族内 {len(family.members)} 组）",
                        phase=self.node_key,
                    )
                    try:
                        ok = await adjudicate_group(
                            replace(ctx, db_session=ws), group, settings,
                        )
                    except asyncio.CancelledError:
                        raise
                    except AgentRunnerError as e:
                        if is_llm_api_failure(str(e)):
                            llm_fatal.append(str(e))
                            llm_stop.set()
                            emit_phase(
                                ctx, f"LLM 调用失败，中止二审：{label}",
                                phase=self.node_key,
                            )
                            return "llm_fatal"
                        emit_phase(ctx, f"二审异常转人工：{label}", phase=self.node_key)
                        from app.contexts.finding.service import FindingService

                        await FindingService(ws).mark_needs_review(group)
                        ok = False
                    except Exception as e:  # noqa: BLE001
                        # 不让单个代表的异常炸掉 gather（兄弟协程会变孤儿
                        # 继续写库）——瞬时错误降级转人工；LLM API 失败中止
                        import logging

                        if is_llm_api_failure(str(e)):
                            llm_fatal.append(str(e))
                            llm_stop.set()
                            emit_phase(
                                ctx, f"LLM 调用失败，中止二审：{label}",
                                phase=self.node_key,
                            )
                            return "llm_fatal"
                        logging.getLogger(__name__).warning(
                            "代表审议异常转人工 %s: %s", label, e, exc_info=True,
                        )
                        emit_phase(ctx, f"二审异常转人工：{label}", phase=self.node_key)
                        from app.contexts.finding.service import FindingService

                        await FindingService(ws).mark_needs_review(group)
                        ok = False
                    await ws.commit()
                if ok:
                    stats.agent += 1
                done += 1
                await self._emit_progress(ctx, stats, pending=total - done)
                return "ok"

        results = await asyncio.gather(*[_one(f) for f in families])
        if any(r == "cancelled" for r in results):
            emit_phase(ctx, "任务已取消，中止二审", phase=self.node_key)
            return True
        if any(r == "llm_fatal" for r in results):
            raise AgentRunnerError(
                llm_fatal[0] if llm_fatal else "AI 节点 triage LLM 调用失败"
            )
        if any(r == "budget" for r in results):
            from app.contexts.agent.usage_ledger import budget_state

            _ex, spent, budget = await budget_state(ctx.db_session, ctx.task_id)
            stats.budget_exhausted = _ex
            emit_phase(
                ctx,
                f"token 预算耗尽（{spent}/{budget}），停止剩余 agent 审议，未审组转人工",
                phase=self.node_key,
            )
            # 前端 TaskEventTimeline 以 triage.progress.reason==='budget'
            # 渲染预算中断标记
            if ctx.on_event:
                ctx.on_event({
                    "type": "triage.progress",
                    "reason": "budget",
                    "spent": spent,
                    "budget": budget,
                })
        return False

    # ── 输出/进度 ────────────────────────────────────────

    async def _verdict_counts(self, ctx: NodeContext, candidate_ids: list[str]) -> dict[str, int]:
        """本次候选组的终判分布（含所有级联来源），兼容旧 output 键。"""
        from app.contexts.finding.models import AlertGroup

        counts = {"tp_count": 0, "fp_count": 0, "need_more_count": 0}
        if not candidate_ids:
            return counts
        rows = (await ctx.db_session.execute(
            select(AlertGroup.ai_verdict).where(AlertGroup.id.in_(candidate_ids))
        )).scalars().all()
        for verdict in rows:
            if verdict == "tp":
                counts["tp_count"] += 1
            elif verdict == "fp":
                counts["fp_count"] += 1
            elif verdict == "need_more_context":
                counts["need_more_count"] += 1
        return counts

    def _output(
        self, stats: cascade.TierStats, skipped_llm: int, leftover: int, *, cancelled: bool,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "adjudicated_count": stats.agent,
            "skipped_llm_count": skipped_llm,
            "skipped_unaudited_count": leftover,
            "carried_count": stats.carried,
            "rule_count": stats.rule,
            "fast_model_count": stats.fast,
            "propagated_count": stats.propagated,
            "propagated_review_count": stats.propagated_review,
            "family_count": stats.families,
            "escalated_count": stats.escalated,
            "budget_exhausted": stats.budget_exhausted,
        }
        if cancelled:
            out["status"] = "cancelled"
        return out

    async def _emit_progress(
        self, ctx: NodeContext, stats: cascade.TierStats, *, pending: int,
    ) -> None:
        if not ctx.on_event:
            return
        ctx.on_event({
            "type": "triage.progress",
            "adjudicated": stats.agent,
            "pending": pending,
            "tiers": {
                "carried": stats.carried,
                "rule": stats.rule,
                "fast_model": stats.fast,
                "agent": stats.agent,
                "propagated": stats.propagated,
            },
        })
