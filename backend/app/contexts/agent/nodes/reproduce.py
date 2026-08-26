"""节点 4 复现验证(AI)— 吃靶场就绪 Handoff + audit 子集,产出复现证据+verdict。"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from app.contexts.agent.contracts import InputAssembler, ReproduceInput
from app.contexts.agent.contracts.outputs import EnvReadyHandoff

from .base import NodeContext, emit_phase, workspace_repo_path

logger = logging.getLogger(__name__)

# 访问超时且 Docker 已销毁/停止时才重调度 env_ready；容器仍在跑则交给 AI 自行探测。
_REVIVE_RUNTIMES = frozenset({"none", "exited"})


async def _probe_lab_access(
    target_url: str,
    *,
    compose_project: str | None = None,
) -> bool:
    """短探活：超时/无响应视为访问失败。"""
    from app.contexts.agent.nodes.env_ready import health

    raw = (target_url or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host = parsed.hostname or "127.0.0.1"
    scheme = parsed.scheme or "http"
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    ok, _, _ = await health.health_check(
        [port],
        host_ips=[host],
        preferred_scheme=scheme,
        probe_path=path,
        compose_project=compose_project,
        retries=2,
        retry_seconds=1,
        settle_seconds=0,
    )
    return ok


async def _lab_runtime_kind(ctx: NodeContext) -> str:
    """读 compose 实际容器态：none / running / partial / exited / unknown。

    无 lab_id 时返回 unknown，避免单测/白盒路径误判为已销毁并强行重调度。
    """
    from app.contexts.lab.docker_ops import list_containers
    from app.contexts.lab.service import LabService, container_runtime_kind

    if not ctx.lab_id or ctx.db_session is None:
        return "unknown"
    svc = LabService(ctx.db_session)
    lab = await svc.repository.get(ctx.lab_id)
    if lab is None or not lab.compose_project:
        return "none"
    try:
        containers = await list_containers(lab.compose_project)
    except Exception:  # noqa: BLE001
        logger.warning("reproduce 列举靶场容器失败 lab=%s", ctx.lab_id, exc_info=True)
        return "none"
    return container_runtime_kind(containers)


async def _compose_project_for(ctx: NodeContext) -> str | None:
    if not ctx.lab_id or ctx.db_session is None:
        return None
    from app.contexts.lab.service import LabService

    lab = await LabService(ctx.db_session).repository.get(ctx.lab_id)
    return lab.compose_project if lab is not None else None


async def _ensure_lab_reachable(
    ctx: NodeContext,
    env: EnvReadyHandoff,
) -> EnvReadyHandoff:
    """靶场访问失败时检测 Docker；销毁/停止则重调度一次 env_ready。"""
    raw_url = env.target_url
    if not raw_url:
        return env

    compose_project = await _compose_project_for(ctx)
    if await _probe_lab_access(str(raw_url), compose_project=compose_project):
        return env

    runtime = await _lab_runtime_kind(ctx)
    if runtime not in _REVIVE_RUNTIMES:
        emit_phase(
            ctx,
            f"靶场探活失败但 Docker 状态为 {runtime}，不重调度 env_ready",
            phase="reproduce",
        )
        return env

    emit_phase(
        ctx,
        f"靶场不可达且 Docker 为 {runtime}，重新调度靶场就绪",
        phase="reproduce",
    )
    from app.contexts.agent.nodes.env_ready import EnvReadyNode

    revived = await EnvReadyNode().execute(ctx)
    new_url = (revived or {}).get("target_url") if isinstance(revived, dict) else None
    if not new_url:
        raise RuntimeError(
            f"靶场已{('销毁' if runtime == 'none' else '停止')}，"
            "重新调度 env_ready 后仍无 target_url"
        )
    ctx.updated_handoffs["env_ready"] = revived
    ctx.previous_outputs["env_ready"] = revived
    return EnvReadyHandoff.model_validate(revived)


class ReproduceNode:
    node_key = "reproduce"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input: ReproduceInput | None) -> ReproduceInput:
        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "reproduce",
            ctx.previous_outputs,
            vulnerability_description=ctx.vulnerability_description,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input: ReproduceInput | None = None) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import (
            rewrite_url_for_agent_container,
            run_ai_node_with_shape_retry,
        )

        if ctx.lab_id:
            from app.contexts.lab.service import LabService

            svc = LabService(ctx.db_session)
            await svc.touch(ctx.lab_id)
            await svc.align_runtime_status(ctx.lab_id)

        inp = self._resolve_input(ctx, node_input)
        env = await _ensure_lab_reachable(ctx, inp.env_ready)
        raw_url = env.target_url
        if not raw_url:
            emit_phase(
                ctx,
                "靶场未就绪，保留白盒代码可达结论",
                phase=self.node_key,
            )
            return {
                "verdict": "code_reachable",
                "reproduced": False,
                "attempts": [],
                "evidence": [],
                "degraded_reason": "env_unavailable",
            }
        target_url = rewrite_url_for_agent_container(str(raw_url)) or str(raw_url)

        src = inp.source
        input_json = {
            "source_path": src.workspace_path or workspace_repo_path(src.repo_dirname),
            "target_url": target_url,
            "initial_creds": env.initial_creds or {},
            "transport_shape": env.transport_shape or {},
            "compose_path": env.compose_path,
            "started_containers": env.started_containers or [],
            "audit": inp.audit.model_dump(exclude_none=True),
            "vulnerability_description": inp.vulnerability_description,
        }
        meta: dict[str, Any] = {}
        output = await run_ai_node_with_shape_retry(
            node_key="reproduce",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
            reproduce_scope=ctx.lab_id or ctx.task_id,
            meta_out=meta,
        )
        from app.contexts.agent.usage_ledger import record_node_usage

        await record_node_usage(ctx, "reproduce", meta)
        return output
