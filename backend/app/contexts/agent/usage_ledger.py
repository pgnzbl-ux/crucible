"""任务 token 消耗台账 — 预算控制与成本可视化。

每个 agent / 快模型会话一行（agent_usage 表）。写入方持各自 session；
预算检查是廉价聚合查询（SUM + 平台单例设置），供 triage 代表审议、
lead 领取等"反复起会话"的循环在每次开工前自查。

预算语义：软停 —— 耗尽后不再开新 agent 会话，已判决/已验证的结果
保留，未审组经既有兜底转 needs_review，任务照常收尾（不硬杀）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def record_usage(
    session: AsyncSession, *, task_id: str, run_id: str | None,
    node_key: str, usage: dict[str, Any] | None, source: str | None = None,
) -> None:
    """记一行消耗。usage 缺失/为 0 也记录（会话次数本身是成本信号）。"""
    from app.contexts.task.models import AgentUsage

    if not task_id:
        return
    prompt = int((usage or {}).get("prompt_tokens") or 0)
    completion = int((usage or {}).get("completion_tokens") or 0)
    session.add(AgentUsage(
        task_id=task_id, run_id=run_id, node_key=node_key, source=source,
        prompt_tokens=prompt, completion_tokens=completion,
    ))


async def task_usage_summary(session: AsyncSession, task_id: str) -> dict[str, int]:
    from app.contexts.task.models import AgentUsage

    prompt, completion = (await session.execute(
        select(
            func.coalesce(func.sum(AgentUsage.prompt_tokens), 0),
            func.coalesce(func.sum(AgentUsage.completion_tokens), 0),
        ).where(AgentUsage.task_id == task_id)
    )).one()
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_tokens": int(prompt or 0) + int(completion or 0),
        "sessions": int((await session.execute(
            select(func.count(AgentUsage.id)).where(AgentUsage.task_id == task_id)
        )).scalar() or 0),
    }


async def budget_state(
    session: AsyncSession, task_id: str,
) -> tuple[bool, int, int]:
    """返回 (是否耗尽, 已消耗, 预算)。预算 0/未配置 = 不限。"""
    from app.contexts.settings.models import PlatformSetting

    budget = int((await session.execute(
        select(PlatformSetting.task_token_budget)
        .where(PlatformSetting.singleton_key == "default")
    )).scalar() or 0)
    if budget <= 0:
        # 未配置预算：连消耗都不必聚合，检查零成本
        return False, 0, 0
    spent = (await task_usage_summary(session, task_id))["total_tokens"]
    return spent >= budget, spent, budget


async def record_node_usage(
    ctx, node_key: str, meta: dict | None, *, source: str | None = "agent",
) -> None:
    """节点侧记账：从 NodeContext 取会话与标识。best-effort，不阻断节点。"""
    import logging

    try:
        session = getattr(ctx, "db_session", None)
        if session is None:
            return
        await record_usage(
            session,
            task_id=getattr(ctx, "task_id", "") or "",
            run_id=getattr(ctx, "run_id", None),
            node_key=node_key,
            usage=(meta or {}).get("usage"),
            source=source,
        )
    except Exception:  # noqa: BLE001 — 记账失败不影响节点
        logging.getLogger(__name__).warning(
            "usage 记账失败 node=%s", node_key, exc_info=True,
        )
