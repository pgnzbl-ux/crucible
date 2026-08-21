"""env_ready AI 配方轮次与凭据补查。"""
from __future__ import annotations

from typing import Any

from ..base import NodeContext, workspace_repo_path
from . import events


async def run_ai_turn(
    ctx: NodeContext,
    attempt: int,
    prev_error: str | None,
    *,
    failed_stage: str | None = None,
    occupied_host_ports: list[int] | None = None,
    credential_lookup_only: bool = False,
    existing_target_url: str | None = None,
    existing_compose_path: str | None = None,
) -> dict[str, Any]:
    """调 AI(经 ai_runner)产出/修正 Dockerfile/compose。

    返回 {target_url?, compose_path, transport_shape?, initial_creds?, started_containers?}。
    """
    from app.contexts.agent.ai_runner import run_ai_node

    inp = ctx.node_input
    if inp is None:
        raise RuntimeError("env_ready.run_ai_turn 缺少 ctx.node_input（EnvReadyInput）")
    src = inp.source
    repo = src.repo_dirname
    input_json = {
        "source_path": src.workspace_path or workspace_repo_path(repo),
        "profile": inp.profile.model_dump(exclude_none=True),
        "attempt": attempt,
        "previous_error": prev_error,
        "failed_stage": failed_stage,
        "occupied_host_ports": list(occupied_host_ports or []),
        "credential_lookup_only": credential_lookup_only,
        "existing_target_url": existing_target_url,
        "existing_compose_path": existing_compose_path,
    }
    return await run_ai_node(
        node_key="env_ready",
        input_json=input_json,
        host_workdir=ctx.host_workdir,
        runner_env=ctx.runner_env,
        on_event=ctx.on_event,
        task_id=ctx.task_id,
        validate=False,  # 排障环自带逐项校验 + 回喂重试；平台先斩后奏会废掉回喂分支
    )


async def _lookup_initial_creds(
    ctx: NodeContext,
    *,
    target_url: str,
    compose_path: str,
) -> dict[str, Any]:
    events._emit(ctx, "复用靶场缺少凭据元数据，AI 只读源码补查登录方式")
    output = await run_ai_turn(
        ctx,
        1,
        None,
        credential_lookup_only=True,
        existing_target_url=target_url,
        existing_compose_path=compose_path,
    )
    creds = output.get("initial_creds")
    from app.contexts.agent.ai_runner import validate_initial_creds

    ok, err = validate_initial_creds(creds)
    if not ok:
        raise RuntimeError(f"靶场凭据补查失败: {err}")
    return creds


async def _backfill_reused_initial_creds(
    ctx: NodeContext,
    svc: Any,
    result: Any,
) -> dict[str, Any]:
    current = result.initial_creds or {}
    if current:
        return current

    target_url = str(result.target_url or "")
    compose_path = result.compose_path or ".vuln-env/docker-compose.yml"
    creds = await _lookup_initial_creds(
        ctx,
        target_url=target_url,
        compose_path=compose_path,
    )
    await svc.mark_ready(
        result.lab_id,
        target_url=target_url,
        compose_path=compose_path,
        transport_shape=result.transport_shape or {"protocol": "http"},
        initial_creds=creds,
    )
    return creds
