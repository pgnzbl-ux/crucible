"""env_ready Lab 复用 / start / 死靶场重建。"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..base import NodeContext
from . import ai_recipe, events, health


def _reused_output(
    result: Any,
    *,
    initial_creds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "target_url": result.target_url,
        "compose_path": result.compose_path or ".vuln-env/docker-compose.yml",
        "transport_shape": result.transport_shape or {"protocol": "http"},
        "initial_creds": initial_creds or result.initial_creds or {},
        "started_containers": [],
        "reused": True,
    }


def _reused_lab_alive(result: Any) -> bool:
    """复用前快探：DB 说 ready 不代表应用进程还活着。

    容器在跑但应用崩溃（Fatal/缺表）时，reproduce 会拿死靶标白烧一整个节点。
    单次探测不重试（快失败，死靶场降级重建的成本远低于白跑 reproduce）。
    GET 首页正文，崩溃页不算活着。target_url host 可能是对外 IP，本机视角换成 127.0.0.1 探。
    """
    raw = str(result.target_url or "")
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    if not parsed.port:
        return False
    scheme = parsed.scheme or "http"
    return health._http_alive(f"{scheme}://127.0.0.1:{parsed.port}")


async def _live_started_containers(compose_project: str | None, fallback: Any) -> list[str]:
    """以 docker ps 实际容器名为准，AI 提交的名单只是兜底。"""
    names: list[str] = []
    if compose_project:
        try:
            from app.contexts.lab.docker_ops import list_containers

            items = await list_containers(compose_project)
            names = [
                str(item.get("name"))
                for item in items
                if isinstance(item, dict) and item.get("name")
            ]
        except Exception:  # noqa: BLE001
            names = []
    if names:
        return names
    if isinstance(fallback, list):
        return [str(x) for x in fallback if x]
    return []


async def _start_lab(ctx: NodeContext, result: Any) -> dict[str, Any]:
    from app.contexts.lab.docker_ops import compose_start, list_containers
    from app.contexts.lab.service import LabService

    # 避免与 create_loop 循环 import：函数内再取
    from .create_loop import _create_lab

    svc = LabService(ctx.db_session)
    events._emit(ctx, f"启动已停止的靶场 {result.compose_project}")
    ok = await compose_start(result.compose_project)
    if not ok:
        if await list_containers(result.compose_project):
            await svc.mark_failed(result.lab_id, "compose start 失败")
            raise RuntimeError("靶场 compose start 失败")
        events._emit(ctx, "靶场容器已不存在，改为重新创建")
        await svc.reclaim_gone_runtime(result.lab_id, ctx.task_id)
        return await _create_lab(ctx, result)
    rebuilt = await _reuse_or_rebuild_dead_lab(ctx, svc, result)
    if rebuilt is not None:
        return rebuilt
    creds = await ai_recipe._backfill_reused_initial_creds(ctx, svc, result)
    if result.initial_creds:
        await svc.mark_ready(
            result.lab_id,
            target_url=result.target_url or "",
            compose_path=result.compose_path or ".vuln-env/docker-compose.yml",
            transport_shape=result.transport_shape or {"protocol": "http"},
            initial_creds=creds,
        )
    return _reused_output(result, initial_creds=creds)


async def _reuse_or_rebuild_dead_lab(
    ctx: NodeContext, svc: Any, result: Any
) -> dict[str, Any] | None:
    """复用前快探；死靶场标 failed → reclaim → 缓存配方重建（不烧 AI）。

    返回 None 表示靶场活着，继续复用流程。
    """
    from .create_loop import _create_lab

    if _reused_lab_alive(result):
        return None
    events._emit(ctx, "复用靶场探活失败（应用可能已死），降级重建")
    await svc.mark_failed(result.lab_id, "复用前探活失败：应用不响应")
    await svc.reclaim_gone_runtime(result.lab_id, ctx.task_id)
    return await _create_lab(ctx, result)
