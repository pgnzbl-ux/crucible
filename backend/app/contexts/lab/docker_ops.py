"""Lab 的 Docker Compose 与容器管理操作。"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess

logger = logging.getLogger(__name__)


async def _run(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
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
    """重建路径与创建路径共用：先策略校验，再以白名单 env 执行 compose。"""
    if not (project or "").strip():
        raise ValueError("compose project 不能为空")
    from .compose_policy import compose_subprocess_env, validate_compose_file

    validate_compose_file(compose_file, workdir)
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
            "--wait",
            "--wait-timeout",
            "300",
        ],
        cwd=workdir,
        timeout=600,
        env=compose_subprocess_env(),
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
            "{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}",
        ]
    )
    containers = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        name, state, status, ports, image = (
            line.split("\t") + ["", "", "", "", ""]
        )[:5]
        containers.append(
            {
                "name": name,
                "state": state,
                "status": status,
                "ports": ports,
                "image": image,
            }
        )
    return containers


async def list_published_ports(project: str) -> list[dict[str, str | int]]:
    """读取运行中 Compose 容器的实际端口绑定。

    不从 compose.yml 猜宿主端口：裸容器端口、变量、范围最终都以
    Docker NetworkSettings.Ports 为准。
    """
    if not (project or "").strip():
        raise ValueError("compose project 不能为空")
    listed = await _run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ]
    )
    ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not ids:
        return []
    inspected = await _run(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Ports}}",
            *ids,
        ]
    )
    result: list[dict[str, str | int]] = []
    for raw in inspected.stdout.splitlines():
        try:
            mappings = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(mappings, dict):
            continue
        for container_key, host_bindings in mappings.items():
            port_text, sep, protocol = str(container_key).partition("/")
            if not sep or not port_text.isdigit() or not isinstance(host_bindings, list):
                continue
            for binding in host_bindings:
                if not isinstance(binding, dict):
                    continue
                host_port = str(binding.get("HostPort") or "")
                if not host_port.isdigit():
                    continue
                result.append(
                    {
                        "host_ip": str(binding.get("HostIp") or "0.0.0.0"),
                        "host_port": int(host_port),
                        "container_port": int(port_text),
                        "protocol": (protocol or "tcp").lower(),
                    }
                )
    return result


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
