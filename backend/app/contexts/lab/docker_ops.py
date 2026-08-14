"""Lab 的 Docker Compose 与容器管理操作。"""
from __future__ import annotations

import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)


async def _run(cmd: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": 120,
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        **kwargs,
    )
    if result.returncode != 0:
        logger.error(
            "Docker 命令失败 cmd=%s: %s",
            cmd,
            (result.stderr or result.stdout or "")[:300],
        )
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


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


async def compose_stop(project: str) -> None:
    if not (project or "").strip():
        raise ValueError("compose project 不能为空")
    await _run(["docker", "compose", "-p", project, "stop"])


async def compose_up_build(project: str, compose_file: str, workdir: str) -> None:
    if not (project or "").strip():
        raise ValueError("compose project 不能为空")
    await _run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            compose_file,
            "up",
            "-d",
            "--build",
        ],
        cwd=workdir,
    )


async def list_containers(project: str) -> list[dict[str, str]]:
    if not (project or "").strip():
        raise ValueError("compose project 不能为空")
    result = await _run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}",
        ]
    )
    containers = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        name, status, ports, image = (line.split("\t") + ["", "", "", ""])[:4]
        containers.append(
            {"name": name, "status": status, "ports": ports, "image": image}
        )
    return containers


async def assert_container_in_project(name: str, project: str) -> None:
    from .errors import LabNotFoundError

    if not any(item["name"] == name for item in await list_containers(project)):
        raise LabNotFoundError(f"容器不存在: {name}")


async def _container_command(command: str, name: str, project: str) -> None:
    await assert_container_in_project(name, project)
    cmd = ["docker", command]
    if command == "rm":
        cmd.append("-f")
    cmd.append(name)
    await _run(cmd)


async def container_stop(name: str, project: str) -> None:
    await _container_command("stop", name, project)


async def container_start(name: str, project: str) -> None:
    await _container_command("start", name, project)


async def container_restart(name: str, project: str) -> None:
    await _container_command("restart", name, project)


async def container_rm(name: str, project: str) -> None:
    await _container_command("rm", name, project)
