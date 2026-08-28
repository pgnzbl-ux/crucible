"""宿主 compose 路径 / 进度摘要 / up·down·logs。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

COMPOSE_PROGRESS_INTERVAL = 2.0
COMPOSE_PROGRESS_MAX = 220
COMPOSE_UP_TIMEOUT = 600
COMPOSE_WAIT_TIMEOUT = 300
# 同一诊断指纹累计达此次数 → 中止 compose，回喂下一轮（避免空转到硬超时）
COMPOSE_DIAG_REPEAT_ABORT = 40
_COMPOSE_URGENT = re.compile(r"error|failed|fatal|exception", re.I)
_COMPOSE_DIAG = re.compile(
    r"(?i)(\[error\]|error:|failed to solve|could not transfer|"
    r"dependencyresolution|npm err!|no such file|copy |"
    r"failed to execute|premature end|etimedout|econnreset|"
    r"address already in use|permission denied|security policy|"
    r"failed to|fatal|traceback|connection refused|unhealthy|"
    r"healthcheck|exited \([1-9][0-9]*\)|oomkilled)"
)
_COMPOSE_DIAG_NOISE = re.compile(
    r"(?i)to see the full stack trace|re-run maven|"
    r"for more information about the errors|"
    r"\[help 1\]|enable full debug logging"
)
_BUILD_STEP_TS = re.compile(r"^#\d+\s+[\d.]+\s+")
_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.+-]+")


def compose_progress_text(line: str, limit: int = COMPOSE_PROGRESS_MAX) -> str | None:
    text = " ".join((line or "").split())
    if not text:
        return None
    return text[:limit]


def progress_fingerprint(text: str) -> str:
    """抹掉 BuildKit 步进秒数 / ISO 时间，得到可去重的稳定指纹。"""
    normalized = _BUILD_STEP_TS.sub("#N ", text or "")
    normalized = _ISO_TS.sub("<ts>", normalized)
    return normalized[:180]


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
    """把 docker compose 的刷屏日志收成可落库的进度句。

    urgent（含 error 等）只对**新指纹**立即放行；与上次已 emit 相同的
    诊断行仍走普通节流，避免同类错误打爆 UI。
    """

    def __init__(
        self,
        emit: Callable[[str], None],
        min_interval: float = COMPOSE_PROGRESS_INTERVAL,
    ) -> None:
        self._emit = emit
        self.min_interval = min_interval
        self._last = 0.0
        self._last_fp: str | None = None
        self._pending: str | None = None

    def push(self, line: str) -> None:
        text = compose_progress_text(line)
        if not text:
            return
        now = time.monotonic()
        fp = progress_fingerprint(text)
        same_as_last = self._last_fp is not None and fp == self._last_fp
        urgent = bool(_COMPOSE_URGENT.search(text)) and not same_as_last
        first = self._last == 0.0
        if first or urgent or (now - self._last) >= self.min_interval:
            self._last = now
            self._last_fp = fp
            self._pending = None
            self._emit(text)
        else:
            self._pending = text

    def flush(self) -> None:
        if self._pending:
            self._emit(self._pending)
            self._pending = None


class ComposeDiagStallGuard:
    """同类诊断日志反复出现时要求中止 compose。"""

    def __init__(self, limit: int = COMPOSE_DIAG_REPEAT_ABORT) -> None:
        self.limit = max(1, int(limit))
        self._counts: dict[str, int] = {}
        self._samples: dict[str, str] = {}

    def observe(self, line: str) -> str | None:
        """若应中止，返回给下一轮 AI 的失败摘要；否则 None。"""
        text = compose_progress_text(line)
        if not text:
            return None
        if not (_COMPOSE_URGENT.search(text) or _COMPOSE_DIAG.search(text)):
            return None
        if _COMPOSE_DIAG_NOISE.search(text):
            return None
        fp = progress_fingerprint(text)
        self._counts[fp] = self._counts.get(fp, 0) + 1
        self._samples.setdefault(fp, text)
        if self._counts[fp] < self.limit:
            return None
        sample = self._samples[fp]
        return (
            "docker compose 同类错误反复刷屏，已中止等待:\n"
            f"{sample}\n"
            "failed to build / repeating diagnostic logs"
        )


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
    text = (err or "").lower()
    platform_markers = (
        "cannot connect to the docker daemon",
        "is the docker daemon running",
        "error during connect",
        "permission denied while trying to connect to the docker api",
        "permission denied while trying to connect to the docker daemon",
        "docker.sock: connect: permission denied",
        "no such file or directory: 'docker'",
        "docker compose 异常:",
    )
    return any(marker in text for marker in platform_markers)


def classify_compose_failure_stage(err: str) -> str:
    """把 compose 的失败分成 AI 能采取不同动作的阶段。"""
    text = (err or "").lower()
    if is_docker_unavailable(err):
        return "docker_unavailable"
    if "安全策略拒绝" in err or "security policy" in text:
        return "compose_policy"
    if "address already in use" in text or "port in use" in text:
        return "port_conflict"
    if "unhealthy" in text or "healthcheck" in text or "health check" in text:
        return "container_healthcheck"
    if "超时" in err or "timeout" in text or "timed out" in text:
        return "compose_timeout"
    build_markers = (
        "failed to solve",
        "failed to build",
        "build fail",
        "build failed",
        "could not transfer",
        "dependencyresolution",
        "npm err!",
        "copy ",
        "dockerfile",
    )
    if any(marker in text for marker in build_markers):
        return "compose_build"
    return "container_start"


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
    up_timeout: int = COMPOSE_UP_TIMEOUT,
    wait_timeout: int = COMPOSE_WAIT_TIMEOUT,
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
        "--wait", "--wait-timeout", str(wait_timeout),
    ]

    def _run() -> tuple[int, str, bool, str | None]:
        from app.contexts.lab.compose_policy import compose_subprocess_env

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=compose_subprocess_env(),
        )

        def _forward(text: str) -> None:
            logger.info("docker compose: %s", text)
            if on_progress:
                on_progress(text)

        throttle = ComposeProgressThrottle(_forward)
        stall = ComposeDiagStallGuard(limit=COMPOSE_DIAG_REPEAT_ABORT)
        chunks: list[str] = []
        timed_out = False
        stall_err: str | None = None

        def _kill() -> None:
            nonlocal timed_out
            timed_out = True
            proc.kill()

        timer = threading.Timer(up_timeout, _kill)
        timer.daemon = True
        timer.start()
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    chunks.append(line)
                    throttle.push(line)
                    stall_err = stall.observe(line)
                    if stall_err:
                        proc.kill()
                        break
            rc = proc.wait()
        finally:
            timer.cancel()
            throttle.flush()
        return rc, "".join(chunks), timed_out, stall_err

    try:
        rc, out, timed_out, stall_err = await asyncio.to_thread(_run)
        if stall_err:
            return False, stall_err
        if timed_out:
            return False, f"docker compose up 超时(>{up_timeout}s)"
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
    """收服务日志和容器状态给下轮 AI 排障。

    先截前 2000 字符会按服务排序误丢 Web 容器根因；这里保留完整的
    ``--tail`` 输出交给 ``summarize_compose_failure`` 扫描，并补充 Docker
    healthcheck 最近输出、退出码和 OOM 状态。
    """
    p_args = _compose_project_args(_compose_ident(lab_id=lab_id, task_id=task_id))
    cmd = ["docker", "compose", *p_args, "logs", "--tail=50"]
    ps_cmd = ["docker", "compose", *p_args, "ps", "-a", "-q"]
    cwd = host_workdir
    if compose_path:
        abs_path = resolve_compose_host_path(compose_path, host_workdir, repo_dirname)
        cmd = ["docker", "compose", *p_args, "-f", abs_path.replace("\\", "/"), "logs", "--tail=50"]
        ps_cmd = [
            "docker", "compose", *p_args, "-f", abs_path.replace("\\", "/"),
            "ps", "-a", "-q",
        ]
        cwd = None

    def _collect() -> str:
        sections: list[str] = []
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw_logs = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part and part.strip()
        )
        if raw_logs:
            sections.append(raw_logs)

        ps_result = subprocess.run(
            ps_cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        ids = [line.strip() for line in (ps_result.stdout or "").splitlines() if line.strip()]
        if ids:
            inspect_result = subprocess.run(
                ["docker", "inspect", *ids],
                capture_output=True,
                text=True,
                timeout=30,
            )
            states = _summarize_container_states(inspect_result.stdout or "")
            if states:
                sections.append(f"--- container states ---\n{states}")
        return "\n".join(sections)

    try:
        return await asyncio.to_thread(_collect)
    except Exception:  # noqa: BLE001
        return ""


def _summarize_container_states(raw: str) -> str:
    """从 docker inspect 中仅提取运行/健康诊断，不回传环境变量。"""
    try:
        documents = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(documents, list):
        return ""

    lines: list[str] = []
    for item in documents:
        if not isinstance(item, dict):
            continue
        state = item.get("State") if isinstance(item.get("State"), dict) else {}
        config = item.get("Config") if isinstance(item.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        name = str(item.get("Name") or "").lstrip("/")
        service = str(labels.get("com.docker.compose.service") or name or "unknown")
        status = str(state.get("Status") or "unknown")
        exit_code = state.get("ExitCode")
        oom = bool(state.get("OOMKilled", False))
        state_error = " ".join(str(state.get("Error") or "").split())[:300]
        summary = f"{service}: status={status} exit={exit_code} oom_killed={str(oom).lower()}"
        if state_error:
            summary += f" error={state_error}"
        health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
        health_status = str(health.get("Status") or "")
        if health_status:
            summary += f" health={health_status}"
        lines.append(summary)
        health_logs = health.get("Log") if isinstance(health.get("Log"), list) else []
        for entry in health_logs[-3:]:
            if not isinstance(entry, dict):
                continue
            output = " ".join(str(entry.get("Output") or "").split())[:600]
            code = entry.get("ExitCode")
            if output or code not in (None, 0):
                lines.append(f"{service} healthcheck: exit={code} output={output or '(empty)'}")
    return "\n".join(lines)


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
