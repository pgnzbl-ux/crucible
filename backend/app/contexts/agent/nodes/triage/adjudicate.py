"""单组判决 — host 切切片 + agent-runner（Claude SDK）submit_result。"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.contexts.finding.hypothesis import Slice, build_pack
from app.contexts.finding.models import Adjudication, AlertGroup, RawFinding
from app.contexts.finding.service import FindingService
from app.contexts.settings.repository import SettingsRepository
from app.core.agent_runner import AgentRunnerError

from ..base import emit_phase, task_run_cancelled, workspace_repo_path

_CONTEXT_TOKEN_BUDGET = 8_000  # 兼容旧引用；新逻辑用 slice_char_budget
# 瞬时 LLM 错误（5xx/断连）退避基数，按重试次数线性放大；测试可置 0
_TRANSIENT_BACKOFF_SECONDS = 2.0


async def hunt_candidate_evidence(session, group_id: str) -> list[dict[str, Any]]:
    """取本组 API Hunt 候选证据，避免混合组由 SAST 代表时丢失鉴权语义。"""
    rows = (
        await session.execute(
            select(RawFinding).where(
                RawFinding.alert_group_id == group_id,
                RawFinding.engine == "api_hunt",
            ).order_by(RawFinding.created_at).limit(8)
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for finding in rows:
        raw = finding.raw if isinstance(finding.raw, dict) else {}
        out.append({
            "endpoint_id": raw.get("endpoint_id"),
            "locus": {
                "file_path": finding.file_path,
                "line_start": finding.line_start,
                "function_symbol": raw.get("function_symbol"),
            },
            "why": list(raw.get("why") or []),
            "evidence": list(raw.get("evidence") or []),
            "qualify": raw.get("qualify") if isinstance(raw.get("qualify"), dict) else {},
            "summary": raw.get("summary"),
            "reasoning": raw.get("reasoning"),
        })
    return out


def slice_char_budget(max_context_tokens: int | None) -> int:
    """按 Provider 窗口换算切片字符预算（约 5% 上下文 × 4 chars/token），夹在 4k–32k。"""
    ctx = int(max_context_tokens) if max_context_tokens else 200_000
    ctx = max(8_000, ctx)
    return max(4_000, min(32_000, (ctx * 4) // 20))


def extract_slices(
    ctx, group, representative, index, *, max_context_tokens: int | None = None
) -> list[Slice]:
    """按需上下文：source→sink 各位置切所在函数；退化命中行±5 行。"""
    from app.contexts.finding.context_extractor import (
        context_around,
        enclosing,
        read_function_source,
    )

    repo_root = ctx.source_path
    slices: list[Slice] = []
    budget = slice_char_budget(max_context_tokens)

    def _add(label: str, text: str | None) -> None:
        nonlocal budget
        if not text:
            return
        if len(text) > budget:
            return
        budget -= len(text)
        slices.append(Slice(label=label, text=text))

    positions: list[tuple[str, int | None]] = [(group.file_path, representative.line_start)]
    for step in (representative.source_to_sink or []):
        path_part = step.split(":", 1)[0]
        line = None
        if ":" in step:
            tail = step.split(":", 1)[1].split("(", 1)[0].strip()
            if tail.isdigit():
                line = int(tail)
        positions.append((path_part, line))

    seen: set[str] = set()
    for path, line in positions:
        if path in seen or not path:
            continue
        seen.add(path)
        entry = enclosing(index, path, line) if index else None
        if entry:
            _add(f"函数 {entry['file']} {entry['symbol']}", read_function_source(repo_root, entry))
        else:
            _add(f"命中位置 {path}", context_around(repo_root, path, line))
    if not slices:
        _add("命中位置", context_around(repo_root, group.file_path, representative.line_start))
    return slices


async def _prepare_adjudication_input(
    ctx, group, settings, *, index=None, max_ctx: int | None = None,
):
    """代表定位 + host 切片 + HypothesisPack + 单组 input 字段（不含 source_path）。

    返回 (fields_dict, representative, slice_count)；组不可审（无代表/无 pack）
    时转 needs_review 并返回 None。
    """
    from .prompt import load_rubric

    svc = FindingService(ctx.db_session)
    representative = await svc.representative_of(group)
    if representative is None:
        await svc.mark_needs_review(group)
        return None

    if index is None:
        from app.contexts.finding.context_extractor import load_index

        index = load_index(ctx.host_workdir)
    slices = extract_slices(
        ctx, group, representative, index, max_context_tokens=max_ctx
    )
    pack = build_pack(group=group, representative=representative, slices=slices)
    if pack is None:
        await svc.mark_needs_review(group)
        return None

    hide = settings.triage_hide_sast_conclusion
    fields: dict[str, Any] = {
        "group_id": str(group.id),
        "closed_question": pack.closed_question,
        "locus": pack.locus.model_dump(),
        "source_to_sink": list(pack.source_to_sink),
        "slices": [s.model_dump() for s in pack.slices],
        "rubric": load_rubric(pack.hypothesis_class) or "",
        "engine_set": list(group.engine_set or []),
        "hypothesis_class": pack.hypothesis_class,
        "grade": pack.grade,
        "has_dataflow": pack.has_dataflow,
    }
    if pack.rule_class:
        fields["rule_class"] = pack.rule_class
    candidate_evidence = await hunt_candidate_evidence(ctx.db_session, group.id)
    if candidate_evidence:
        fields["api_hunt_candidate_evidence"] = candidate_evidence
    if not hide:
        fields["engine_conclusion"] = f"{representative.rule_id}: {representative.message}"
    return fields, representative, len(slices)


def _workspace_repo_for(ctx) -> str:
    """容器内源码路径：与其余 AI 节点一致，不带宿主绝对路径进 prompt。"""
    from ..base import workspace_repo_path

    src = getattr(ctx, "node_input", None)
    source_handoff = getattr(src, "source", None)
    return (
        getattr(source_handoff, "workspace_path", None)
        or workspace_repo_path(getattr(source_handoff, "repo_dirname", None))
        or ctx.source_path
    )


async def adjudicate_group(
    ctx, group, settings, transient_state: dict[str, Any] | None = None,
) -> bool:
    """一组判决：起 triage agent-runner；单组瞬时失败转人工，绝不下沉 fp。

    致命 LLM API 失败（余额不足 / 401 / 模型不存在 / 上下文超限）向上抛出，
    由节点中止流程，不得伪装成「已转人工」后继续 dispatch/report。
    瞬时 LLM 失败（5xx / 断连 / 限流）按 triage_llm_transient_retries 退避重试，
    耗尽后仅该组转人工；但连续多组瞬时降级（transient_state 计数达
    triage_llm_transient_fatal_streak）视为平台级网关故障，同样向上抛出中止。

    transient_state 由节点级调用方持有（{"streak": int, "escalated": bool}），
    跨组共享：判决成功清零，瞬时降级累加，升级时置 escalated 并抛出。

    返回是否记录了判决(降级转人工的组返回 False，不占 adjudicated 计数)。
    """
    from app.contexts.agent.ai_runner import run_ai_node_with_shape_retry
    from app.contexts.agent.llm_errors import (
        is_fatal_llm_error,
        is_llm_api_failure,
    )

    svc = FindingService(ctx.db_session)
    try:
        provider = await SettingsRepository(ctx.db_session).get_default()
    except Exception:  # noqa: BLE001 — 切片预算兜底，不阻断判决
        provider = None
    prepared = await _prepare_adjudication_input(
        ctx, group, settings,
        max_ctx=getattr(provider, "max_context_tokens", None),
    )
    if prepared is None:
        return False
    input_json, _rep, slices_count = prepared
    input_json["source_path"] = _workspace_repo_for(ctx)

    meta: dict[str, Any] = {}
    transient_retries = max(
        0, int(getattr(settings, "triage_llm_transient_retries", 1) or 0)
    )
    streak_limit = max(
        1, int(getattr(settings, "triage_llm_transient_fatal_streak", 3) or 1)
    )
    label = f"{group.cwe or '?'} {group.file_path or ''}".strip()
    attempt = 0
    while True:
        try:
            output = await run_ai_node_with_shape_retry(
                node_key="triage",
                input_json=input_json,
                host_workdir=ctx.host_workdir,
                runner_env=ctx.runner_env or {},
                on_event=ctx.on_event,
                task_id=ctx.task_id,
                meta_out=meta,
            )
            if transient_state is not None:
                transient_state["streak"] = 0
            break
        except AgentRunnerError as e:
            # 取消拆容器产生的 exit=137 不是真实失败：保持 clustered 原状，
            # 由外层逐组取消检查收尾，避免污染成 needs_review
            if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
                return False
            if not is_llm_api_failure(str(e)):
                await svc.mark_needs_review(group)
                return False
            if is_fatal_llm_error(str(e)):
                raise
            # 瞬时 LLM 错误：先退避重试本组，耗尽后转人工；
            # 连续多组降级则升级为平台级中止
            if attempt < transient_retries:
                attempt += 1
                emit_phase(
                    ctx,
                    f"二审瞬时 LLM 错误，退避重试 {attempt}/{transient_retries}：{label}",
                    phase="triage",
                )
                await asyncio.sleep(_TRANSIENT_BACKOFF_SECONDS * attempt)
                continue
            if transient_state is not None:
                transient_state["streak"] = int(transient_state.get("streak", 0)) + 1
                if transient_state["streak"] >= streak_limit:
                    transient_state["escalated"] = True
                    raise AgentRunnerError(
                        f"AI 节点 triage LLM 调用失败: 连续 "
                        f"{transient_state['streak']} 组瞬时错误"
                        f"（疑似网关故障），中止二审: {str(e)[-300:]}"
                    ) from e
            emit_phase(ctx, f"二审瞬时 LLM 错误转人工：{label}", phase="triage")
            await svc.mark_needs_review(group)
            return False

    verdict = output.get("verdict") or "need_more_context"
    if verdict not in ("tp", "fp", "need_more_context"):
        verdict = "need_more_context"

    # 审计链（spec §4.2 全量 prompt/response/usage）：容器 sidecar 回传真实
    # system(skill)+user prompt 与 SDK usage；缺失时退回 input 概要
    system_append = meta.get("system_append")
    prompt_text = (
        "[system] claude_code preset + triage skill:\n"
        f"{system_append if system_append else '(skill 未回传)'}\n\n"
        f"[user]\n{meta.get('prompt') if meta.get('prompt') else input_json!r}"
    )
    response_text = str(meta.get("assistant_text") or output)
    usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    group.verdict_source = "agent"
    from app.contexts.agent.usage_ledger import normalize_usage, record_usage

    # 台账 prefer model_usage；判决审计链仍保留 sidecar usage 原文
    await record_usage(
        ctx.db_session, task_id=ctx.task_id, run_id=ctx.run_id,
        node_key="triage",
        usage=usage,
        model_usage=meta.get("model_usage"),
        source="agent",
        on_event=ctx.on_event,
    )
    # adjudications.usage 写入归一化后的数值（含 cache），便于复核对齐台账
    adj_usage = normalize_usage(usage, meta.get("model_usage"))
    qualify = {
        "attacker_controlled": output.get("attacker_controlled"),
        "reaches_sink": output.get("reaches_sink"),
        "sanitizer": output.get("sanitizer"),
    }
    from app.contexts.finding.narrative import narrative_from_agent

    summary, reasoning = narrative_from_agent(output)
    await svc.record_adjudication(
        group=group,
        adjudication=Adjudication(
            alert_group_id=group.id, attempt=1,
            provider_id=None, model=meta.get("model"),
            verdict=verdict,
            confidence=float(output.get("confidence") or 0.0),
            why=list(output.get("why") or []),
            evidence=list(output.get("evidence") or []),
            need=list(output.get("need") or []),
            summary=summary,
            reasoning=reasoning,
            context_log=[{
                "round": 1, "slices": slices_count, "via": "agent-runner",
                "qualify": qualify,
            }],
            prompt_text=prompt_text[:50000],
            response_text=response_text[:20000],
            usage=adj_usage,
        ),
    )
    return True


async def adjudicate_families_batch(
    ctx, families, settings, stats,
) -> dict[str, Any]:
    """批量子代理二审：整个节点一次容器，家族在容器内以 Task 子代理并行。

    输出契约 {"verdicts":[...]} 与单组形状同源（_validate_triage_output 分支）；
    台账只记一次（model_usage 已聚合主环+子代理整树）。返回：
      - {"ok": True} 全部落库/降级完成，继续族内传播
      - {"cancelled": True} 任务已取消
      - AgentRunnerError 向上抛（致命 LLM/网关故障），与逐族路径语义一致
    未出现在输出中的族保持原状 → 由 mark_unaudited_for_review 兜底转人工。
    """
    from app.core.config import get_settings

    total = len(families)
    emit_phase(
        ctx,
        f"批量代表审议启动（子代理模式）：{total} 族 · 单容器内并行",
        phase="triage",
    )
    async def _precheck() -> str | None:
        from app.contexts.agent.nodes.base import task_run_cancelled

        if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
            return "cancelled"
        from app.contexts.agent.usage_ledger import budget_state

        exhausted, spent, budget = await budget_state(ctx.db_session, ctx.task_id)
        if exhausted:
            stats.budget_exhausted = True
            emit_phase(
                ctx,
                f"token 预算耗尽（{spent}/{budget}），跳过本轮批量审议，未审组转人工",
                phase="triage",
            )
            return "budget"
        return None

    stop = await _precheck()
    if stop == "cancelled":
        return {"cancelled": True}
    if stop == "budget":
        return {"ok": True}

    max_ctx: int | None = None
    try:
        provider = await SettingsRepository(ctx.db_session).get_default()
        if provider is not None:
            max_ctx = getattr(provider, "max_context_tokens", None)
    except Exception:  # noqa: BLE001 — 切片预算兜底
        max_ctx = None
    from app.contexts.finding.context_extractor import load_index

    index = load_index(ctx.host_workdir)

    entries: list[dict[str, Any]] = []
    family_by_gid: dict[str, Any] = {}
    for family in families:
        prepared = await _prepare_adjudication_input(
            ctx, family.representative, settings, index=index, max_ctx=max_ctx,
        )
        if prepared is None:
            continue
        fields, _rep, _n = prepared
        label = (
            f"{family.representative.cwe or '?'} "
            f"{family.representative.file_path or ''}"
        ).strip()
        entries.append({"label": label, **fields})
        family_by_gid[str(family.representative.id)] = family

    if not entries:
        # 无可审代表（全部缺代表/pack）：交给传播前的兜底
        return {"ok": True}

    batch_input: dict[str, Any] = {
        "mode": "batch",
        "batch_size": len(entries),
        "source_path": _workspace_repo_for(ctx),
        "families": entries,
    }

    if not get_settings().claude_agent_sdk_enabled:
        # Mock：与 _mock_output 同语义，逐族 tp 定格式，不起容器
        output = {
            "verdicts": [
                {
                    "group_id": e["group_id"], "verdict": "tp",
                    "confidence": 0.85,
                    "why": ["[Mock] 批量子代理模式固定二审"],
                    "evidence": ["[Mock] evidence"],
                    "summary": "[Mock] 批量判决",
                    "reasoning": "[Mock] 批量判决",
                    "attacker_controlled": True,
                    "reaches_sink": True,
                    "sanitizer": "none",
                }
                for e in entries
            ],
        }
        meta: dict[str, Any] = {}
    else:
        from app.contexts.agent.ai_runner import run_ai_node_with_shape_retry
        from app.contexts.agent.nodes.base import task_run_cancelled

        meta = {}
        try:
            output = await run_ai_node_with_shape_retry(
                node_key="triage",
                input_json=batch_input,
                host_workdir=ctx.host_workdir,
                runner_env=ctx.runner_env or {},
                on_event=ctx.on_event,
                task_id=ctx.task_id,
                meta_out=meta,
                skill_override="triage_batch",
            )
        except AgentRunnerError as e:
            # 取消拆容器（exit=137）不是失败：交由编排器取消链路收尾
            if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
                return {"cancelled": True}
            raise

    # 台账一次：整树用量（主会话+子代理）已在 model_usage 聚合
    from app.contexts.agent.usage_ledger import record_usage

    await record_usage(
        ctx.db_session, task_id=ctx.task_id, run_id=ctx.run_id,
        node_key="triage",
        usage={},
        model_usage=meta.get("model_usage"),
        source="agent",
        on_event=ctx.on_event,
    )

    svc = FindingService(ctx.db_session)
    done = 0
    system_append = meta.get("system_append")
    assistant_text = str(meta.get("assistant_text") or "")[:20000]
    for item in output.get("verdicts") or []:
        gid = str(item.get("group_id") or "").strip()
        family = family_by_gid.get(gid)
        if family is None:
            continue
        group = await ctx.db_session.get(AlertGroup, gid)
        if group is None:
            continue
        verdict = item.get("verdict") or "need_more_context"
        if verdict not in ("tp", "fp", "need_more_context"):
            verdict = "need_more_context"
        group.verdict_source = "agent"
        qualify = {
            "attacker_controlled": item.get("attacker_controlled"),
            "reaches_sink": item.get("reaches_sink"),
            "sanitizer": item.get("sanitizer"),
        }
        from app.contexts.finding.narrative import narrative_from_agent

        summary, reasoning = narrative_from_agent(item)
        prompt_text = (
            "[system] claude_code preset + triage_batch skill:\n"
            f"{system_append if system_append else '(skill 未回传)'}\n\n"
            f"[user][family {gid}] "
            f"{next((en['label'] for en in entries if en['group_id'] == gid), '')}"
        )
        await svc.record_adjudication(
            group=group,
            adjudication=Adjudication(
                alert_group_id=group.id, attempt=1,
                provider_id=None, model=meta.get("model"),
                verdict=verdict,
                confidence=float(item.get("confidence") or 0.0),
                why=list(item.get("why") or []),
                evidence=list(item.get("evidence") or []),
                need=list(item.get("need") or []),
                summary=summary,
                reasoning=reasoning,
                context_log=[{
                    "round": 1, "slices": None, "via": "agent-runner-batch",
                    "qualify": qualify,
                }],
                prompt_text=prompt_text[:50000],
                response_text=assistant_text,
                usage={},
            ),
        )
        stats.agent += 1
        done += 1
    emit_phase(
        ctx,
        f"批量代表审议完成：{done}/{len(entries)} 族已判定（未覆盖的族自动转人工）",
        phase="triage",
    )
    if ctx.on_event:
        ctx.on_event({
            "type": "triage.progress",
            "node_key": "triage",
            "done": done,
            "total": len(entries),
            "message": f"批量审议完成 {done}/{len(entries)}",
        })
    return {"ok": True}
