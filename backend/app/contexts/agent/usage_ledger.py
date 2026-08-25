"""任务 token 消耗台账 — 预算控制与成本可视化。

裁决：Claude Agent SDK 官方用量说明优先（ResultMessage.usage /
model_usage）；Messages usage 仅解释键名。只认回传字段，禁止自算 cache。
详见 docs/token-usage-accounting.md。

每个 agent / 快模型会话一行（agent_usage 表）。写入方持各自 session；
预算检查是廉价聚合查询（SUM + 平台单例设置），供 triage 代表审议、
lead 领取等"反复起会话"的循环在每次开工前自查。

预算语义：软停 —— 耗尽后不再开新 agent 会话，已判决/已验证的结果
保留，未审组经既有兜底转 needs_review，任务照常收尾（不硬杀）。
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


def _tok(raw: dict[str, Any], *keys: str) -> int:
    for key in keys:
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return 0


def _sum_model_usage(model_usage: Any) -> dict[str, int] | None:
    """ResultMessage.model_usage：对各模型 ModelUsage camelCase 求和。

    官方 prefer model_usage（整树）。形状：
    - {model_name: {inputTokens, ...} | ModelUsage}
    - list[上述 map 或单条 usage]（形状回喂多轮拼接）
    非 dict/list 或空 → None（退回 usage）。
    """
    if model_usage is None:
        return None

    if isinstance(model_usage, list):
        acc = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        saw = False
        for item in model_usage:
            part = _sum_model_usage(item)
            if part is None:
                continue
            saw = True
            for k in acc:
                acc[k] += part[k]
        return acc if saw else None

    if hasattr(model_usage, "__dict__") and not isinstance(model_usage, dict):
        # 单个 ModelUsage 对象
        raw = {
            "inputTokens": getattr(model_usage, "inputTokens", None)
            if getattr(model_usage, "inputTokens", None) is not None
            else getattr(model_usage, "input_tokens", None),
            "outputTokens": getattr(model_usage, "outputTokens", None)
            if getattr(model_usage, "outputTokens", None) is not None
            else getattr(model_usage, "output_tokens", None),
            "cacheReadInputTokens": getattr(model_usage, "cacheReadInputTokens", None)
            if getattr(model_usage, "cacheReadInputTokens", None) is not None
            else getattr(model_usage, "cache_read_input_tokens", None),
            "cacheCreationInputTokens": getattr(
                model_usage, "cacheCreationInputTokens", None,
            )
            if getattr(model_usage, "cacheCreationInputTokens", None) is not None
            else getattr(model_usage, "cache_creation_input_tokens", None),
        }
        return {
            "prompt_tokens": _tok(raw, "inputTokens", "input_tokens", "prompt_tokens"),
            "completion_tokens": _tok(raw, "outputTokens", "output_tokens", "completion_tokens"),
            "cache_read_input_tokens": _tok(
                raw, "cacheReadInputTokens", "cache_read_input_tokens",
            ),
            "cache_creation_input_tokens": _tok(
                raw, "cacheCreationInputTokens", "cache_creation_input_tokens",
            ),
        }

    if not isinstance(model_usage, dict) or not model_usage:
        return None

    # 单条 usage dict（camelCase / snake）
    if any(
        k in model_usage
        for k in (
            "inputTokens", "outputTokens", "cacheReadInputTokens",
            "cacheCreationInputTokens", "input_tokens", "output_tokens",
            "prompt_tokens", "completion_tokens",
        )
    ):
        return {
            "prompt_tokens": _tok(
                model_usage, "inputTokens", "input_tokens", "prompt_tokens",
            ),
            "completion_tokens": _tok(
                model_usage, "outputTokens", "output_tokens", "completion_tokens",
            ),
            "cache_read_input_tokens": _tok(
                model_usage, "cacheReadInputTokens", "cache_read_input_tokens",
            ),
            "cache_creation_input_tokens": _tok(
                model_usage, "cacheCreationInputTokens", "cache_creation_input_tokens",
            ),
        }

    # {model_name: usage}
    acc = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    saw = False
    for item in model_usage.values():
        part = _sum_model_usage(item)
        if part is None:
            continue
        saw = True
        for k in acc:
            acc[k] += part[k]
    return acc if saw else None


def normalize_usage(
    usage: dict[str, Any] | None = None,
    model_usage: Any = None,
) -> dict[str, int]:
    """归一化为台账四字段。

    优先 model_usage（SDK 整树）的 prompt/completion；cache_* 若 model_usage
    缺省为 0 而 ResultMessage.usage 带有 API 回传 cache，则补上 usage 侧
    （仍是回传字段，禁止自算）。部分网关只在 usage 里填 cache_read。
    """
    raw = usage if isinstance(usage, dict) else {}
    from_usage = {
        "prompt_tokens": _tok(raw, "prompt_tokens", "input_tokens"),
        "completion_tokens": _tok(raw, "completion_tokens", "output_tokens"),
        "cache_read_input_tokens": _tok(raw, "cache_read_input_tokens"),
        "cache_creation_input_tokens": _tok(raw, "cache_creation_input_tokens"),
    }
    summed = _sum_model_usage(model_usage)
    if summed is None:
        return from_usage
    # model_usage 优先；cache 若整树未带而 usage 有，保留 usage 回传值
    if summed["cache_read_input_tokens"] == 0 and from_usage["cache_read_input_tokens"]:
        summed["cache_read_input_tokens"] = from_usage["cache_read_input_tokens"]
    if (
        summed["cache_creation_input_tokens"] == 0
        and from_usage["cache_creation_input_tokens"]
    ):
        summed["cache_creation_input_tokens"] = from_usage["cache_creation_input_tokens"]
    return summed



def usage_total(parts: dict[str, int]) -> int:
    return (
        int(parts.get("prompt_tokens") or 0)
        + int(parts.get("completion_tokens") or 0)
        + int(parts.get("cache_read_input_tokens") or 0)
        + int(parts.get("cache_creation_input_tokens") or 0)
    )


def usage_payload(parts: dict[str, int]) -> dict[str, int]:
    """API / SSE 形状（含 total_tokens）。"""
    return {
        "prompt_tokens": int(parts.get("prompt_tokens") or 0),
        "completion_tokens": int(parts.get("completion_tokens") or 0),
        "cache_read_input_tokens": int(parts.get("cache_read_input_tokens") or 0),
        "cache_creation_input_tokens": int(parts.get("cache_creation_input_tokens") or 0),
        "total_tokens": usage_total(parts),
    }


async def record_usage(
    session: AsyncSession,
    *,
    task_id: str,
    run_id: str | None,
    node_key: str,
    usage: dict[str, Any] | None = None,
    model_usage: Any = None,
    source: str | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> dict[str, int]:
    """记一行消耗。usage/model_usage 缺失/为 0 也记录（会话次数本身是成本信号）。

    成功后若提供 on_event，发 SSE usage.updated（本会话 + 本节点本 run 累计）。
    返回本会话归一化四字段（不含 total）。
    """
    from app.contexts.task.models import AgentUsage

    empty = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    if not task_id:
        return empty

    parts = normalize_usage(usage, model_usage)
    session.add(AgentUsage(
        task_id=task_id,
        run_id=run_id,
        node_key=node_key,
        source=source,
        prompt_tokens=parts["prompt_tokens"],
        completion_tokens=parts["completion_tokens"],
        cache_read_input_tokens=parts["cache_read_input_tokens"],
        cache_creation_input_tokens=parts["cache_creation_input_tokens"],
    ))
    # 累计需看到本行：flush 后再聚合（调用方事务内）
    await session.flush()

    if on_event:
        cumulative = await node_usage_summary(
            session, task_id=task_id, run_id=run_id, node_key=node_key,
        )
        on_event({
            "type": "usage.updated",
            "node_key": node_key,
            "usage": usage_payload(parts),
            "cumulative": cumulative,
        })
    return parts


async def task_usage_summary(session: AsyncSession, task_id: str) -> dict[str, int]:
    from app.contexts.task.models import AgentUsage

    prompt, completion, cache_read, cache_creation = (await session.execute(
        select(
            func.coalesce(func.sum(AgentUsage.prompt_tokens), 0),
            func.coalesce(func.sum(AgentUsage.completion_tokens), 0),
            func.coalesce(func.sum(AgentUsage.cache_read_input_tokens), 0),
            func.coalesce(func.sum(AgentUsage.cache_creation_input_tokens), 0),
        ).where(AgentUsage.task_id == task_id)
    )).one()
    parts = {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "cache_read_input_tokens": int(cache_read or 0),
        "cache_creation_input_tokens": int(cache_creation or 0),
    }
    return {
        **usage_payload(parts),
        "sessions": int((await session.execute(
            select(func.count(AgentUsage.id)).where(AgentUsage.task_id == task_id)
        )).scalar() or 0),
    }


async def node_usage_summary(
    session: AsyncSession,
    *,
    task_id: str,
    run_id: str | None,
    node_key: str,
) -> dict[str, int]:
    """单节点在指定 run（或 run_id 为空时仅 null run 行）上的用量合计。"""
    from app.contexts.task.models import AgentUsage

    q = select(
        func.coalesce(func.sum(AgentUsage.prompt_tokens), 0),
        func.coalesce(func.sum(AgentUsage.completion_tokens), 0),
        func.coalesce(func.sum(AgentUsage.cache_read_input_tokens), 0),
        func.coalesce(func.sum(AgentUsage.cache_creation_input_tokens), 0),
    ).where(
        AgentUsage.task_id == task_id,
        AgentUsage.node_key == node_key,
    )
    if run_id:
        q = q.where(AgentUsage.run_id == run_id)
    else:
        q = q.where(AgentUsage.run_id.is_(None))
    prompt, completion, cache_read, cache_creation = (await session.execute(q)).one()
    return usage_payload({
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "cache_read_input_tokens": int(cache_read or 0),
        "cache_creation_input_tokens": int(cache_creation or 0),
    })


async def run_nodes_usage_map(
    session: AsyncSession, *, task_id: str, run_id: str,
) -> dict[str, dict[str, int]]:
    """run 内按 node_key 聚合；无消耗的节点不出现。"""
    from app.contexts.task.models import AgentUsage

    rows = (await session.execute(
        select(
            AgentUsage.node_key,
            func.coalesce(func.sum(AgentUsage.prompt_tokens), 0),
            func.coalesce(func.sum(AgentUsage.completion_tokens), 0),
            func.coalesce(func.sum(AgentUsage.cache_read_input_tokens), 0),
            func.coalesce(func.sum(AgentUsage.cache_creation_input_tokens), 0),
        )
        .where(AgentUsage.task_id == task_id, AgentUsage.run_id == run_id)
        .group_by(AgentUsage.node_key)
    )).all()
    out: dict[str, dict[str, int]] = {}
    for node_key, prompt, completion, cache_read, cache_creation in rows:
        out[str(node_key)] = usage_payload({
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "cache_read_input_tokens": int(cache_read or 0),
            "cache_creation_input_tokens": int(cache_creation or 0),
        })
    return out


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
    """节点侧记账：从 NodeContext 取会话与标识。prefer meta.model_usage。best-effort。"""
    import logging

    try:
        session = getattr(ctx, "db_session", None)
        if session is None:
            return
        meta = meta or {}
        await record_usage(
            session,
            task_id=getattr(ctx, "task_id", "") or "",
            run_id=getattr(ctx, "run_id", None),
            node_key=node_key,
            usage=meta.get("usage") if isinstance(meta.get("usage"), dict) else None,
            model_usage=meta.get("model_usage"),
            source=source,
            on_event=getattr(ctx, "on_event", None),
        )
    except Exception:  # noqa: BLE001 — 记账失败不影响节点
        logging.getLogger(__name__).warning(
            "usage 记账失败 node=%s", node_key, exc_info=True,
        )
