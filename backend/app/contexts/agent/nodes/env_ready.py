"""节点 2 靶场就绪 — AI 出配方 + 代码执行 docker compose 的排障循环。

AI 在 agent-runner 内写/改 {repo}/.vuln-env/Dockerfile + docker-compose.yml(文本,不碰 docker.sock);
worker 就地在任务 workspace 执行 docker compose up(项目名 -p crucible-lab-{id} 隔离,
无文件暂存,build.context 天然指向仓库内模块) + 健康检查;失败回喂 AI(max 5 轮)。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
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
HEALTH_RETRIES = 30
HEALTH_RETRY_SECONDS = 3
HEALTH_SETTLE_SECONDS = 3
_PROBE_BODY_LIMIT = 65536
_PROBE_SNIPPET_LIMIT = 800
# 端口通了但应用崩溃：PHP Fatal、缺表、Spring Whitelabel、Python traceback、WP 连不上库。
_CRASH_BODY_RE = re.compile(
    r"(?is)("
    r"fatal error:|"
    r"parse error:|"
    r"uncaught (?:\w+ )?exception|"
    r"sqlstate\[\w+\]|"
    r"base table or view not found|"
    r"table ['\"][\w.]+['\"] doesn't exist|"
    r"traceback \(most recent call last\)|"
    r"internal server error|"
    r"whitelabel error page|"
    r"502 bad gateway|"
    r"503 service unavailable|"
    r"504 gateway timeout|"
    r"error establishing a database connection|"
    r"could not connect to (?:the )?database|"
    r"no such table[: ]|"
    r"operationalerror"
    r")"
)
COMPOSE_PROGRESS_INTERVAL = 2.0
COMPOSE_PROGRESS_MAX = 220
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


def web_container_ports(mappings: list[tuple[int, int]]) -> list[int]:
    """与 web_host_ports 同序的容器侧端口（供 scheme 推断）。"""
    seen: set[int] = set()
    ports: list[int] = []
    for host_port, container_port in mappings:
        if container_port in _SIDECAR_CONTAINER_PORTS:
            continue
        if host_port in seen:
            continue
        seen.add(host_port)
        ports.append(container_port)
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


def load_web_container_ports(compose_abs: str) -> list[int]:
    """与 load_web_host_ports 同序的容器侧端口（host_port 去重后对位）。

    用于探活推断入口 scheme（443/8443 → https）。容器侧信息在去重时可能
    丢失（同一 host 口多个 target），此时对位退化为 None → http，可接受。
    """
    from pathlib import Path

    try:
        text = Path(compose_abs).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return web_container_ports(parse_compose_port_mappings(text))


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


def _read_probe_text(fp: Any, limit: int = _PROBE_BODY_LIMIT) -> str:
    try:
        raw = fp.read(limit) if hasattr(fp, "read") else b""
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _crash_page_reason(body: str) -> str:
    if not body or not _CRASH_BODY_RE.search(body):
        return ""
    snippet = " ".join(body.split())
    if len(snippet) > _PROBE_SNIPPET_LIMIT:
        snippet = snippet[:_PROBE_SNIPPET_LIMIT] + "…"
    return f"首页内容异常: {snippet}"


def _probe_http(url: str, timeout: float = 5) -> tuple[bool, str]:
    """GET 首页：非 5xx 且正文不是崩溃页才算活着（401/404 也算）。

    https 自签证书放宽校验——探活只关心应用是否起来，不校验身份。
    """
    import ssl
    import urllib.error
    import urllib.request

    ctx = None
    if url.startswith("https://"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            code = int(getattr(resp, "status", 200))
            body = _read_probe_text(resp)
            if not (200 <= code < 500):
                return False, f"HTTP {code}"
            reason = _crash_page_reason(body)
            if reason:
                return False, reason
            return True, ""
    except urllib.error.HTTPError as e:
        code = int(e.code)
        if 400 <= code < 500:
            return True, ""
        body = _read_probe_text(e)
        reason = _crash_page_reason(body)
        return False, reason or f"HTTP {code}"
    except Exception as e:  # noqa: BLE001
        return False, f"无 HTTP 应答: {type(e).__name__}"


def _http_alive(url: str, timeout: float = 5) -> bool:
    ok, reason = _probe_http(url, timeout)
    _http_alive.last_error = reason
    return ok


_HTTPS_CONTAINER_PORTS = {443, 8443, 9443}


def _probe_scheme_for(container_port: int | None) -> str:
    """容器侧端口暗示入口协议：443/8443/9443 → https，其余 http。"""
    if container_port in _HTTPS_CONTAINER_PORTS:
        return "https"
    return "http"


async def health_check(
    ports: list[int] | None,
    extra_ports: list[int] | None = None,
    container_ports: list[int] | None = None,
) -> tuple[bool, int | None, str]:
    """只对 compose 映射到宿主机的 Web 端口探活，不扫本机 80/8080 等常用口。

    compose up 后先等 HEALTH_SETTLE_SECONDS，再 GET 首页正文：端口通但
    Fatal/缺表/Whitelabel 不算就绪。失败细节写入 health_check.last_error
    供回喂 AI。container_ports 与 ports 等长对位，用于推断入口 scheme。
    返回 (ok, live_port, scheme)。
    """
    health_check.last_error = ""
    ordered: list[int] = []
    schemes: list[str] = []
    seen: set[int] = set()
    cps = list(container_ports or [])
    for idx, p in enumerate(list(ports or []) + list(extra_ports or [])):
        port = int(p)
        if port in seen:
            continue
        seen.add(port)
        ordered.append(port)
        schemes.append(_probe_scheme_for(cps[idx] if idx < len(cps) else None))
    if not ordered:
        health_check.last_error = "无 Web 映射口"
        return False, None, "http"

    if HEALTH_SETTLE_SECONDS > 0:
        await asyncio.sleep(HEALTH_SETTLE_SECONDS)

    primary = ordered[0]
    last_error = ""
    for _ in range(HEALTH_RETRIES):
        if _http_alive(f"{schemes[0]}://127.0.0.1:{primary}"):
            return True, primary, schemes[0]
        reason = getattr(_http_alive, "last_error", "")
        if isinstance(reason, str) and reason:
            last_error = reason
        await asyncio.sleep(HEALTH_RETRY_SECONDS)
    for p, scheme in zip(ordered[1:], schemes[1:], strict=False):
        if _http_alive(f"{scheme}://127.0.0.1:{p}"):
            return True, p, scheme
        reason = getattr(_http_alive, "last_error", "")
        if isinstance(reason, str) and reason:
            last_error = reason
    health_check.last_error = last_error or "无 HTTP 应答"
    return False, None, "http"


def _health_fail_detail() -> str:
    detail = getattr(health_check, "last_error", "")
    return detail if isinstance(detail, str) and detail else "无 HTTP 应答"


def _emit(ctx: NodeContext, message: str) -> None:
    if ctx.on_event:
        ctx.on_event({"type": "phase.updated", "phase": "env_ready", "message": message})


def _snapshot_failed_attempt(
    ctx: NodeContext,
    attempt: int,
    last_error: str | None,
    failed_stage: str | None,
    recipe: dict[str, Any] | None = None,
) -> None:
    try:
        from app.contexts.agent.node_failure import snapshot_attempt

        snapshot_attempt(
            ctx.host_workdir,
            "env_ready",
            attempt,
            previous_error=last_error,
            platform_error=f"failed_stage={failed_stage or 'unknown'}\n{last_error or ''}",
            submit=recipe,
            copy_vuln_env=True,
        )
    except Exception:
        logger.warning("env_ready 失败快照失败 attempt=%s", attempt, exc_info=True)


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
    *,
    failed_stage: str | None = None,
    occupied_host_ports: list[int] | None = None,
    credential_lookup_only: bool = False,
    existing_target_url: str | None = None,
    existing_compose_path: str | None = None,
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
        "failed_stage": failed_stage,
        "occupied_host_ports": list(occupied_host_ports or []),
        "credential_lookup_only": credential_lookup_only,
        "existing_target_url": existing_target_url,
        "existing_compose_path": existing_compose_path,
    }
    return await run_ai_node(
        node_key="env_ready",
        input_json=input_json,
        host_workdir=ctx.host_workdir,
        runner_env=ctx.runner_env,
        on_event=ctx.on_event,
        task_id=ctx.task_id,
        validate=False,  # 排障环自带逐项校验 + 回喂重试；平台先斩后奏会废掉回喂分支
    )


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
    from urllib.parse import urlparse

    raw = str(result.target_url or "")
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    if not parsed.port:
        return False
    scheme = parsed.scheme or "http"
    return _http_alive(f"{scheme}://127.0.0.1:{parsed.port}")


async def _lookup_initial_creds(
    ctx: NodeContext,
    *,
    target_url: str,
    compose_path: str,
) -> dict[str, Any]:
    _emit(ctx, "复用靶场缺少凭据元数据，AI 只读源码补查登录方式")
    output = await run_ai_turn(
        ctx,
        1,
        None,
        credential_lookup_only=True,
        existing_target_url=target_url,
        existing_compose_path=compose_path,
    )
    creds = output.get("initial_creds")
    from app.contexts.agent.ai_runner import validate_initial_creds

    ok, err = validate_initial_creds(creds)
    if not ok:
        raise RuntimeError(f"靶场凭据补查失败: {err}")
    return creds


async def _backfill_reused_initial_creds(
    ctx: NodeContext,
    svc: Any,
    result: Any,
) -> dict[str, Any]:
    current = result.initial_creds or {}
    if current:
        return current

    target_url = str(result.target_url or "")
    compose_path = result.compose_path or ".vuln-env/docker-compose.yml"
    creds = await _lookup_initial_creds(
        ctx,
        target_url=target_url,
        compose_path=compose_path,
    )
    await svc.mark_ready(
        result.lab_id,
        target_url=target_url,
        compose_path=compose_path,
        transport_shape=result.transport_shape or {"protocol": "http"},
        initial_creds=creds,
    )
    return creds


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


async def _upload_then_mark_ready(
    ctx: NodeContext,
    svc: Any,
    result: Any,
    *,
    commit_sha: str,
    lab_compose: str,
    output: dict[str, Any],
    repo: str | None = None,
) -> None:
    from pathlib import Path

    repo_name = (repo or "").strip() or None
    recipe_root = (
        str(Path(ctx.host_workdir) / repo_name) if repo_name else ctx.host_workdir
    )
    try:
        uploaded = await svc.upload_recipe(
            owner_id=ctx.owner_id,
            project_id=ctx.project_id or "",
            commit_sha=commit_sha,
            lab_workdir=recipe_root,
            compose_path=lab_compose,
            transport_shape=output["transport_shape"],
            initial_creds=output["initial_creds"],
            started_containers=output.get("started_containers") or [],
        )
        if uploaded is False:
            _emit(
                ctx,
                "警告：配方缓存上传失败（MinIO 异常），靶场仍可用；"
                "rebuild 时将无法复用本配方",
            )
        await svc.mark_ready(
            result.lab_id,
            target_url=output["target_url"],
            compose_path=workspace_compose_rel(repo_name, lab_compose),
            transport_shape=output["transport_shape"],
            initial_creds=output["initial_creds"],
        )
    except Exception:
        await docker_compose_down(
            ctx.host_workdir,
            lab_compose,
            repo_name,
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
    repo: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """MinIO 命中后落位 workspace、改口、up、探活。成功产出含 reused；docker 不可用则抛；失败 (None, last_error)。"""
    from pathlib import Path

    repo_name = (repo or "").strip() or None
    if not repo_name:
        return None, None
    repo_dir = Path(ctx.host_workdir) / repo_name

    hit = await svc.download_recipe(
        owner_id=ctx.owner_id or "",
        project_id=ctx.project_id or "",
        commit_sha=commit_sha,
        dest_workdir=str(repo_dir),
    )
    if not hit:
        return None, None

    compose_rel = repo_compose_rel(hit.get("compose_path") if isinstance(hit, dict) else None)
    compose_file = repo_dir / compose_rel
    if not compose_file.is_file():
        return None, f"缓存配方缺少 compose 文件: {compose_rel}"

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

    _emit(ctx, "命中已缓存配方，平台启动靶场（docker compose up -d --build）")
    ok, err = await docker_compose_up(
        compose_rel,
        ctx.host_workdir,
        repo_name,
        lab_id=result.lab_id,
        on_progress=lambda line: _emit(ctx, line),
    )
    if not ok:
        if is_docker_unavailable(err):
            raise RuntimeError(err)
        logs = await collect_compose_logs(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        last_error = (
            f"compose up 失败: {err}\n--- logs ---\n{summarize_compose_failure(logs)}"
        )
        logger.warning("缓存配方 compose up 失败: %s", (err or "")[:200])
        _emit(ctx, "缓存配方启动失败，回喂 AI")
        await docker_compose_down(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        return None, last_error

    _emit(
        ctx,
        f"正在探活 127.0.0.1:{web_ports[0]}"
        + (f" 等 {len(web_ports)} 个映射口" if len(web_ports) > 1 else ""),
    )
    ok, live_port, scheme = await health_check(
        web_ports, container_ports=load_web_container_ports(str(compose_file))
    )
    if not ok or live_port is None:
        logs = await collect_compose_logs(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        last_error = (
            f"健康检查不过(mapped_ports={web_ports})\n"
            f"{_health_fail_detail()}\n"
            f"--- logs ---\n{summarize_compose_failure(logs)}"
        )
        _emit(ctx, "缓存配方探活失败，回喂 AI")
        await docker_compose_down(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        return None, last_error

    advertise = host_advertise_ip()
    target_url = publish_target_url(live_port, advertise, scheme=scheme)
    output = {
        "target_url": target_url,
        "compose_path": compose_rel,
        "transport_shape": (hit.get("transport_shape") if isinstance(hit, dict) else None)
        or {"protocol": "http"},
        "initial_creds": (hit.get("initial_creds") if isinstance(hit, dict) else None) or {},
        "started_containers": (hit.get("started_containers") if isinstance(hit, dict) else None)
        or [],
        "reused": True,
    }
    output["started_containers"] = await _live_started_containers(
        getattr(result, "compose_project", None),
        output.get("started_containers"),
    )
    if not output["initial_creds"]:
        # 凭据补查失败必须先拆刚 up 的靶场再抛：否则 DB=failed/容器在跑，
        # 端口泄漏且孤儿 compose 阻塞后续轮次（该分支随 P0#2 修复变可达）
        try:
            output["initial_creds"] = await _lookup_initial_creds(
                ctx,
                target_url=target_url,
                compose_path=compose_rel,
            )
        except Exception:
            await docker_compose_down(
                ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
            )
            raise
    await _upload_then_mark_ready(
        ctx,
        svc,
        result,
        commit_sha=commit_sha,
        lab_compose=compose_rel,
        output=output,
        repo=repo_name,
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
    rebuilt = await _reuse_or_rebuild_dead_lab(ctx, svc, result)
    if rebuilt is not None:
        return rebuilt
    creds = await _backfill_reused_initial_creds(ctx, svc, result)
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
    if _reused_lab_alive(result):
        return None
    _emit(ctx, "复用靶场探活失败（应用可能已死），降级重建")
    await svc.mark_failed(result.lab_id, "复用前探活失败：应用不响应")
    await svc.reclaim_gone_runtime(result.lab_id, ctx.task_id)
    return await _create_lab(ctx, result)


async def _bump_node_attempt(ctx: NodeContext, attempt: int) -> None:
    """把排障轮次写进 NodeRun.attempt（表上可见真实轮次）。best-effort。"""
    try:
        from sqlalchemy import update

        from app.contexts.task.models import NodeRun

        await ctx.db_session.execute(
            update(NodeRun)
            .where(
                NodeRun.run_id == ctx.run_id,
                NodeRun.node_index == 2,
            )
            .values(attempt=attempt)
        )
        await ctx.db_session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("更新 NodeRun.attempt 失败 attempt=%s", attempt, exc_info=True)


async def _create_lab(ctx: NodeContext, result: Any) -> dict[str, Any]:
    from app.contexts.lab.service import LabService

    svc = LabService(ctx.db_session)
    last_error: str | None = None
    failed_stage: str | None = None
    repo = repo_dirname_from_outputs(ctx.previous_outputs)
    exclude_project = _exclude_compose_project(result.lab_id)
    commit_sha = _commit_sha_from(ctx)

    try:
        cached, last_error = await _try_cached_recipe(
            ctx,
            svc,
            result,
            commit_sha=commit_sha,
            exclude_project=exclude_project,
            repo=repo,
        )
        if cached is not None:
            return cached
        if last_error:
            failed_stage = "cached_recipe"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt > 1:
                await _bump_node_attempt(ctx, attempt)
            occupied = list_docker_occupied_host_ports(exclude_project=exclude_project)
            _emit(ctx, f"第 {attempt}/{MAX_ATTEMPTS} 轮：AI 分析并写 Dockerfile/compose")
            recipe = await run_ai_turn(
                ctx,
                attempt,
                last_error,
                failed_stage=failed_stage,
                occupied_host_ports=sorted(occupied),
            )

            from app.contexts.agent.ai_runner import validate_initial_creds

            creds_ok, creds_err = validate_initial_creds(recipe.get("initial_creds"))
            if not creds_ok:
                last_error = f"attempt {attempt} {creds_err}"
                failed_stage = "recipe_validation"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                _emit(
                    ctx,
                    f"initial_creds 无效，回喂 AI 补查（{attempt}/{MAX_ATTEMPTS}）",
                )
                continue

            compose_path = recipe.get("compose_path", ".vuln-env/docker-compose.yml")
            abs_compose = resolve_compose_host_path(compose_path, ctx.host_workdir, repo)
            web_ports = load_web_host_ports(abs_compose)
            if not web_ports:
                last_error = (
                    f"attempt {attempt} compose 未把 Web 端口映射到宿主机。"
                    "只映射浏览器访问的入口（host:container），"
                    "postgres/redis/mysql 不要写 ports 到宿主。"
                )
                failed_stage = "recipe_validation"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
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
                failed_stage = "port_conflict"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                _emit(
                    ctx,
                    f"端口 {conflicts} 已被占用，回喂 AI 改映射（{attempt}/{MAX_ATTEMPTS}）",
                )
                continue

            compose_rel = repo_compose_rel(compose_path)
            _emit(ctx, f"第 {attempt}/{MAX_ATTEMPTS} 轮：平台启动靶场（docker compose up -d --build）")
            ok, err = await docker_compose_up(
                compose_rel,
                ctx.host_workdir,
                repo,
                lab_id=result.lab_id,
                on_progress=lambda line: _emit(ctx, line),
            )
            if not ok:
                logs = await collect_compose_logs(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                last_error = (
                    f"attempt {attempt} compose up 失败: {err}\n"
                    f"--- logs ---\n{summarize_compose_failure(logs)}"
                )
                failed_stage = "compose_up"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                logger.warning(f"节点 2 attempt {attempt} 失败: {err[:200]}")
                _emit(ctx, f"启动失败，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await docker_compose_down(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                continue

            _emit(ctx, f"正在探活 127.0.0.1:{web_ports[0]}" + (
                f" 等 {len(web_ports)} 个映射口" if len(web_ports) > 1 else ""
            ))
            ok, live_port, scheme = await health_check(
                web_ports, container_ports=load_web_container_ports(str(abs_compose))
            )
            if not ok or live_port is None:
                logs = await collect_compose_logs(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                last_error = (
                    f"attempt {attempt} 健康检查不过(mapped_ports={web_ports})\n"
                    f"{_health_fail_detail()}\n"
                    f"--- logs ---\n{summarize_compose_failure(logs)}"
                )
                failed_stage = "health_check"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                _emit(ctx, f"探活失败，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await docker_compose_down(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                continue

            advertise = host_advertise_ip()
            target_url = publish_target_url(live_port, advertise, scheme=scheme)
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
                "compose_path": compose_rel,
                "transport_shape": recipe.get("transport_shape", {"protocol": "http"}),
                "initial_creds": recipe["initial_creds"],
                "started_containers": recipe.get("started_containers", []),
            }
            output["started_containers"] = await _live_started_containers(
                getattr(result, "compose_project", None),
                output.get("started_containers"),
            )
            await _upload_then_mark_ready(
                ctx,
                svc,
                result,
                commit_sha=commit_sha,
                lab_compose=compose_rel,
                output=output,
                repo=repo,
            )
            return output

        raise RuntimeError(f"靶场搭建 {MAX_ATTEMPTS} 轮全失败: {(last_error or 'unknown')[:500]}")
    except Exception as e:
        # 异常本体优先：last_error 是上一轮排障的旧账，拿它掩盖本轮异常
        # 会误导排障（attempt≥2 的 AI 异常曾被旧错误顶替）
        detail = str(e).strip() or last_error or "unknown"
        await svc.mark_failed(result.lab_id, detail[:500])
        raise


class EnvReadyNode:
    node_index = 2
    node_key = "env_ready"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import EnvReadyInput, InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "env_ready",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    def _bridge_previous(self, ctx: NodeContext, node_input) -> None:
        """边界投影：typed Input → previous_outputs，内部 Lab/compose 本阶段仍读此桥。"""
        ctx.previous_outputs = {
            "source": node_input.source.model_dump(exclude_none=True),
            "profile": node_input.profile.model_dump(exclude_none=True),
        }

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        inp = self._resolve_input(ctx, node_input)
        self._bridge_previous(ctx, inp)

        # Mock 模式:SDK 未启用时跳过真实 AI + docker compose,直接返回模拟靶场
        from app.core.config import get_settings
        if not get_settings().claude_agent_sdk_enabled:
            logger.info("[Mock] 节点 env_ready 返回模拟靶场(不执行 docker compose)")
            advertise = host_advertise_ip()
            return {
                "target_url": publish_target_url(8080, advertise),
                "compose_path": ".vuln-env/docker-compose.yml",
                "transport_shape": {"protocol": "http", "listener": "0.0.0.0:8080", "tls_termination": "无"},
                "initial_creds": {"note": "[Mock] 未配置预设账号"},
                "started_containers": ["mock-app"],
            }

        sha = inp.source.commit_sha
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
            svc = LabService(ctx.db_session)
            rebuilt = await _reuse_or_rebuild_dead_lab(ctx, svc, result)
            if rebuilt is not None:
                return rebuilt
            creds = await _backfill_reused_initial_creds(ctx, svc, result)
            return _reused_output(result, initial_creds=creds)
        if result.role == "start":
            return await _start_lab(ctx, result)
        return await _create_lab(ctx, result)
