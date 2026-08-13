"""节点 2 靶场就绪 — AI 出配方 + 代码执行 docker compose 的排障循环。

AI 在 agent-runner 内写/改 .vuln-env/Dockerfile + docker-compose.yml(文本,不碰 docker.sock);
worker 在 host 执行 docker compose up + 健康检查;失败回喂 AI(max 5 轮)。
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any

from .base import NodeContext

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def resolve_compose_host_path(compose_path: str, host_workdir: str) -> str:
    """把 AI 给出的 compose 路径解析为宿主机绝对路径。

    约定:配方写在 host_workdir/project/.vuln-env/。
    兼容容器内绝对路径 /workspace/project/... 以及误放在 host_workdir 根下的文件。
    """
    from pathlib import Path

    raw = (compose_path or ".vuln-env/docker-compose.yml").replace("\\", "/")
    host = Path(host_workdir)
    if raw.startswith("/workspace/"):
        rel = raw[len("/workspace/"):].lstrip("/")
        return str(host / rel)
    if raw.startswith("/") and os.path.exists(raw):
        return raw
    project_hit = host / "project" / raw
    root_hit = host / raw
    if project_hit.exists():
        return str(project_hit)
    if root_hit.exists():
        return str(root_hit)
    return str(project_hit)


async def docker_compose_up(compose_path: str, host_workdir: str) -> tuple[bool, str]:
    """在 host 执行 docker compose up -d --build,返回 (ok, error)。"""
    abs_path = resolve_compose_host_path(compose_path, host_workdir)
    abs_path = abs_path.replace("\\", "/")
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "compose", "-f", abs_path, "up", "-d", "--build"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or result.stdout)[:1000]
    except subprocess.TimeoutExpired:
        return False, "docker compose up 超时(>300s)"
    except Exception as e:  # noqa: BLE001
        return False, f"docker compose 异常: {e}"


async def health_check(port: int | None, host_workdir: str) -> tuple[bool, str]:
    """curl 探活,返回 (ok, target_url)。"""
    ports = [port] if port else [80, 3000, 5000, 8000, 8080, 8888]
    for p in ports:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["curl", "-sf", "-o", "/dev/null", f"http://localhost:{p}", "--max-time", "5"],
                capture_output=True,
            )
            if result.returncode == 0:
                return True, f"http://localhost:{p}"
        except Exception:  # noqa: BLE001
            continue
    return False, ""


async def collect_compose_logs(host_workdir: str, compose_path: str | None = None) -> str:
    """收 docker compose logs 给下轮 AI 排障。"""
    cmd = ["docker", "compose", "logs", "--tail=50"]
    cwd = host_workdir
    if compose_path:
        abs_path = resolve_compose_host_path(compose_path, host_workdir)
        cmd = ["docker", "compose", "-f", abs_path.replace("\\", "/"), "logs", "--tail=50"]
        cwd = None
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        return (result.stdout or result.stderr)[:2000]
    except Exception:  # noqa: BLE001
        return ""


async def docker_compose_down(host_workdir: str, compose_path: str | None = None) -> None:
    """任务结束拆掉靶场(best-effort)。"""
    from pathlib import Path

    abs_path = None
    if compose_path:
        abs_path = resolve_compose_host_path(compose_path, host_workdir)
    else:
        default = Path(host_workdir) / "project" / ".vuln-env" / "docker-compose.yml"
        if default.exists():
            abs_path = str(default)
    if not abs_path or not os.path.exists(abs_path):
        return
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "compose", "-f", abs_path.replace("\\", "/"), "down", "-v", "--remove-orphans"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:  # noqa: BLE001
        logger.warning("docker compose down 失败(best-effort)", exc_info=True)


async def run_ai_turn(
    ctx: NodeContext, attempt: int, prev_error: str | None
) -> dict[str, Any]:
    """调 AI(经 ai_runner)产出/修正 Dockerfile/compose。

    返回 {target_url?, compose_path, transport_shape?, initial_creds?, started_containers?}。
    """
    from app.contexts.agent.ai_runner import run_ai_node

    input_json = {
        "source_path": "/workspace/project",
        "profile": ctx.previous_outputs.get("profile", {}),
        "attempt": attempt,
        "previous_error": prev_error,
    }
    return await run_ai_node(
        node_key="env_ready",
        input_json=input_json,
        host_workdir=ctx.host_workdir,
        runner_env=ctx.runner_env,
        on_event=ctx.on_event,
    )


class EnvReadyNode:
    node_index = 2
    node_key = "env_ready"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        # Mock 模式:SDK 未启用时跳过真实 AI + docker compose,直接返回模拟靶场
        from app.core.config import get_settings
        if not get_settings().claude_agent_sdk_enabled:
            logger.info("[Mock] 节点 env_ready 返回模拟靶场(不执行 docker compose)")
            return {
                "target_url": "http://localhost:8080",
                "compose_path": ".vuln-env/docker-compose.yml",
                "transport_shape": {"protocol": "http", "listener": "0.0.0.0:8080", "tls_termination": "无"},
                "initial_creds": {},
                "started_containers": ["mock-app"],
            }

        profile = ctx.previous_outputs.get("profile", {})
        port = profile.get("port")
        last_error: str | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            # AI 产配方(首轮无 error,后续带 logs)
            recipe = await run_ai_turn(ctx, attempt, last_error)

            # worker 执行 docker compose
            compose_path = recipe.get("compose_path", ".vuln-env/docker-compose.yml")
            ok, err = await docker_compose_up(compose_path, ctx.host_workdir)
            if not ok:
                logs = await collect_compose_logs(ctx.host_workdir, compose_path)
                last_error = f"attempt {attempt} compose up 失败: {err}\n--- logs ---\n{logs}"
                logger.warning(f"节点 2 attempt {attempt} 失败: {err[:200]}")
                continue

            # 健康检查(AI 可能直接给了 target_url,优先用)
            target_url = recipe.get("target_url")
            if not target_url:
                ok, target_url = await health_check(port, ctx.host_workdir)
                if not ok:
                    last_error = f"attempt {attempt} 健康检查不过(port={port})"
                    continue

            return {
                "target_url": target_url,
                "compose_path": compose_path,
                "transport_shape": recipe.get("transport_shape", {"protocol": "http"}),
                "initial_creds": recipe.get("initial_creds", {}),
                "started_containers": recipe.get("started_containers", []),
            }

        raise RuntimeError(f"靶场搭建 {MAX_ATTEMPTS} 轮全失败: {(last_error or 'unknown')[:500]}")
