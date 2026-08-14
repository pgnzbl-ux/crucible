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


async def compose_down(project: str) -> None:
    """`docker compose -p {project} down -v --remove-orphans`。"""
    if not (project or "").strip():
        raise ValueError("compose project 不能为空")
    cmd = [
        "docker",
        "compose",
        "-p",
        project,
        "down",
        "-v",
        "--remove-orphans",
    ]
    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        logger.error(
            "docker compose down 失败 project=%s: %s",
            project,
            (result.stderr or result.stdout or "")[:300],
        )
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr,
        )
