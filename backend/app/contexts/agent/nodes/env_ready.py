"""节点 2 靶场就绪 — AI 出配方 + 代码执行 docker compose 的排障循环。

AI 在 agent-runner 内写/改 .vuln-env/Dockerfile + docker-compose.yml(文本,不碰 docker.sock);
worker 在 host 执行 docker compose up + 健康检查;失败回喂 AI(max 5 轮)。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from app.contexts.agent.target_url import (
    host_advertise_ip,
    publish_target_url,
)

from .base import NodeContext, repo_dirname_from_outputs, workspace_repo_path

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
COMPOSE_UP_TIMEOUT = 600
HEALTH_RETRIES = 8
HEALTH_RETRY_SECONDS = 2
COMPOSE_PROGRESS_INTERVAL = 2.0
COMPOSE_PROGRESS_MAX = 220
_COMPOSE_URGENT = re.compile(r"error|failed|fatal|exception", re.I)
_SIDECAR_CONTAINER_PORTS = {3306, 5432, 6379, 27017, 5672, 1433, 9200, 11211}
_SHORT_PORT = re.compile(
    r"^(?:(?:\d{1,3}\.){3}\d{1,3}:)?(\d+):(\d+)(?:/(?:tcp|udp))?$", re.I
)
_BARE_PORT = re.compile(r"^(\d+)(?:/(?:tcp|udp))?$")


def compose_progress_text(line: str, limit: int = COMPOSE_PROGRESS_MAX) -> str | None:
    text = " ".join((line or "").split())
    if not text:
        return None
    return text[:limit]


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


def parse_compose_port_mappings(text: str) -> list[tuple[int, int]]:
    """从 compose 文本抽出 (宿主机端口, 容器端口)。"""
    mappings: list[tuple[int, int]] = []
    in_ports = False
    ports_indent = 0
    pending_target: int | None = None
    pending_published: int | None = None

    def flush_long() -> None:
        nonlocal pending_target, pending_published
        if pending_published is not None and pending_target is not None:
            mappings.append((pending_published, pending_target))
        elif pending_target is not None and pending_published is None:
            mappings.append((pending_target, pending_target))
        pending_target = None
        pending_published = None

    for raw in (text or "").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if stripped.startswith("ports:"):
            flush_long()
            in_ports = True
            ports_indent = indent
            continue
        if in_ports and indent <= ports_indent and not stripped.startswith("-"):
            flush_long()
            in_ports = False
        if not in_ports:
            continue
        target_m = re.match(r"target:\s*(\d+)\s*$", stripped)
        if target_m:
            pending_target = int(target_m.group(1))
            continue
        published_m = re.match(r"published:\s*[\"']?(\d+)[\"']?\s*$", stripped)
        if published_m:
            pending_published = int(published_m.group(1))
            continue
        if stripped.startswith("-"):
            flush_long()
            rest = stripped[1:].strip().strip("\"'")
            target_inline = re.match(r"target:\s*(\d+)\s*$", rest)
            if target_inline:
                pending_target = int(target_inline.group(1))
                continue
            published_inline = re.match(r"published:\s*[\"']?(\d+)[\"']?\s*$", rest)
            if published_inline:
                pending_published = int(published_inline.group(1))
                continue
            short = _SHORT_PORT.match(rest)
            if short:
                mappings.append((int(short.group(1)), int(short.group(2))))
                continue
            bare = _BARE_PORT.match(rest)
            if bare:
                port = int(bare.group(1))
                mappings.append((port, port))
    flush_long()
    return mappings


def web_host_ports(mappings: list[tuple[int, int]]) -> list[int]:
    """只保留映射到宿主机的 Web 入口；数据库/MQ 端口不算靶场地址。"""
    seen: set[int] = set()
    ports: list[int] = []
    for host_port, container_port in mappings:
        if container_port in _SIDECAR_CONTAINER_PORTS:
            continue
        if host_port in seen:
            continue
        seen.add(host_port)
        ports.append(host_port)
    return ports


_SHORT_HOST_IN_LINE = re.compile(
    r"(?:(?:\d{1,3}\.){3}\d{1,3}:)?(?P<host>\d+):\d+(?:/(?:tcp|udp))?",
    re.I,
)
_PUBLISHED_HOST_IN_LINE = re.compile(
    r"(published:\s*[\"']?)(\d+)",
    re.I,
)


def _pick_free_host_port(start: int, taken: set[int]) -> int | None:
    candidate = start
    while candidate in taken:
        candidate += 1
        if candidate > 65535:
            return None
    return candidate


def _apply_host_port_replacements(text: str, replacements: dict[int, int]) -> str:
    """只改 ports 段里短语法 HOST:CONTAINER 的宿主侧，以及 published: 行。"""
    lines: list[str] = []
    in_ports = False
    ports_indent = 0
    for raw in (text or "").splitlines(keepends=True):
        body = raw[:-2] if raw.endswith("\r\n") else (raw[:-1] if raw.endswith("\n") else raw)
        nl = raw[len(body):]
        stripped = body.strip()
        indent = len(body) - len(body.lstrip(" "))
        if stripped.startswith("ports:"):
            in_ports = True
            ports_indent = indent
            lines.append(raw)
            continue
        if in_ports and stripped and indent <= ports_indent and not stripped.startswith("-"):
            in_ports = False
        if not in_ports or not stripped or stripped.startswith("#"):
            lines.append(raw)
            continue
        if "published:" in stripped:
            pub = _PUBLISHED_HOST_IN_LINE.search(body)
            if pub:
                port = int(pub.group(2))
                if port in replacements:
                    body = (
                        body[: pub.start(2)]
                        + str(replacements[port])
                        + body[pub.end(2) :]
                    )
                    lines.append(body + nl)
                    continue
        else:
            short = _SHORT_HOST_IN_LINE.search(body)
            if short:
                host = int(short.group("host"))
                if host in replacements:
                    body = (
                        body[: short.start("host")]
                        + str(replacements[host])
                        + body[short.end("host") :]
                    )
                    lines.append(body + nl)
                    continue
        lines.append(raw)
    return "".join(lines)


def rewrite_compose_host_ports(text: str, occupied: set[int]) -> str | None:
    """冲突的 Web 宿主口改为空闲口；只改 host 侧。无 Web 映射返回 None。"""
    mappings = parse_compose_port_mappings(text)
    web_ports = web_host_ports(mappings)
    if not web_ports:
        return None
    occupied_set = set(occupied)
    conflicts = [p for p in web_ports if p in occupied_set]
    if not conflicts:
        return text

    taken = set(occupied_set)
    taken.update(p for p in web_ports if p not in occupied_set)
    replacements: dict[int, int] = {}
    for host in conflicts:
        picked = _pick_free_host_port(host + 1, taken)
        if picked is None:
            return None
        replacements[host] = picked
        taken.add(picked)
    return _apply_host_port_replacements(text, replacements)


def is_docker_unavailable(err: str) -> bool:
    """docker 守护进程连不上或 docker 命令根本不存在，与配方构建失败区分。"""
    text = err or ""
    if "Cannot connect to the Docker daemon" in text:
        return True
    return "docker compose 异常:" in text


def load_web_host_ports(compose_abs: str) -> list[int]:
    from pathlib import Path

    try:
        text = Path(compose_abs).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return web_host_ports(parse_compose_port_mappings(text))


_PUBLISHED_HOST_PORT = re.compile(r"(\d+)->")


def parse_docker_ps_published_ports(
    text: str, *, exclude_project: str | None = None
) -> set[int]:
    """从 `docker ps --format '{{.Label project}}\\t{{.Ports}}'` 抽出宿主已映射端口。"""
    occupied: set[int] = set()
    skip = (exclude_project or "").strip().lower()
    for raw in (text or "").splitlines():
        project, sep, ports = raw.partition("\t")
        if not sep:
            ports = raw
            project = ""
        if skip and project.strip().lower() == skip:
            continue
        for match in _PUBLISHED_HOST_PORT.finditer(ports):
            occupied.add(int(match.group(1)))
    return occupied


def list_docker_occupied_host_ports(*, exclude_project: str | None = None) -> set[int]:
    """查当前运行中容器已 publish 到宿主的端口。查失败则当无占用，交给 compose up 暴露。"""
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--format",
                '{{.Label "com.docker.compose.project"}}\t{{.Ports}}',
            ],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:  # noqa: BLE001
        logger.warning("docker ps 查占用端口失败", exc_info=True)
        return set()
    if result.returncode != 0:
        logger.warning(
            "docker ps 查占用端口失败: %s", (result.stderr or result.stdout)[:300]
        )
        return set()
    return parse_docker_ps_published_ports(result.stdout, exclude_project=exclude_project)


def _compose_project_args(lab_id: str | None) -> list[str]:
    if not lab_id:
        return []
    from app.contexts.agent.runtime_cleanup import lab_project_name

    return ["-p", lab_project_name(lab_id)]


def _compose_ident(*, lab_id: str | None = None, task_id: str | None = None) -> str | None:
    return lab_id if lab_id is not None else task_id


def lab_recipe_compose_path(compose_path: str | None) -> str:
    """把 AI 的 compose 路径收成 lab 目录下的相对路径（.vuln-env/...）。

    丢掉 /workspace/<repo>/ 前缀；配方已拷到 labs/{id}/.vuln-env，不能再拼一层仓库名。
    """
    raw = (compose_path or ".vuln-env/docker-compose.yml").replace("\\", "/")
    marker = ".vuln-env/"
    idx = raw.find(marker)
    if idx >= 0:
        return raw[idx:]
    name = raw.rsplit("/", 1)[-1] or "docker-compose.yml"
    return f".vuln-env/{name}"


def sync_recipe_to_lab(src_repo_dir: str, lab_workdir: str) -> None:
    """把任务 workspace 里的 .vuln-env 拷到 lab 目录（存在则覆盖）。"""
    from pathlib import Path

    src = Path(src_repo_dir) / ".vuln-env"
    dst = Path(lab_workdir) / ".vuln-env"
    if not src.is_dir():
        raise FileNotFoundError(f"配方目录不存在: {src}")
    Path(lab_workdir).mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


async def docker_compose_up(
    compose_path: str,
    host_workdir: str,
    repo_dirname: str | None = None,
    *,
    lab_id: str | None = None,
    task_id: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """在 host 执行 docker compose up -d --build,返回 (ok, error)。

    无 TTY 时必须 --progress plain，否则 BuildKit 只刷 \\r，前端看起来像卡死。
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
        "docker", "compose", *_compose_project_args(_compose_ident(lab_id=lab_id, task_id=task_id)),
        "-f", abs_path, "up", "-d", "--build", "--progress", "plain",
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
        return False, out[-1000:]
    except Exception as e:  # noqa: BLE001
        return False, f"docker compose 异常: {e}"


