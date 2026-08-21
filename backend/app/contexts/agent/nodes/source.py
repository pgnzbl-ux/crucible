"""节点 0 源码获取(代码)— Git：查表命中则拉 MinIO，否则 clone；上传包：只从 MinIO 解开。

失败必须抛错，不能标 completed。GitHub 网络/权限/空仓等错误由 clone 层分类。
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from app.contexts.agent.contracts import SourceInput

from .base import NodeContext, workspace_repo_path


class SourceNode:
    node_index = 0
    node_key = "source"

    @property
    def is_ai(self) -> bool:
        return False

    def _resolve_input(self, ctx: NodeContext, node_input: SourceInput | None) -> SourceInput:
        if node_input is not None:
            return node_input
        return SourceInput(
            project_address=ctx.project_address,
            project_ref=ctx.project_ref,
            project_ref_type=ctx.project_ref_type,
            clone_depth=ctx.clone_depth,
            source_type=ctx.source_type or "git",
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input: SourceInput | None = None) -> dict[str, Any]:
        inp = self._resolve_input(ctx, node_input)
        # 与 Input 对齐，供 _acquire_* 走 ctx 字段
        ctx.project_address = inp.project_address
        ctx.project_ref = inp.project_ref
        ctx.project_ref_type = inp.project_ref_type
        ctx.clone_depth = inp.clone_depth
        ctx.source_type = inp.source_type
        ctx.host_workdir = inp.host_workdir
        ctx.source_path = inp.source_path

        source_type = inp.source_type or "git"
        if source_type == "local_upload":
            result = await self._acquire_upload(ctx)
            fail_prefix = "源码解包失败"
        else:
            result = await self._acquire_git(ctx)
            fail_prefix = "源码克隆失败"

        if not result.ok:
            err = result.error or f"{fail_prefix}: 未知原因"
            if fail_prefix not in err:
                err = f"{fail_prefix}: {err}"
            raise RuntimeError(err)

        from app.contexts.project.repository import ProjectRepository
        from app.contexts.project.service import ProjectService

        if ctx.db_session is not None:
            svc = ProjectService(ProjectRepository(ctx.db_session))
            if ctx.owner_id and result.origin == "git":
                await svc.record_source_artifact(result, ctx.owner_id)
            if ctx.project_id:
                await svc.touch_cloned(ctx.project_id)

        out = asdict(result)
        out["source_path"] = result.project_path
        out["workspace_path"] = workspace_repo_path(result.repo_dirname)
        out["project_address"] = ctx.project_address
        out["project_ref"] = ctx.project_ref
        out["host_workdir"] = ctx.host_workdir
        out["source_type"] = source_type
        return out

    async def _acquire_git(self, ctx: NodeContext):
        from app.contexts.project.git_url import parse_git_url
        from app.contexts.project.repository import ProjectRepository
        from app.contexts.project.service import ProjectService
        from app.contexts.project.source_acquire import CachedSource, acquire_source

        cached = None
        cached_by_sha_fn = None
        owner_id = ctx.owner_id
        if ctx.db_session is not None and owner_id:
            svc = ProjectService(ProjectRepository(ctx.db_session))
            try:
                parsed = parse_git_url(ctx.project_address)
                cached = await svc.find_cached_source(
                    ctx.project_address,
                    ctx.project_ref,
                    owner_id,
                    ref_type=ctx.project_ref_type,
                )
                index: dict[str, CachedSource] = {
                    item.commit_sha.lower(): item
                    for item in await svc.list_cached_sources(
                        owner_id, parsed.host, parsed.project_key
                    )
                }

                def _by_sha(sha: str, _idx: dict[str, CachedSource] = index) -> CachedSource | None:
                    needle = (sha or "").lower()
                    if not needle:
                        return None
                    for stored, src in _idx.items():
                        if stored.startswith(needle) or needle.startswith(stored):
                            return src
                    return None

                cached_by_sha_fn = _by_sha if index else None
            except ValueError:
                cached = None

        return await asyncio.to_thread(
            acquire_source,
            ctx.host_workdir,
            ctx.project_address,
            ctx.project_ref,
            ref_type_hint=ctx.project_ref_type,
            clone_depth=ctx.clone_depth,
            cached=cached,
            owner_id=owner_id,
            cached_by_sha_fn=cached_by_sha_fn,
        )

    async def _acquire_upload(self, ctx: NodeContext):
        from app.contexts.project.repository import ProjectRepository
        from app.contexts.project.service import ProjectService
        from app.contexts.project.source_acquire import acquire_uploaded_source

        cached = None
        if ctx.db_session is not None and ctx.owner_id:
            svc = ProjectService(ProjectRepository(ctx.db_session))
            cached = await svc.find_cached_source(
                ctx.project_address,
                None,
                ctx.owner_id,
                ref_type="upload",
            )
        return await asyncio.to_thread(
            acquire_uploaded_source,
            ctx.host_workdir,
            cached=cached,
        )
