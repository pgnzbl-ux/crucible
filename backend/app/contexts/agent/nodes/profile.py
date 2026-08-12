"""节点 1 项目画像(代码)— 调 profile_detector 规则引擎。"""
from __future__ import annotations

from typing import Any

from app.contexts.agent.profile_detector import detect_profile

from .base import NodeContext


class ProfileNode:
    node_index = 1
    node_key = "profile"

    @property
    def is_ai(self) -> bool:
        return False

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        return detect_profile(ctx.source_path)