def _http_alive(url: str, timeout: float = 5) -> bool:
    """探活：能连上且不是 5xx 即视为靶场起来了（401/404 也算）。"""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            return 200 <= int(code) < 500
    except urllib.error.HTTPError as e:
        return 400 <= int(e.code) < 500
    except Exception:  # noqa: BLE001
        return False


async def health_check(ports: list[int] | None, extra_ports: list[int] | None = None) -> tuple[bool, int | None]:
    """只对 compose 映射到宿主机的 Web 端口探活，不扫本机 80/8080 等常用口。"""
    ordered: list[int] = []
    seen: set[int] = set()
    for p in list(ports or []) + list(extra_ports or []):
        port = int(p)
        if port in seen:
            continue
        seen.add(port)
        ordered.append(port)
    if not ordered:
        return False, None

    primary = ordered[0]
    for _ in range(HEALTH_RETRIES):
        if _http_alive(f"http://127.0.0.1:{primary}"):
            return True, primary
        await asyncio.sleep(HEALTH_RETRY_SECONDS)
    for p in ordered[1:]:
        if _http_alive(f"http://127.0.0.1:{p}"):
            return True, p
    return False, None


def _emit(ctx: NodeContext, message: str) -> None:
    if ctx.on_event:
        ctx.on_event({"type": "phase.updated", "phase": "env_ready", "message": message})


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


