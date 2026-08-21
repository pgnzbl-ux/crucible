"""env_ready 进度事件。"""
from __future__ import annotations

from ..base import NodeContext


def _emit(ctx: NodeContext, message: str) -> None:
    if ctx.on_event:
        ctx.on_event({"type": "phase.updated", "phase": "env_ready", "message": message})
