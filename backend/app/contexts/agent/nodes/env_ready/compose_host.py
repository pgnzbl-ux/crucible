"""宿主 compose 路径 / 进度摘要 / up·down·logs。"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

COMPOSE_PROGRESS_INTERVAL = 2.0
COMPOSE_PROGRESS_MAX = 220
COMPOSE_UP_TIMEOUT = 600
_COMPOSE_URGENT = re.compile(r"error|failed|fatal|exception", re.I)
_COMPOSE_DIAG = re.compile(
    r"(?i)(\[error\]|error:|failed to solve|could not transfer|"
    r"dependencyresolution|npm err!|no such file|copy |"
    r"failed to execute|premature end|etimedout|econnreset|"
    r"address already in use|permission denied|security policy|"
    r"failed to)"
)
_COMPOSE_DIAG_NOISE = re.compile(
    r"(?i)to see the full stack trace|re-run maven|"
    r"for more information about the errors|"
    r"\[help 1\]|enable full debug logging"
)

def compose_progress_text(line: str, limit: int = COMPOSE_PROGRESS_MAX) -> str | None:
    text = " ".join((line or "").split())
    if not text:
        return None
    return text[:limit]


def summarize_compose_failure(text: str, *, limit: int = 1600) -> str:
    """抽出 COPY / Maven 传输失败等根因行，丢掉 Maven Help 与拉层进度。"""
    raw = text or ""
    if not raw.strip():
        return ""
    hits: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or _COMPOSE_DIAG_NOISE.search(stripped):
            continue
        if _COMPOSE_DIAG.search(stripped):
            clipped = stripped[:300]
            if not hits or hits[-1] != clipped:
                hits.append(clipped)
    if hits:
        joined = "\n".join(hits)
        return joined[:limit]
    return raw[-limit:]


class ComposeProgressThrottle:
    """把 docker compose 的刷屏日志收成可落库的进度句。"""

    def __init__(
        self,
        emit: Callable[[str], None],
        min_interval: float = COMPOSE_PROGRESS_INTERVAL,
    ) -> None:
        self._emit = emit
        self.min_interval = min_interval
        self._last = 0.0
        self._pending: str | None = None

    def push(self, line: str) -> None:
        text = compose_progress_text(line)
        if not text:
            return
        now = time.monotonic()
        urgent = bool(_COMPOSE_URGENT.search(text))
        first = self._last == 0.0
        if first or urgent or (now - self._last) >= self.min_interval:
            self._last = now
            self._pending = None
            self._emit(text)
        else:
            self._pending = text

    def flush(self) -> None:
        if self._pending:
            self._emit(self._pending)
            self._pending = None


def resolve_compose_host_path(
    compose_path: str, host_workdir: str, repo_dirname: str | None = None
) -> str:
    """把 AI 给出的 compose 路径解析为宿主机绝对路径。

    约定:任务 workspace 下配方在 host_workdir/{repo_dirname}/.vuln-env/。
    lab 目录（repo_dirname 为空）下配方在 host_workdir/.vuln-env/。
    兼容容器内绝对路径 /workspace/<repo>/... 以及误放在 host_workdir 根下的文件。
    """
    from pathlib import Path

    raw = (compose_path or ".vuln-env/docker-compose.yml").replace("\\", "/")
    host = Path(host_workdir)
    name = (repo_dirname or "").strip()
    if raw.startswith("/workspace/"):
        rel = raw[len("/workspace/"):].lstrip("/")
        return str(host / rel)
    if raw.startswith("/") and os.path.exists(raw):
        return raw
    if not name:
        return str(host / raw)
    repo_hit = host / name / raw
    root_hit = host / raw
    if repo_hit.exists():
        return str(repo_hit)
    if root_hit.exists():
        return str(root_hit)
    return str(repo_hit)


def is_docker_unavailable(err: str) -> bool:
    """docker 守护进程连不上或 docker 命令根本不存在，与配方构建失败区分。"""
    text = err or ""
    if "Cannot connect to the Docker daemon" in text:
        return True
    return "docker compose 异常:" in text


def _compose_project_args(lab_id: str | None) -> list[str]:
    if not lab_id:
        return []
    from app.contexts.agent.runtime_cleanup import lab_project_name

    return ["-p", lab_project_name(lab_id)]


def _compose_ident(*, lab_id: str | None = None, task_id: str | None = None) -> str | None:
    return lab_id if lab_id is not None else task_id


def repo_compose_rel(compose_path: str | None) -> str:
    """把 AI/缓存的 compose 路径收成仓库内相对路径（.vuln-env/...）。

    丢掉 /workspace/<repo>/ 前缀与误带的仓库名，就地执行时以
    {host_workdir}/{repo}/ 为基准解析。
    """
    raw = (compose_path or ".vuln-env/docker-compose.yml").replace("\\", "/")
    marker = ".vuln-env/"
    idx = raw.find(marker)
    if idx >= 0:
        return raw[idx:]
    name = raw.rsplit("/", 1)[-1] or "docker-compose.yml"
    return f".vuln-env/{name}"


def workspace_compose_rel(repo: str | None, compose_rel: str) -> str:
    """lab.compose_path 存 workspace 相对路径（含仓库名前缀），rebuild 同构解析。"""
    name = (repo or "").strip().strip("/\\")
    rel = compose_rel.replace("\\", "/").lstrip("/")
    if name and not rel.startswith(f"{name}/"):
        return f"{name}/{rel}"
    return rel



async def docker_compose_up(
    compose_path: str,
    host_workdir: str,
    repo_dirname: str | None = None,
    *,
    lab_id: str | None = None,
    task_id: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """在 host 就地执行 docker compose up -d --build，返回 (ok, error)。

    - 就地执行（2026-08-18）：compose 留在任务 workspace 的 {repo}/.vuln-env，
      不再拷进 labs/{id}。build.context 相对路径天然解析到仓库内模块，
      多模块项目可用；compose 以 -p crucible-lab-{id} 项目名隔离。
    - 保留 --build：AI 改完 Dockerfile 后若不重建会复用坏镜像，回喂轮次无效。
    - --progress plain 必须是 compose 全局旗标；挂在 up 后 Compose v5 会 unknown flag。
    - 同时设 BUILDKIT_PROGRESS=plain，无 TTY 时按行输出构建日志。
    """
    abs_path = resolve_compose_host_path(compose_path, host_workdir, repo_dirname)
    abs_path = abs_path.replace("\\", "/")
    from app.contexts.lab.compose_policy import (
        ComposePolicyError,
        validate_compose_file,
    )

    try:
        validate_compose_file(abs_path, host_workdir)
    except ComposePolicyError as exc:
        return False, f"docker compose 安全策略拒绝: {exc}"
    cmd = [
        "docker", "compose",
        "--progress", "plain",
        *_compose_project_args(_compose_ident(lab_id=lab_id, task_id=task_id)),
        "-f", abs_path, "up", "-d", "--build",
    ]

    def _run() -> tuple[int, str, bool]:
        env = os.environ.copy()
        env.setdefault("BUILDKIT_PROGRESS", "plain")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )

        def _forward(text: str) -> None:
            logger.info("docker compose: %s", text)
            if on_progress:
                on_progress(text)

        throttle = ComposeProgressThrottle(_forward)
        chunks: list[str] = []
        timed_out = False

        def _kill() -> None:
            nonlocal timed_out
            timed_out = True
            proc.kill()

        timer = threading.Timer(COMPOSE_UP_TIMEOUT, _kill)
        timer.daemon = True
        timer.start()
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    chunks.append(line)
                    throttle.push(line)
            rc = proc.wait()
        finally:
            timer.cancel()
            throttle.flush()
        return rc, "".join(chunks), timed_out

    try:
        rc, out, timed_out = await asyncio.to_thread(_run)
        if timed_out:
            return False, f"docker compose up 超时(>{COMPOSE_UP_TIMEOUT}s)"
        if rc == 0:
            return True, ""
        return False, summarize_compose_failure(out)
    except Exception as e:  # noqa: BLE001
        return False, f"docker compose 异常: {e}"


async def collect_compose_logs(
    host_workdir: str,
    compose_path: str | None = None,
    repo_dirname: str | None = None,
    *,
    lab_id: str | None = None,
    task_id: str | None = None,
) -> str:
    """收 docker compose logs 给下轮 AI 排障。"""
    p_args = _compose_project_args(_compose_ident(lab_id=lab_id, task_id=task_id))
    cmd = ["docker", "compose", *p_args, "logs", "--tail=50"]
    cwd = host_workdir
    if compose_path:
        abs_path = resolve_compose_host_path(compose_path, host_workdir, repo_dirname)
        cmd = ["docker", "compose", *p_args, "-f", abs_path.replace("\\", "/"), "logs", "--tail=50"]
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


async def docker_compose_down(
    host_workdir: str,
    compose_path: str | None = None,
    repo_dirname: str | None = None,
    *,
    lab_id: str | None = None,
    task_id: str | None = None,
) -> None:
    """创建失败回滚时拆本 Lab 的 compose（best-effort）。优先按项目名，不依赖 yaml 还在。"""
    from pathlib import Path

    cmds: list[list[str]] = []
    p_args = _compose_project_args(_compose_ident(lab_id=lab_id, task_id=task_id))
    if p_args:
        cmds.append(["docker", "compose", *p_args, "down", "-v", "--remove-orphans"])

    abs_path = None
    if compose_path:
        abs_path = resolve_compose_host_path(compose_path, host_workdir, repo_dirname)
    elif host_workdir:
        matches = list(Path(host_workdir).glob("*/.vuln-env/docker-compose.yml"))
        if matches:
            abs_path = str(matches[0])
    if abs_path and os.path.exists(abs_path):
        cmds.append(
            [
                "docker", "compose", *p_args, "-f", abs_path.replace("\\", "/"),
                "down", "-v", "--remove-orphans",
            ]
        )
    if not cmds:
        return
    for cmd in cmds:
        try:
            await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True, text=True, timeout=120,
            )
        except Exception:  # noqa: BLE001
            logger.warning("docker compose down 失败(best-effort)", exc_info=True)

async def up_with_logs(
    compose_rel: str,
    host_workdir: str,
    repo_dirname: str | None,
    *,
    lab_id: str | None,
    on_progress=None,
) -> tuple[bool, str]:
    """执行 compose up；失败时附带 logs 摘要。不 down——由调用方决定。"""
    ok, err = await docker_compose_up(
        compose_rel,
        host_workdir,
        repo_dirname,
        lab_id=lab_id,
        on_progress=on_progress,
    )
    if ok:
        return True, ""
    logs = await collect_compose_logs(
        host_workdir, compose_rel, repo_dirname, lab_id=lab_id
    )
    detail = (
        f"compose up 失败: {err}" + "\n--- logs ---\n" + summarize_compose_failure(logs)
    )
    return False, detail


async def fail_and_down(
    host_workdir: str,
    compose_rel: str,
    repo_dirname: str | None,
    *,
    lab_id: str | None,
    detail: str,
) -> str:
    """best-effort down，原样返回 detail。"""
    await docker_compose_down(
        host_workdir, compose_rel, repo_dirname, lab_id=lab_id
    )
    return detail


async def probe_mapped_ports(
    web_ports: list[int],
    *,
    container_ports: list[int] | None = None,
    host_workdir: str,
    compose_rel: str,
    repo_dirname: str | None,
    lab_id: str | None,
) -> tuple[bool, int | None, str, str]:
    """探活映射口。失败带 logs；成功返回 live_port/scheme。"""
    from . import health as health_mod

    ok, live_port, scheme = await health_mod.health_check(
        web_ports, container_ports=container_ports
    )
    if ok and live_port is not None:
        return True, live_port, scheme, ""
    logs = await collect_compose_logs(
        host_workdir, compose_rel, repo_dirname, lab_id=lab_id
    )
    fail = health_mod._health_fail_detail()
    detail = (
        f"健康检查不过(mapped_ports={web_ports})" + "\n" + fail + "\n--- logs ---\n"
        + summarize_compose_failure(logs)
    )
    return False, None, "http", detail

