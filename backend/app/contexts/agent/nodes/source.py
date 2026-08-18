"""节点 0 源码获取(代码)— 查表命中则拉 MinIO，否则 git clone 到 {workdir}/{repo_dirname}。

失败必须抛错，不能标 completed。GitHub 网络/权限/空仓等错误由 clone 层分类。
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from .base import NodeContext, workspace_repo_path


class SourceNode:
    node_index = 0
    node_key = "source"

    @property
    def is_ai(self) -> bool:
        return False

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.project.repository import ProjectRepository
        from app.contexts.project.service import ProjectService
        from app.contexts.project.source_acquire import acquire_source

        cached = None
        svc = None
        owner_id = ctx.owner_id
        if ctx.db_session is not None:
            svc = ProjectService(ProjectRepository(ctx.db_session))
            if owner_id:
                try:
                    cached = await svc.find_cached_source(
                        ctx.project_address, ctx.project_ref, owner_id
                    )
                except ValueError:
                    cached = None

        result = await asyncio.to_thread(
            acquire_source,
            ctx.host_workdir,
            ctx.project_address,
            ctx.project_ref,
            cached=cached,
            owner_id=owner_id,
        )
        if not result.ok:
            err = result.error or "源码克隆失败: 未知原因"
            if "源码克隆失败" not in err:
                err = f"源码克隆失败: {err}"
            raise RuntimeError(err)

        if svc is not None:
            if owner_id and result.origin == "git":
                await svc.record_source_artifact(result, owner_id)
            if ctx.project_id:
                await svc.touch_cloned(ctx.project_id)

        out = asdict(result)
        out["source_path"] = result.project_path
        out["workspace_path"] = workspace_repo_path(result.repo_dirname)
        out["project_address"] = ctx.project_address
        out["project_ref"] = ctx.project_ref
        out["host_workdir"] = ctx.host_workdir
        return out