async def run_ai_turn(
    ctx: NodeContext,
    attempt: int,
    prev_error: str | None,
    occupied_host_ports: list[int] | None = None,
) -> dict[str, Any]:
    """调 AI(经 ai_runner)产出/修正 Dockerfile/compose。

    返回 {target_url?, compose_path, transport_shape?, initial_creds?, started_containers?}。
    """
    from app.contexts.agent.ai_runner import run_ai_node

    src = ctx.previous_outputs.get("source", {})
    repo = src.get("repo_dirname") or repo_dirname_from_outputs(ctx.previous_outputs)
    input_json = {
        "source_path": src.get("workspace_path") or workspace_repo_path(repo),
        "profile": ctx.previous_outputs.get("profile", {}),
        "attempt": attempt,
        "previous_error": prev_error,
        "occupied_host_ports": list(occupied_host_ports or []),
    }
    return await run_ai_node(
        node_key="env_ready",
        input_json=input_json,
        host_workdir=ctx.host_workdir,
        runner_env=ctx.runner_env,
        on_event=ctx.on_event,
        task_id=ctx.task_id,
    )


def _reused_output(result: Any) -> dict[str, Any]:
    return {
        "target_url": result.target_url,
        "compose_path": result.compose_path or ".vuln-env/docker-compose.yml",
        "transport_shape": result.transport_shape or {"protocol": "http"},
        "initial_creds": result.initial_creds or {},
        "started_containers": [],
        "reused": True,
    }


