"""节点 2 靶场就绪 — AI 出配方 + 代码执行 docker compose 的排障循环。

Phase 5：入口只编排；实现在子模块。对外公开 `EnvReadyNode`。
纯工具请从 `.ports` / `.health` / `.compose_host` / `.ai_recipe` 等导入。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.contexts.agent.target_url import host_advertise_ip, publish_target_url

from ..base import NodeContext
from . import ai_recipe, create_loop, reuse
from .create_loop import MAX_ATTEMPTS
from .events import _emit

logger = logging.getLogger(__name__)
LAB_WAIT_TIMEOUT_SECONDS = 1860


async def _resolve_project_id(ctx: NodeContext) -> str:
    if ctx.project_id:
        return ctx.project_id
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    try:
        project = await ProjectService(ProjectRepository(ctx.db_session)).upsert_by_git_url(
            git_url=ctx.project_address,
            owner_id=ctx.owner_id,
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("env_ready 无法确保 project_id，不能 acquire 靶场") from e
    project_id = getattr(project, "id", None)
    if not project_id:
        raise RuntimeError("env_ready 无法确保 project_id，不能 acquire 靶场")
    ctx.project_id = project_id
    return project_id


async def _wait_for_lab(
    ctx: NodeContext,
    *,
    owner_id: str,
    project_id: str,
    commit_sha: str,
) -> Any:
    from app.contexts.lab.service import LabService

    svc = LabService(ctx.db_session)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LAB_WAIT_TIMEOUT_SECONDS
    while loop.time() < deadline:
        _emit(ctx, "等待其他任务把靶场搭好")
        await asyncio.sleep(2)
        result = await svc.acquire(
            owner_id=owner_id,
            project_id=project_id,
            commit_sha=commit_sha,
            task_id=ctx.task_id,
        )
        if result.role != "wait":
            return result
    raise RuntimeError(
        f"等待共享靶场就绪超时（>{LAB_WAIT_TIMEOUT_SECONDS}s），请重试任务"
    )


class EnvReadyNode:
    node_key = "env_ready"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "env_ready",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp

        # Mock 模式:SDK 未启用时跳过真实 AI + docker compose,直接返回模拟靶场
        from app.core.config import get_settings
        if not get_settings().claude_agent_sdk_enabled:
            logger.info("[Mock] 节点 env_ready 返回模拟靶场(不执行 docker compose)")
            advertise = host_advertise_ip()
            return {
                "target_url": publish_target_url(8080, advertise),
                "compose_path": ".vuln-env/docker-compose.yml",
                "transport_shape": {
                    "protocol": "http",
                    "listener": "0.0.0.0:8080",
                    "tls_termination": "无",
                },
                "initial_creds": {"note": "[Mock] 未配置预设账号"},
                "started_containers": ["mock-app"],
            }

        sha = inp.source.commit_sha
        if not sha:
            raise RuntimeError("env_ready 缺少 source.commit_sha，不能 acquire 靶场")
        project_id = await _resolve_project_id(ctx)
        ctx.project_id = project_id
        if not ctx.owner_id:
            raise RuntimeError("env_ready 缺少 owner_id，不能 acquire 靶场")

        from app.contexts.lab.service import LabService

        result = await LabService(ctx.db_session).acquire(
            owner_id=ctx.owner_id,
            project_id=project_id,
            commit_sha=sha,
            task_id=ctx.task_id,
        )
        if result.role == "wait":
            result = await _wait_for_lab(
                ctx,
                owner_id=ctx.owner_id,
                project_id=project_id,
                commit_sha=sha,
            )
        if result.role == "reuse":
            _emit(ctx, f"复用靶场：{result.target_url}")
            svc = LabService(ctx.db_session)
            rebuilt = await reuse._reuse_or_rebuild_dead_lab(ctx, svc, result)
            if rebuilt is not None:
                return rebuilt
            creds = await ai_recipe._backfill_reused_initial_creds(ctx, svc, result)
            return reuse._reused_output(result, initial_creds=creds)
        if result.role == "start":
            return await reuse._start_lab(ctx, result)
        return await create_loop._create_lab(ctx, result)


__all__ = ["EnvReadyNode", "MAX_ATTEMPTS"]
