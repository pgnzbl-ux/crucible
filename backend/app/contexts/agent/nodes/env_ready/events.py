"""env_ready 进度事件 + 取消探测。"""
from __future__ import annotations

from ..base import NodeContext, task_run_cancelled


def _emit(ctx: NodeContext, message: str) -> None:
    if ctx.on_event:
        ctx.on_event({"type": "phase.updated", "phase": "env_ready", "message": message})


async def raise_if_cancelled(ctx: NodeContext) -> None:
    """env_ready 长循环/探活的取消检查点：取消即抛错。

    编排器收到节点异常后会先复查库内取消状态，把 NodeRun 收敛为
    cancelled（而不是 failed），所以这里抛 RuntimeError 即可。
    """
    if ctx.db_session is None:
        return
    if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
        raise RuntimeError("任务已取消，中止 env_ready")


def cancel_check(ctx: NodeContext):
    """health_check 重试循环按次回调的取消探测（异步，不共享他人会话）。"""
    async def _check() -> bool:
        if ctx.db_session is None:
            return False
        return await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id)

    return _check