async def _resolve_project_id(ctx: NodeContext) -> str:
    if ctx.project_id:
        return ctx.project_id
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    try:
        project = await ProjectService(ProjectRepository(ctx.db_session)).upsert_by_git_url(
            git_url=ctx.project_address,
            owner_id=ctx.owner_id,
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("env_ready 无法确保 project_id，不能 acquire 靶场") from e
    project_id = getattr(project, "id", None)
    if not project_id:
        raise RuntimeError("env_ready 无法确保 project_id，不能 acquire 靶场")
    ctx.project_id = project_id
    return project_id


def _commit_sha_from(ctx: NodeContext) -> str:
    return str((ctx.previous_outputs.get("source") or {}).get("commit_sha") or "")


def _exclude_compose_project(lab_id: str | None) -> str | None:
    """与 lab_project_name 同形，避免 import runtime_cleanup 拉起 Docker client。"""
    if not lab_id:
        return None
    return f"crucible-lab-{str(lab_id).lower()}"


async def _upload_then_mark_ready(
    ctx: NodeContext,
    svc: Any,
    result: Any,
    *,
    commit_sha: str,
    lab_compose: str,
    output: dict[str, Any],
) -> None:
    try:
        await svc.upload_recipe(
            owner_id=ctx.owner_id,
            project_id=ctx.project_id or "",
            commit_sha=commit_sha,
            lab_workdir=result.workdir,
            compose_path=lab_compose,
            transport_shape=output["transport_shape"],
            initial_creds=output["initial_creds"],
            started_containers=output.get("started_containers") or [],
        )
        await svc.mark_ready(
            result.lab_id,
            target_url=output["target_url"],
            compose_path=lab_compose,
            transport_shape=output["transport_shape"],
            initial_creds=output["initial_creds"],
        )
    except Exception:
        await docker_compose_down(
            result.workdir,
            lab_compose,
            None,
            lab_id=result.lab_id,
        )
        raise


async def _try_cached_recipe(
    ctx: NodeContext,
    svc: Any,
    result: Any,
    *,
    commit_sha: str,
    exclude_project: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """MinIO 命中后改口、up、探活。成功产出含 reused；docker 不可用则抛；失败 (None, last_error)。"""
    from pathlib import Path

    hit = await svc.download_recipe(
        owner_id=ctx.owner_id or "",
        project_id=ctx.project_id or "",
        commit_sha=commit_sha,
        dest_workdir=result.workdir,
    )
    if not hit:
        return None, None

    lab_compose = lab_recipe_compose_path(hit.get("compose_path") if isinstance(hit, dict) else None)
    compose_file = Path(result.workdir) / lab_compose
    if not compose_file.is_file():
        return None, f"缓存配方缺少 compose 文件: {lab_compose}"

    occupied = list_docker_occupied_host_ports(exclude_project=exclude_project)
    text = compose_file.read_text(encoding="utf-8", errors="replace")
    rewritten = rewrite_compose_host_ports(text, occupied)
    if rewritten is None:
        web_ports = web_host_ports(parse_compose_port_mappings(text))
        if not web_ports:
            return None, "缓存配方 compose 未把 Web 端口映射到宿主机。"
        conflicts = [p for p in web_ports if p in occupied]
        return None, (
            f"缓存配方宿主端口无法改写: {conflicts}。"
            f"docker 当前已占用: {sorted(occupied)}。"
        )
    if rewritten != text:
        compose_file.write_text(rewritten, encoding="utf-8")

    web_ports = load_web_host_ports(str(compose_file))
    if not web_ports:
        return None, "缓存配方 compose 未把 Web 端口映射到宿主机。"

    _emit(ctx, "命中已缓存配方，平台启动靶场（docker compose up --build）")
    ok, err = await docker_compose_up(
        lab_compose,
        result.workdir,
        None,
        lab_id=result.lab_id,
        on_progress=lambda line: _emit(ctx, line),
    )
    if not ok:
        if is_docker_unavailable(err):
            raise RuntimeError(err)
        logs = await collect_compose_logs(
            result.workdir, lab_compose, None, lab_id=result.lab_id
        )
        last_error = f"compose up 失败: {err}\n--- logs ---\n{logs}"
        logger.warning("缓存配方 compose up 失败: %s", (err or "")[:200])
        _emit(ctx, "缓存配方启动失败，回喂 AI")
        await docker_compose_down(
            result.workdir, lab_compose, None, lab_id=result.lab_id
        )
        return None, last_error

    _emit(
        ctx,
        f"正在探活 127.0.0.1:{web_ports[0]}"
        + (f" 等 {len(web_ports)} 个映射口" if len(web_ports) > 1 else ""),
    )
    ok, live_port = await health_check(web_ports)
    if not ok or live_port is None:
        logs = await collect_compose_logs(
            result.workdir, lab_compose, None, lab_id=result.lab_id
        )
        last_error = (
            f"健康检查不过(mapped_ports={web_ports})\n--- logs ---\n{logs}"
        )
        _emit(ctx, "缓存配方探活失败，回喂 AI")
        await docker_compose_down(
            result.workdir, lab_compose, None, lab_id=result.lab_id
        )
        return None, last_error

    advertise = host_advertise_ip()
    target_url = publish_target_url(live_port, advertise)
    output = {
        "target_url": target_url,
        "compose_path": lab_compose,
        "transport_shape": (hit.get("transport_shape") if isinstance(hit, dict) else None)
        or {"protocol": "http"},
        "initial_creds": (hit.get("initial_creds") if isinstance(hit, dict) else None) or {},
        "started_containers": (hit.get("started_containers") if isinstance(hit, dict) else None)
        or [],
        "reused": True,
    }
    await _upload_then_mark_ready(
        ctx,
        svc,
        result,
        commit_sha=commit_sha,
        lab_compose=lab_compose,
        output=output,
    )
    _emit(ctx, f"靶场就绪：{target_url}")
    return output, None


async def _wait_for_lab(
    ctx: NodeContext,
    *,
    owner_id: str,
    project_id: str,
    commit_sha: str,
) -> Any:
    from app.contexts.lab.service import LabService
    from app.core.config import get_settings

    timeout = get_settings().agent_runner_timeout_seconds
    deadline = time.monotonic() + float(timeout)
    svc = LabService(ctx.db_session)
    while True:
        _emit(ctx, "等待其他任务把靶场搭好")
        await asyncio.sleep(2)
        if time.monotonic() >= deadline:
            raise RuntimeError("等待靶场就绪超时")
        result = await svc.acquire(
            owner_id=owner_id,
            project_id=project_id,
            commit_sha=commit_sha,
            task_id=ctx.task_id,
        )
        if result.role != "wait":
            return result


async def _start_lab(ctx: NodeContext, result: Any) -> dict[str, Any]:
    from app.contexts.lab.docker_ops import compose_start, list_containers
    from app.contexts.lab.service import LabService

    svc = LabService(ctx.db_session)
    _emit(ctx, f"启动已停止的靶场 {result.compose_project}")
    ok = await compose_start(result.compose_project)
    if not ok:
        if await list_containers(result.compose_project):
            await svc.mark_failed(result.lab_id, "compose start 失败")
            raise RuntimeError("靶场 compose start 失败")
        _emit(ctx, "靶场容器已不存在，改为重新创建")
        await svc.reclaim_gone_runtime(result.lab_id, ctx.task_id)
        return await _create_lab(ctx, result)
    await svc.mark_ready(
        result.lab_id,
        target_url=result.target_url or "",
        compose_path=result.compose_path or ".vuln-env/docker-compose.yml",
        transport_shape=result.transport_shape or {"protocol": "http"},
        initial_creds=result.initial_creds or {},
    )
    return _reused_output(result)


async def _create_lab(ctx: NodeContext, result: Any) -> dict[str, Any]:
    from pathlib import Path

    from app.contexts.lab.service import LabService

    svc = LabService(ctx.db_session)
    last_error: str | None = None
    repo = repo_dirname_from_outputs(ctx.previous_outputs)
    exclude_project = _exclude_compose_project(result.lab_id)
    src_repo = str(Path(ctx.host_workdir) / (repo or "project"))
    commit_sha = _commit_sha_from(ctx)

    try:
        cached, last_error = await _try_cached_recipe(
            ctx,
            svc,
            result,
            commit_sha=commit_sha,
            exclude_project=exclude_project,
        )
        if cached is not None:
            return cached

        for attempt in range(1, MAX_ATTEMPTS + 1):
            occupied = list_docker_occupied_host_ports(exclude_project=exclude_project)
            _emit(ctx, f"第 {attempt}/{MAX_ATTEMPTS} 轮：AI 分析并写 Dockerfile/compose")
            recipe = await run_ai_turn(
                ctx, attempt, last_error, occupied_host_ports=sorted(occupied)
            )

            compose_path = recipe.get("compose_path", ".vuln-env/docker-compose.yml")
            abs_compose = resolve_compose_host_path(compose_path, ctx.host_workdir, repo)
            web_ports = load_web_host_ports(abs_compose)
            if not web_ports:
                last_error = (
                    f"attempt {attempt} compose 未把 Web 端口映射到宿主机。"
                    "只映射浏览器访问的入口（host:container），"
                    "postgres/redis/mysql 不要写 ports 到宿主。"
                )
                _emit(ctx, f"缺少 Web 端口映射，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                continue

            occupied = list_docker_occupied_host_ports(exclude_project=exclude_project)
            conflicts = [p for p in web_ports if p in occupied]
            if conflicts:
                last_error = (
                    f"attempt {attempt} 宿主端口已被其他容器占用: {conflicts}。"
                    f"docker 当前已占用: {sorted(occupied)}。"
                    "只改 compose 的 host 侧映射口（例如 3001:3000 改成 3011:3000），"
                    "不要改容器内监听口，不要映射已占用端口。"
                )
                _emit(
                    ctx,
                    f"端口 {conflicts} 已被占用，回喂 AI 改映射（{attempt}/{MAX_ATTEMPTS}）",
                )
                continue

            sync_recipe_to_lab(src_repo, result.workdir)
            lab_compose = lab_recipe_compose_path(compose_path)
            _emit(ctx, f"第 {attempt}/{MAX_ATTEMPTS} 轮：平台启动靶场（docker compose up --build）")
            ok, err = await docker_compose_up(
                lab_compose,
                result.workdir,
                None,
                lab_id=result.lab_id,
                on_progress=lambda line: _emit(ctx, line),
            )
            if not ok:
                logs = await collect_compose_logs(
                    result.workdir, lab_compose, None, lab_id=result.lab_id
                )
                last_error = f"attempt {attempt} compose up 失败: {err}\n--- logs ---\n{logs}"
                logger.warning(f"节点 2 attempt {attempt} 失败: {err[:200]}")
                _emit(ctx, f"启动失败，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await docker_compose_down(
                    result.workdir, lab_compose, None, lab_id=result.lab_id
                )
                continue

            _emit(ctx, f"正在探活 127.0.0.1:{web_ports[0]}" + (
                f" 等 {len(web_ports)} 个映射口" if len(web_ports) > 1 else ""
            ))
            ok, live_port = await health_check(web_ports)
            if not ok or live_port is None:
                logs = await collect_compose_logs(
                    result.workdir, lab_compose, None, lab_id=result.lab_id
                )
                last_error = (
                    f"attempt {attempt} 健康检查不过(mapped_ports={web_ports})\n--- logs ---\n{logs}"
                )
                _emit(ctx, f"探活失败，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await docker_compose_down(
                    result.workdir, lab_compose, None, lab_id=result.lab_id
                )
                continue

            advertise = host_advertise_ip()
            target_url = publish_target_url(live_port, advertise)
            raw_url = recipe.get("target_url") or ""
            if raw_url:
                from urllib.parse import urlparse

                parsed = urlparse(str(raw_url) if "://" in str(raw_url) else f"http://{raw_url}")
                suffix = parsed.path or ""
                if parsed.query:
                    suffix += f"?{parsed.query}"
                if suffix and suffix != "/":
                    target_url = f"{target_url.rstrip('/')}{suffix}"

            _emit(ctx, f"靶场就绪：{target_url}")
            output = {
                "target_url": target_url,
                "compose_path": lab_compose,
                "transport_shape": recipe.get("transport_shape", {"protocol": "http"}),
                "initial_creds": recipe.get("initial_creds", {}),
                "started_containers": recipe.get("started_containers", []),
            }
            await _upload_then_mark_ready(
                ctx,
                svc,
                result,
                commit_sha=commit_sha,
                lab_compose=lab_compose,
                output=output,
            )
            return output

        raise RuntimeError(f"靶场搭建 {MAX_ATTEMPTS} 轮全失败: {(last_error or 'unknown')[:500]}")
    except Exception as e:
        await svc.mark_failed(result.lab_id, (last_error or str(e))[:500])
        raise


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
            advertise = host_advertise_ip()
            return {
                "target_url": publish_target_url(8080, advertise),
                "compose_path": ".vuln-env/docker-compose.yml",
                "transport_shape": {"protocol": "http", "listener": "0.0.0.0:8080", "tls_termination": "无"},
                "initial_creds": {},
                "started_containers": ["mock-app"],
            }

        sha = (ctx.previous_outputs.get("source") or {}).get("commit_sha")
        if not sha:
            raise RuntimeError("env_ready 缺少 source.commit_sha，不能 acquire 靶场")
        project_id = await _resolve_project_id(ctx)
        ctx.project_id = project_id
        if not ctx.owner_id:
            raise RuntimeError("env_ready 缺少 owner_id，不能 acquire 靶场")

        from app.contexts.lab.service import LabService

        result = await LabService(ctx.db_session).acquire(
            owner_id=ctx.owner_id,
            project_id=project_id,
            commit_sha=sha,
            task_id=ctx.task_id,
        )
        if result.role == "wait":
            result = await _wait_for_lab(
                ctx,
                owner_id=ctx.owner_id,
                project_id=project_id,
                commit_sha=sha,
            )
        if result.role == "reuse":
            _emit(ctx, f"复用靶场：{result.target_url}")
            return _reused_output(result)
        if result.role == "start":
            return await _start_lab(ctx, result)
        return await _create_lab(ctx, result)
