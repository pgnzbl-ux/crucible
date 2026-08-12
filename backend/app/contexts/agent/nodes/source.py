"""节点 0 源码获取(代码)— git clone 由编排器层在调 execute 前完成。

此节点的 execute 只是把 clone 结果包装成 output_json。
"""
from __future__ import annotations

from typing import Any

from .base import NodeContext


class SourceNode:
    node_index = 0
    node_key = "source"

    @property
    def is_ai(self) -> bool:
        return False

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        return {
            "source_path": ctx.source_path,
            "project_address": ctx.project_address,
            "project_ref": ctx.project_ref,
            "host_workdir": ctx.host_workdir,
        }
