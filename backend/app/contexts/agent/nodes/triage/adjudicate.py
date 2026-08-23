"""单组判决 — host 切切片 + agent-runner（Claude SDK）submit_result。"""
from __future__ import annotations

from typing import Any

from app.contexts.finding.hypothesis import Slice, build_pack
from app.contexts.finding.models import Adjudication
from app.contexts.finding.service import FindingService
from app.core.agent_runner import AgentRunnerError

from ..base import task_run_cancelled, workspace_repo_path

_CONTEXT_TOKEN_BUDGET = 8_000


def extract_slices(ctx, group, representative, index) -> list[Slice]:
    """按需上下文：source→sink 各位置切所在函数；退化命中行±5 行。"""
    from app.contexts.finding.context_extractor import (
        context_around,
        enclosing,
        read_function_source,
    )

    repo_root = ctx.source_path
    slices: list[Slice] = []
    budget = _CONTEXT_TOKEN_BUDGET

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


async def adjudicate_group(ctx, group, settings) -> bool:
    """一组判决：起 triage agent-runner；失败转人工，绝不下沉 fp。

    返回是否记录了判决(降级转人工的组返回 False，不占 adjudicated 计数)。
    """
    from app.contexts.agent.ai_runner import run_ai_node_with_shape_retry
    from app.contexts.finding.context_extractor import load_index
    from .prompt import load_rubric

    svc = FindingService(ctx.db_session)
    representative = await svc.representative_of(group)
    if representative is None:
        await svc.mark_needs_review(group)
        return False

    index = load_index(ctx.host_workdir)
    slices = extract_slices(ctx, group, representative, index)
    pack = build_pack(group=group, representative=representative, slices=slices)
    if pack is None:
        await svc.mark_needs_review(group)
        return False

    hide = settings.triage_hide_sast_conclusion
    # 容器内源码路径：与其余 AI 节点一致（audit/reproduce 传 workspace_path），
    # 不把宿主绝对路径带进 prompt
    src = getattr(ctx, "node_input", None)
    source_handoff = getattr(src, "source", None)
    workspace_repo = (
        getattr(source_handoff, "workspace_path", None)
        or workspace_repo_path(getattr(source_handoff, "repo_dirname", None))
        or ctx.source_path
    )
    input_json: dict[str, Any] = {
        "source_path": workspace_repo,
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
        input_json["rule_class"] = pack.rule_class
    if not hide:
        input_json["engine_conclusion"] = f"{representative.rule_id}: {representative.message}"

    meta: dict[str, Any] = {}
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
    except AgentRunnerError:
        # 取消拆容器产生的 exit=137 不是真实失败：保持 clustered 原状，
        # 由外层逐组取消检查收尾，避免污染成 needs_review
        if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
            return False
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
    from app.contexts.agent.usage_ledger import record_usage

    await record_usage(
        ctx.db_session, task_id=ctx.task_id, run_id=ctx.run_id,
        node_key="triage", usage=usage, source="agent",
    )
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
            context_log=[{"round": 1, "slices": len(slices), "via": "agent-runner"}],
            prompt_text=prompt_text[:50000],
            response_text=response_text[:20000],
            usage=usage,
        ),
    )
    return True
