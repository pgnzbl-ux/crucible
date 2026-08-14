"""Lab 的 docker compose 操作。Task 4 只提供 compose_start；down 留给 Task 5。"""
from __future__ import annotations

import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)


async def compose_start(compose_project: str) -> bool:
    """`docker compose -p {project} start`，成功返回 True。"""
    if not (compose_project or "").strip():
        return False
    cmd = ["docker", "compose", "-p", compose_project, "start"]
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:  # noqa: BLE001
        logger.warning("docker compose start 失败", exc_info=True)
        return False
    if result.returncode != 0:
        logger.warning(
            "docker compose start 失败: %s",
            (result.stderr or result.stdout or "")[:300],
        )
        return False
    return True
