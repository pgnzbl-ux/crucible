"""triage 分级收敛管线 — 逐层过滤，只有少数不确定项走到全价 agent 二审。

T0 携带：同项目同代表指纹的历史判决直接携带（重跑/新任务零 LLM 成本复用）。
T1 规则前置：历史 agent 亲审 FP 率 ≥ 阈值且样本足够的规则，新命中直接判 fp。
T2 快模型首审：llm_gateway screening 角色单次调用（无容器、无工具），
   高置信定案；低置信 / need_more_context / 网关失败 → 升级。
T3 族级审议：同根因族(rule|cwe|目录)只审代表，族内传播（置信度打折）。

每一层的判决都落 Adjudication 审计行并在 AlertGroup.verdict_source 标记
来源（agent | fast_model | rule | carryover | propagated），复核台可溯源抽查。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, case, func, or_, select

from app.contexts.agent.nodes.base import emit_phase
from app.contexts.finding.models import Adjudication, AlertGroup, RawFinding
from app.contexts.finding.service import FindingService

logger = logging.getLogger(__name__)

FAST_CONCURRENCY = 8  # 快模型纯 HTTP 调用，并发可高于 agent
FAST_PROGRESS_EVERY = 25  # 快审每完成 N 次调用播报一次进度事件
FAST_BATCH = 100  # 快审分批粒度：批间复查 token 预算


@dataclass
class TierStats:
    """级联各层消减统计（output_json / triage.progress 数据源）。"""

    carried: int = 0
    rule: int = 0
    fast: int = 0
    agent: int = 0
    propagated: int = 0
    propagated_review: int = 0
    escalated: int = 0
    families: int = 0
    budget_exhausted: bool = False

    def summary(self) -> str:
        return (
            f"携带 {self.carried} · 规则 {self.rule} · 快审 {self.fast} · 族代表 {self.agent} · 传播 {self.propagated}"
        )


@dataclass
class Family:
    key: str
    members: list[AlertGroup] = field(default_factory=list)

    @property
    def representative(self) -> AlertGroup:
        """成员数最多者为代表；并列取 group_key 排序保证确定性。"""
        return sorted(self.members, key=lambda g: (-g.member_count, g.group_key))[0]


def _tier_adjudication(
    *,
    verdict: str,
    confidence: float | None,
    why: list[str],
    source: str,
    detail: dict[str, Any],
    usage: dict[str, int] | None = None,
    summary: str | None = None,
    reasoning: str | None = None,
) -> Adjudication:
    """非 agent 层的审计行：prompt/response 存判定依据摘要，保住回放可解释性。"""
    from app.contexts.finding.narrative import narrative_from_why

    syn_s, syn_r = narrative_from_why(why)
    return Adjudication(
        attempt=1,
        verdict=verdict,
        confidence=confidence,
        why=why,
        evidence=[],
        need=[],
        summary=(summary or syn_s)[:800],
        reasoning=(reasoning or syn_r)[:8000],
        context_log=[{"tier": source, **detail}],
        prompt_text=f"[{source}] 级联前置判定，无 LLM/agent 会话",
        response_text=json.dumps(detail, ensure_ascii=False, default=str),
        usage=usage or {},
    )


async def _apply(
    svc: FindingService,
    group: AlertGroup,
    adjudication: Adjudication,
    *,
    source: str,
    run_id: str | None = None,
    on_event=None,
) -> None:
    adjudication.alert_group_id = group.id
    group.verdict_source = source
    if source == "fast_model":
        # 快审有真实 LLM 消耗，入台账；携带/规则/传播是零成本前置层
        from app.contexts.agent.usage_ledger import record_usage

        await record_usage(
            svc.session,
            task_id=group.task_id,
            run_id=run_id,
            node_key="screen",
            usage=adjudication.usage or {},
            source="fast_model",
            on_event=on_event,
        )
    await svc.record_adjudication(group=group, adjudication=adjudication)


async def rule_by_rep(session, rep_ids: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """rep_id → (rule_id, fingerprint) 批量预取。"""
    if not rep_ids:
        return {}, {}
    rows = (
        await session.execute(
            select(RawFinding.id, RawFinding.rule_id, RawFinding.fingerprint).where(RawFinding.id.in_(rep_ids))
        )
    ).all()
    return {rid: rule for rid, rule, _fp in rows}, {rid: fp for rid, _rule, fp in rows}


# ── T0 同项目同指纹判决携带 ──────────────────────────────


async def apply_carryover(
    svc: FindingService,
    *,
    groups: list[AlertGroup],
    project_id: str | None,
    settings,
) -> tuple[list[AlertGroup], int]:
    """返回 (仍未决的组, 携带定案数)。

    携带条件：同项目、代表指纹相同、历史判决 tp/fp 且置信达标。
    """
    if not getattr(settings, "triage_carryover_enabled", False) or not groups:
        return groups, 0
    if not project_id:
        return groups, 0
    session = svc.session
    _, fp_of = await rule_by_rep(session, [g.representative_finding_id for g in groups])
    fingerprints = set(fp_of.values())
    if not fingerprints:
        return groups, 0
    min_conf = settings.triage_carryover_min_confidence
    from app.contexts.task.models import Task

    rows = (
        await session.execute(
            select(RawFinding.fingerprint, AlertGroup.ai_verdict, AlertGroup.ai_confidence)
            .join(AlertGroup, AlertGroup.representative_finding_id == RawFinding.id)
            .join(Task, Task.id == AlertGroup.task_id)
            .where(
                Task.project_id == project_id,
                AlertGroup.status.in_(("adjudicated", "resolved")),
                AlertGroup.ai_verdict.in_(("tp", "fp")),
                AlertGroup.ai_confidence >= min_conf,
                # 只携带 agent 亲审真值：rule/fast/propagated 入库即成"永久真值"
                # 会跨任务自举复利（与 T1 统计同口径）
                or_(
                    AlertGroup.verdict_source.is_(None),
                    AlertGroup.verdict_source == "agent",
                ),
                RawFinding.fingerprint.in_(fingerprints),
            )
            .order_by(AlertGroup.ai_confidence.desc())
        )
    ).all()
    history: dict[str, tuple[str, float]] = {}
    for fp, verdict, conf in rows:
        history.setdefault(fp, (verdict, float(conf or 0)))
    remaining = []
    carried = 0
    for group in groups:
        fp = fp_of.get(group.representative_finding_id)
        hit = history.get(fp) if fp else None
        if hit is None:
            remaining.append(group)
            continue
        verdict, conf = hit
        await _apply(
            svc,
            group,
            _tier_adjudication(
                verdict=verdict,
                confidence=conf,
                source="carryover",
                why=[f"同项目同指纹历史判决携带（{verdict}，置信 {conf}）"],
                detail={"fingerprint": fp, "historical_verdict": verdict},
            ),
            source="carryover",
        )
        carried += 1
    return remaining, carried


# ── T1 规则历史 FP 率前置 ────────────────────────────────


async def rule_fp_rates(
    session,
    *,
    min_samples: int,
    resolved_weight: float = 3.0,
) -> dict[str, tuple[float, float]]:
    """规则 FP 先验：验证真值(resolution)加权融合 agent 亲审。

    - resolved false_positive/ignored → fp 证据 × resolved_weight（真值优先，
      同组的 agent 判决不再计票，避免同一条发现重复计权）
    - resolved confirmed → tp 证据 × resolved_weight
    - 未验证组只计 agent 亲审(null/agent 来源)，fast_model/rule/propagated
      的输出不回流——避免前置层自举污染先验
    返回 {rule_id: (fp_rate, 加权样本量)}。
    """
    fp_expr = case(
        (AlertGroup.resolution.in_(("false_positive", "ignored")), resolved_weight),
        # 已验证组只计真值票：confirmed 的组不再按 agent 判决计 fp
        (AlertGroup.resolution.is_not(None), 0.0),
        (AlertGroup.ai_verdict == "fp", 1.0),
        else_=0.0,
    )
    tp_expr = case(
        (AlertGroup.resolution == "confirmed", resolved_weight),
        # 已验证组只计真值票：fp/ignored 的组不再按 agent 判决计 tp
        (AlertGroup.resolution.is_not(None), 0.0),
        (AlertGroup.ai_verdict == "tp", 1.0),
        else_=0.0,
    )
    rows = (
        await session.execute(
            select(
                RawFinding.rule_id,
                func.sum(fp_expr).label("fp_w"),
                func.sum(tp_expr).label("tp_w"),
            )
            .join(AlertGroup, AlertGroup.representative_finding_id == RawFinding.id)
            .where(
                or_(
                    AlertGroup.resolution.is_not(None),
                    and_(
                        AlertGroup.ai_verdict.in_(("tp", "fp")),
                        or_(
                            AlertGroup.verdict_source.is_(None),
                            AlertGroup.verdict_source == "agent",
                        ),
                    ),
                )
            )
            .group_by(RawFinding.rule_id)
        )
    ).all()
    return {
        rule: (float(fp_w or 0) / total, total)
        for rule, fp_w, tp_w in rows
        if rule and (total := float(fp_w or 0) + float(tp_w or 0)) >= min_samples
    }


async def calibrated_propagate_factor(
    session,
    *,
    default_factor: float,
    min_verified: int,
    project_id: str | None = None,
) -> float:
    """传播折扣按历史验证一致率自校准。

    一致率 = agent 亲审(tp/fp) 与终态 resolution 同向的比例。样本不足时
    返回默认折扣。夹在 [0.3, 0.95]：校准是修正不是颠覆。
    给 project_id 时只统计同项目（与 T0/T1 同口径），避免跨项目互染。
    """
    agree_expr = case(
        (
            or_(
                and_(AlertGroup.ai_verdict == "tp", AlertGroup.resolution == "confirmed"),
                and_(
                    AlertGroup.ai_verdict == "fp",
                    AlertGroup.resolution.in_(("false_positive", "ignored")),
                ),
            ),
            1.0,
        ),
        else_=0.0,
    )
    stmt = select(func.count(), func.sum(agree_expr)).where(
        AlertGroup.ai_verdict.in_(("tp", "fp")),
        AlertGroup.resolution.is_not(None),
    )
    if project_id:
        from app.contexts.task.models import Task

        stmt = stmt.join(Task, Task.id == AlertGroup.task_id).where(
            Task.project_id == project_id,
        )
    n, agreed = (await session.execute(stmt)).one()
    n, agreed = int(n or 0), float(agreed or 0)
    if n < min_verified:
        return default_factor
    return max(0.3, min(0.95, agreed / n))


async def apply_rule_preverdict(
    svc: FindingService,
    *,
    groups: list[AlertGroup],
    settings,
) -> tuple[list[AlertGroup], int]:
    """返回 (仍未决的组, 规则前置定案数)。"""
    if not getattr(settings, "triage_rule_enabled", False) or not groups:
        return groups, 0
    session = svc.session
    rates = await rule_fp_rates(
        session,
        min_samples=settings.triage_rule_min_samples,
        resolved_weight=getattr(settings, "triage_feedback_resolved_weight", 3.0),
    )
    hot = {rule: (rate, n) for rule, (rate, n) in rates.items() if rate >= settings.triage_rule_fp_rate_min}
    if not hot:
        return groups, 0
    rule_of, _ = await rule_by_rep(session, [g.representative_finding_id for g in groups])
    remaining = []
    decided = 0
    for group in groups:
        rule = rule_of.get(group.representative_finding_id)
        hit = hot.get(rule) if rule else None
        if hit is None:
            remaining.append(group)
            continue
        rate, n = hit
        await _apply(
            svc,
            group,
            _tier_adjudication(
                verdict="fp",
                confidence=min(rate, 0.99),
                source="rule",
                why=[f"规则 {rule} 历史 agent 亲审 FP 率 {rate:.0%}（n={n}）"],
                detail={"rule_id": rule, "fp_rate": rate, "samples": n},
            ),
            source="rule",
        )
        decided += 1
    return remaining, decided


# ── T2 快模型首审（llm_gateway screening）────────────────


def _fast_prompt(pack, group, *, rubric: str | None) -> tuple[str, str]:
    """(system, user)：与 agent 审议同源的封闭问题 + 切片，单次补全。"""
    system = (
        "你是 SAST 告警二审的快审员。只依据给定切片与污点路径判断，"
        "不得臆造代码外的信息。输出严格 JSON："
        '{"verdict":"tp|fp|need_more_context","confidence":0-1,"why":["..."],"need":["..."]}\n'
        "tp=证据足以确认可利用/可达；fp=证据足以否定；不确定必须给 need_more_context。"
        f"{rubric or ''}"
    )
    user = json.dumps(
        {
            "closed_question": pack.closed_question,
            "cwe": group.cwe,
            "file_path": group.file_path,
            "function_symbol": group.function_symbol,
            "line_span": group.line_span,
            "grade": pack.grade,
            "has_dataflow": pack.has_dataflow,
            "rule_class": pack.rule_class,
            "source_to_sink": list(pack.source_to_sink),
            "slices": [{"label": s.label, "text": s.text} for s in pack.slices],
        },
        ensure_ascii=False,
    )
    return system, user


def _snapshot_provider(provider):
    """复制轻量 Messages 消费的字段，避免并发协程读取 ORM 对象。"""
    from app.contexts.settings.provider_runtime import ProviderRuntimeConfig

    return ProviderRuntimeConfig.from_provider(provider)


async def fast_screen(
    ctx,
    svc: FindingService,
    *,
    groups: list[AlertGroup],
    settings,
) -> tuple[list[AlertGroup], int]:
    """返回 (升级到 agent 的组, 快审定案数)。快审失败/低置信一律升级，绝不下沉 fp。"""
    from app.core.llm_gateway import llm_complete, parse_verdict_json

    if not getattr(settings, "triage_fast_model_enabled", False) or not groups:
        return groups, 0
    from app.contexts.agent.nodes.triage.adjudicate import extract_slices
    from app.contexts.agent.nodes.triage.prompt import load_rubric
    from app.contexts.finding.context_extractor import load_index
    from app.contexts.finding.hypothesis import build_pack

    session = svc.session
    index = load_index(ctx.host_workdir)
    prepared: list[tuple[AlertGroup, str, str]] = []
    escalated: list[AlertGroup] = []
    for group in groups:
        rep = await svc.representative_of(group)
        if rep is None:
            escalated.append(group)
            continue
        slices = extract_slices(ctx, group, rep, index)
        if not slices:
            # 无代码切片不送快审：单次补全没有工具补看源码，零上下文等于
            # 盲猜（agent 亲审有仓库挂载，不受此限制）
            escalated.append(group)
            continue
        pack = build_pack(group=group, representative=rep, slices=slices)
        if pack is None:
            escalated.append(group)
            continue
        system, user = _fast_prompt(
            pack,
            group,
            rubric=load_rubric(pack.hypothesis_class),
        )
        prepared.append((group, system, user))

    # 并发协程绝不共享 session（greenlet 互锁 + 事务锁滞留的根源）：
    # provider 在并发前一次性解析，之后 llm_complete 只走 HTTP
    from app.core.llm_gateway import _resolve_provider

    try:
        provider = await _resolve_provider(session, "screening")
    except Exception as e:  # noqa: BLE001 — 无可用 provider：全组升级 agent
        logger.warning("快审 provider 解析失败，整层升级: %s", e)
        return groups, 0
    # ORM 对象并发跨协程读属性是隐性契约（绑定 session 过期即 greenlet 崩）：
    # 快照成纯值再交给并发调用，与解析会话彻底解耦
    provider = _snapshot_provider(provider)

    sem = asyncio.Semaphore(FAST_CONCURRENCY)
    # 快审全程可见：每 FAST_PROGRESS_EVERY 次调用播报一次进度
    state = {"done": 0, "tp": 0, "fp": 0, "escalated": 0}
    total = len(prepared)
    emit_phase(
        ctx,
        f"快审启动：{total} 组送 screening 模型（并发 {FAST_CONCURRENCY}）",
        phase="screen",
    )

    def _note_progress(outcome: tuple | None) -> None:
        state["done"] += 1
        if outcome is None:
            state["escalated"] += 1
        elif outcome[0] == "tp":
            state["tp"] += 1
        elif outcome[0] == "fp":
            state["fp"] += 1
        if state["done"] % FAST_PROGRESS_EVERY == 0 or state["done"] == total:
            emit_phase(
                ctx,
                f"快审 {state['done']}/{total}（可疑真洞 {state['tp']} · 误报 {state['fp']} · 升级 {state['escalated']}）",
                phase="screen",
            )
            if ctx.on_event:
                ctx.on_event(
                    {
                        "type": "triage.progress",
                        "adjudicated": state["tp"] + state["fp"],
                        "pending": total - state["done"],
                        "tiers": {
                            "carried": 0,
                            "rule": 0,
                            "fast_model": state["tp"] + state["fp"],
                            "agent": 0,
                            "propagated": 0,
                        },
                        "stage": "fast_screen",
                        "node_key": "screen",
                    }
                )

    async def _one(item) -> tuple[AlertGroup, tuple | None]:
        group, system, user = item
        async with sem:
            try:
                result = await llm_complete(
                    role="screening",
                    system=system,
                    user=user,
                    provider=provider,
                    max_tokens=1024,
                )
                parsed = parse_verdict_json(result.text)
            except Exception as e:  # noqa: BLE001 — 瞬时失败升级；平台级 LLM 失败中止
                from app.contexts.agent.llm_errors import is_llm_api_failure
                from app.core.agent_runner import AgentRunnerError

                if is_llm_api_failure(str(e)):
                    raise AgentRunnerError(f"AI 节点 screen LLM 调用失败: {e}") from e
                logger.info("快审失败升级 agent: %s %s", group.group_key, e)
                _note_progress(None)
                return group, None
            verdict = parsed.get("verdict")
            if verdict not in ("tp", "fp", "need_more_context"):
                verdict = "need_more_context"
            try:
                confidence = float(parsed.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
            outcome = (verdict, confidence, parsed, result)
            _note_progress(outcome if verdict == "fp" else None)
            return group, outcome

    # 快审也烧 token（本层 ~1.2k/条 × 数百条）：预算耗尽整层跳过、
    # 分批间复查（主会话串行点，并发协程不碰会话）
    from app.contexts.agent.usage_ledger import budget_state

    exhausted, spent, budget = await budget_state(session, ctx.task_id)
    if exhausted:
        emit_phase(
            ctx,
            f"token 预算耗尽（{spent}/{budget}），快审跳过，全部升级 agent 审议",
            phase="screen",
        )
        return groups, 0

    results: list[tuple[AlertGroup, tuple | None]] = []
    for start in range(0, len(prepared), FAST_BATCH):
        batch = prepared[start : start + FAST_BATCH]
        results.extend(await asyncio.gather(*[_one(item) for item in batch]))
        exhausted, spent, budget = await budget_state(session, ctx.task_id)
        if exhausted and start + FAST_BATCH < len(prepared):
            emit_phase(
                ctx,
                f"token 预算耗尽（{spent}/{budget}），快审中止于 {len(results)}/{len(prepared)}，其余升级 agent 审议",
                phase="screen",
            )
            break
    by_id = {g.id: outcome for g, outcome in results}
    threshold = settings.triage_fast_confidence
    decided = 0
    for group, system, user in prepared:
        outcome = by_id.get(group.id)
        if outcome is None:
            escalated.append(group)
            continue
        verdict, confidence, parsed, result = outcome
        if verdict == "fp" and confidence >= threshold:
            await _apply(
                svc,
                group,
                _tier_adjudication(
                    verdict=verdict,
                    confidence=confidence,
                    source="fast_model",
                    why=[str(w) for w in (parsed.get("why") or [])][:5],
                    detail={"model": result.model},
                    usage=result.usage,
                ),
                source="fast_model",
                run_id=getattr(ctx, "run_id", None),
                on_event=getattr(ctx, "on_event", None),
            )
            decided += 1
        else:
            escalated.append(group)
    # 未参与快审（无代表/pack 不合格）的组同样升级
    prepared_ids = {g.id for g, _s, _u in prepared}
    escalated.extend(g for g in groups if g.id not in prepared_ids)
    return escalated, decided


# ── T3 同根因族 ─────────────────────────────────────────


def family_key_of(group: AlertGroup, rule_id: str | None) -> str:
    """族键 = rule|CWE|文件目录。同目录同规则同 CWE 视为同根因模式。"""
    module = "/".join((group.file_path or "").split("/")[:-1])
    raw = f"{rule_id or ''}|{group.cwe or ''}|{module}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def group_families(
    groups: list[AlertGroup],
    rule_of: dict[str, str],
) -> list[Family]:
    families: dict[str, Family] = {}
    for group in groups:
        rule = rule_of.get(group.representative_finding_id)
        key = family_key_of(group, rule)
        group.family_key = key
        families.setdefault(key, Family(key=key)).members.append(group)
    return list(families.values())


async def propagate_family_verdicts(
    svc: FindingService,
    *,
    family: Family,
    rep: AlertGroup,
    settings,
    factor: float | None = None,
) -> tuple[int, int]:
    """代表判决传播到成员。返回 (传播定案数, 转人工数)。

    代表 tp/fp 且置信达标 → 成员同判决、置信度打折(factor 可由验证一致率
    自校准，缺省用 settings 默认)、来源 propagated；
    否则成员转 needs_review（宁可人工也不错误传播）。
    """
    verdict = rep.ai_verdict
    confidence = float(rep.ai_confidence or 0)
    discount = factor if factor is not None else float(getattr(settings, "triage_propagate_confidence_factor", 0.85))
    propagated = review = 0
    for member in family.members:
        if member.id == rep.id:
            continue
        if verdict in ("tp", "fp") and confidence >= settings.triage_propagate_min_confidence:
            await _apply(
                svc,
                member,
                _tier_adjudication(
                    verdict=verdict,
                    confidence=round(confidence * discount, 3),
                    source="propagated",
                    why=[
                        f"同根因族 {family.key[:8]} 代表判决传播"
                        f"（代表 {verdict}，置信 {confidence}，折扣 {discount:.2f}）"
                    ],
                    detail={
                        "family_key": family.key,
                        "representative_id": rep.id,
                        "propagate_factor": discount,
                    },
                ),
                source="propagated",
            )
            propagated += 1
        else:
            await svc.mark_needs_review(member)
            review += 1
    return propagated, review
