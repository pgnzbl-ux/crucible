"""lead_verify 节点 — discovery 多线索终认的显式编排入口。

discovery 子图不含单例 audit/reproduce；本节点由编排器调度，调用
LeadWorker 逐条执行 audit→reproduce（落 LeadNodeRun），禁止再藏在 report 前的旁路。
"""
from __future__ import annotations

from typing import Any

from .base import NodeContext, emit_phase


class LeadVerifyNode:
    node_key = "lead_verify"

    @property
    def is_ai(self) -> bool:
        # 自身不起容器；下游 LeadRun 的 audit/reproduce 才是 AI 工位
        return False

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from collections import Counter

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.contexts.agent.lead_worker import drain_lead_queue, load_lead_runs
        from app.contexts.settings.repository import SettingsRepository
        from app.contexts.settings.service import SettingsService

        prev = ctx.previous_outputs or {}
        profile = prev.get("profile") or {}
        env_ready = prev.get("env_ready") or None
        if not env_ready:
            env_ready = None

        factory = ctx.session_factory
        allow_reclaim = factory is not None
        if factory is None:
            if ctx.db_session is None or ctx.db_session.bind is None:
                raise RuntimeError("lead_verify 需要 session_factory 或可用 db_session")
            factory = async_sessionmaker(
                ctx.db_session.bind, class_=AsyncSession, expire_on_commit=False,
            )

        async with factory() as s:
            runtime = await SettingsService(SettingsRepository(s)).get_runtime_settings()

        emit_phase(ctx, "开始逐线索终认（LeadWorker）", phase=self.node_key)
        done_ids = await drain_lead_queue(
            session_factory=factory,
            task_id=ctx.task_id,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
            runner_env=ctx.runner_env or {},
            source=prev.get("source"),
            profile=profile,
            env_ready=env_ready if isinstance(env_ready, dict) else None,
            on_event=ctx.on_event,
            lab_id=ctx.lab_id,
            allow_reclaim=allow_reclaim,
            concurrency=runtime.lead_verify_per_task,
            time_budget_deadline=ctx.budget_deadline,
        )

        # Lead 路径可能回写 env_ready；供后续 finalize/report 与其它线索可见
        if isinstance(env_ready, dict) and env_ready.get("target_url"):
            ctx.updated_handoffs["env_ready"] = dict(env_ready)
            ctx.previous_outputs["env_ready"] = dict(env_ready)

        async with factory() as s:
            leads = await load_lead_runs(s, ctx.run_id)
        status_counts = Counter(lead.status for lead in leads)
        verdict_counts = Counter((lead.verdict or "needs_review") for lead in leads)
        failed = int(status_counts.get("failed", 0))
        output: dict[str, Any] = {
            "lead_count": len(leads),
            "completed_lead_ids": list(done_ids),
            "lead_status_counts": dict(status_counts),
            "verdict_counts": dict(verdict_counts),
            "completed_count": int(status_counts.get("completed", 0)),
            "failed_count": failed,
            "skipped_count": int(status_counts.get("skipped", 0)),
            "queued_count": int(status_counts.get("queued", 0))
            + int(status_counts.get("running", 0)),
        }
        if failed:
            output["status"] = "failed"
            output["ok"] = False
        else:
            output["ok"] = True
        emit_phase(
            ctx,
            f"终认完成：{output['completed_count']} 完成 / {failed} 失败 / "
            f"{output['skipped_count']} 跳过（共 {len(leads)}）",
            phase=self.node_key,
        )
        return output
