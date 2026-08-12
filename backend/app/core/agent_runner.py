"""
agent-runner 容器编排 + 流式消费 — Crucible 唯一的容器抽象（替代原 SandboxManager）。

设计原则：
- Agent Runner = Claude Agent SDK 的隔离执行环境，与代码层（FastAPI / Celery）物理隔离
- 凭据仅通过 docker run --env 注入，容器销毁 env 消失（零落盘）
- 流式 stdout 通过 container.logs(stream=True) + 自建行缓冲解析（跨 chunk 半行处理）
- 同步接口：run_with_streaming() 内部全同步（适配 Celery asyncio.to_thread 包装）
- 取消双保险：信号钩子 + cleanup_stale 巡检

线程模型：
- 本模块全同步实现（Docker SDK 同步 API）
- Celery worker 通过 asyncio.to_thread 调用本模块
- 回调 on_event 在同步线程内执行，落库由 on_event 通过 asyncio.run_coroutine_threadsafe 跨入主 loop
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.types import LogConfig

from .config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)

AGENT_RUNNER_NAME_PREFIX = "crucible-agent-runner"
AGENT_RUNNER_NETWORK = "crucible-sandbox-net"


class AgentRunnerError(Exception):
    """agent-runner 容器编排失败"""


# ─ ── 行缓冲 JSONL 解析器（应对 docker logs 按字节 chunk 切分） ──


class LineBufferedJsonParser:
    """按 \\n 切分字节 chunk，拼完整行后 json.loads。
    用于 container.logs(stream=True) 输出（按 docker daemon 缓冲切分，不保证行边界）。
    """

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> Iterator[dict]:
        """喂一个 chunk，返回零或多条解析出的事件。"""
        if not chunk:
            return
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            yield from self._emit_line(line)

    def flush(self) -> Iterator[dict]:
        """EOF 时调用，处理最后一行（无 \\n 结尾）。"""
        if self._buffer:
            yield from self._emit_line(self._buffer)
            self._buffer = b""

    def _emit_line(self, raw: bytes) -> Iterator[dict]:
        line = raw.strip()
        if not line:
            return
        try:
            yield json.loads(line.decode("utf-8", errors="replace"))
            return
        except json.JSONDecodeError:
            pass
        # 非法 JSON：兜底为 raw 事件（不阻塞流）
        logger.warning(f"agent-runner 非 JSONL 行（跳过）: {line[:200]}")
        yield {
            "type": "raw",
            "content": line.decode("utf-8", errors="replace")[:500],
            "timestamp": time.time(),
        }


# ─ ── Spec ─ ──


@dataclass
class AgentRunnerSpec:
    """agent-runner 容器规格"""

    image: str = "crucible-agent-runner:base"
    cpu_limit: float = 1.0
    memory_limit: str = "1g"
    network: str | None = AGENT_RUNNER_NETWORK
    env: dict[str, str] = field(default_factory=dict)
    host_workdir: str = ""                  # bind mount 源（host 路径 → /workspace）
    prompt_json_filename: str = ".prompt.json"
    workdir_container: str = "/workspace"
    user: str = "1000:1000"
    extra_labels: dict[str, str] = field(default_factory=dict)
    pids_limit: int = 256
    network_disabled: bool = False           # True = 完全断网（强隔离）
    timeout_seconds: int = 1800


# ─ ── Manager ─ ──


class AgentRunnerManager:
    """agent-runner 容器生命周期管理（全局单例）。

    模式与原 SandboxManager 保持一致：create() 拉起容器；返回 AgentRunner 句柄。
    流式场景推荐使用 run_with_streaming() 一步完成"拉起 + 流消费 + 收尾"。
    """

    _instance: "AgentRunnerManager | None" = None

    def __new__(cls) -> "AgentRunnerManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        try:
            self._client = docker.from_env()
        except DockerException as e:
            raise AgentRunnerError(f"无法连接 Docker daemon: {e}") from e
        self._ensure_network()

    def _ensure_network(self) -> None:
        """确保专用网络存在（默认 internal=False，可外联 git clone）"""
        try:
            self._client.networks.get(AGENT_RUNNER_NETWORK)
        except NotFound:
            try:
                self._client.networks.create(
                    AGENT_RUNNER_NETWORK,
                    driver="bridge",
                    internal=False,
                    labels={"managed_by": "crucible-agent-runner"},
                )
            except DockerException as e:
                logger.warning(f"创建网络 {AGENT_RUNNER_NETWORK} 失败（继续）: {e}")

    # ── 容器编排 ──

    def create(self, spec: AgentRunnerSpec, name: str | None = None) -> "AgentRunner":
        """拉起 agent-runner 容器（不等待其结束）"""
        spec = self._resolve_defaults(spec)
        name = name or f"{AGENT_RUNNER_NAME_PREFIX}-{uuid.uuid4().hex[:8]}"

        if not spec.host_workdir:
            raise AgentRunnerError("AgentRunnerSpec.host_workdir 必须设置")

        # 资源限制
        nano_cpus = max(int(spec.cpu_limit * 1_000_000_000), 100_000_000)

        container_config: dict = {
            "image": spec.image,
            "name": name,
            "command": ["python", "-m", "runner.run_one"],
            "environment": spec.env,
            "working_dir": spec.workdir_container,
            "user": spec.user,
            "volumes": {
                spec.host_workdir: {"bind": spec.workdir_container, "mode": "rw"},
            },
            "labels": {
                "managed_by": "crucible-agent-runner",
                "agent_runner_id": name,
                **spec.extra_labels,
            },
            "nano_cpus": nano_cpus,
            "mem_limit": spec.memory_limit,
            "memswap_limit": spec.memory_limit,
            "pids_limit": spec.pids_limit,
            "read_only": True,
            # tmpfs：根只读下唯一可写区
            "tmpfs": {
                "/tmp": "rw,size=256m,nosuid,nodev,uid=1000,gid=1000,mode=1777",
            },
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "network_disabled": spec.network_disabled,
            "network": None if spec.network_disabled else spec.network,
            "log_config": LogConfig(
                type="json-file",
                config={"max-size": "10m", "max-file": "3"},
            ),
            "detach": True,
        }

        try:
            self._ensure_image(spec.image)
            container = self._client.containers.create(**container_config)
            container.start()
        except ImageNotFound:
            raise AgentRunnerError(f"agent-runner 镜像不存在: {spec.image}（先构建）")
        except DockerException as e:
            raise AgentRunnerError(f"agent-runner 容器创建失败: {e}") from e

        return AgentRunner(self._client, container, spec, name)

    def _ensure_image(self, image: str) -> None:
        """镜像不存在则拉取（默认本地已有；pull 留给运维）"""
        try:
            self._client.images.get(image)
        except NotFound:
            try:
                self._client.images.pull(image)
            except DockerException as e:
                raise AgentRunnerError(f"agent-runner 镜像拉取失败: {e}") from e

    def _resolve_defaults(self, spec: AgentRunnerSpec) -> AgentRunnerSpec:
        """用 settings 兜底"""
        if not spec.image:
            spec.image = settings.agent_runner_image
        if spec.cpu_limit is None or spec.cpu_limit <= 0:
            spec.cpu_limit = settings.agent_runner_cpu_limit
        if not spec.memory_limit:
            spec.memory_limit = settings.agent_runner_memory_limit
        if spec.network is None:
            spec.network = settings.agent_runner_network
        if not spec.timeout_seconds:
            spec.timeout_seconds = settings.agent_runner_timeout_seconds
        return spec

    # ── 一站式流式拉起 ──

    def run_with_streaming(
        self,
        spec: AgentRunnerSpec,
        on_event: Callable[[dict], None],
    ) -> tuple[int, dict]:
        """拉起 agent-runner 容器 + 流式消费 stdout JSONL + 收尾清理。

        全程同步：
          - 拉起容器（同步 docker SDK）
          - container.logs(stream=True, follow=True) 同步迭代
          - 行缓冲解析 → on_event(event) 同步回调
          - container.wait() 同步等待结束
          - 容器清理

        返回 (exit_code, summary)。
        """
        runner: AgentRunner | None = None
        parser = LineBufferedJsonParser()
        try:
            runner = self.create(spec)
            logger.info(f"agent-runner 容器启动: {runner.name} image={spec.image}")

            # 边消费边回调（行缓冲）
            for chunk in runner.container.logs(
                stream=True, follow=True, stdout=True, stderr=False
            ):
                if isinstance(chunk, bytes):
                    pass
                elif isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                else:
                    chunk = str(chunk).encode("utf-8")
                for event in parser.feed(chunk):
                    on_event(event)

            # EOF：flush 残余
            for event in parser.flush():
                on_event(event)

            # 等待容器结束
            wait_result = runner.container.wait()
            exit_code = int(wait_result.get("StatusCode", 1))

            # 检查 OOM
            runner.container.reload()
            state = runner.container.attrs.get("State", {}) or {}
            oom_killed = bool(state.get("OOMKilled", False))
            if oom_killed and exit_code == 137:
                logger.warning(f"agent-runner 容器 OOM kill: {runner.name}")

            # 失败时(非 0 且非 OOM)在删容器前抓 stderr,供 executor 诊断。
            # 此前 executor 在 finally 后才取,那时容器已删 → 永远空。
            stderr_tail = ""
            if exit_code != 0 and not oom_killed:
                try:
                    raw = runner.container.logs(tail=50, stdout=False, stderr=True)
                    if isinstance(raw, bytes):
                        stderr_tail = raw.decode("utf-8", errors="replace")
                    else:
                        stderr_tail = str(raw)
                except Exception:  # noqa: BLE001 — 抓 stderr 失败不阻断
                    pass

            summary = {
                "container_id": runner.id,
                "container_name": runner.name,
                "exit_code": exit_code,
                "oom_killed": oom_killed,
                "stderr_tail": stderr_tail,
            }
            return exit_code, summary

        except AgentRunnerError:
            raise
        except DockerException as e:
            raise AgentRunnerError(f"agent-runner 流式消费失败: {e}") from e
        finally:
            if runner is not None:
                try:
                    runner.stop_and_remove()
                except Exception:
                    pass

    def remove_by_id(self, container_id: str) -> None:
        """按 ID 移除容器（幂等）"""
        try:
            container = self._client.containers.get(container_id)
            container.remove(force=True, v=True)
        except NotFound:
            pass
        except DockerException as e:
            logger.warning(f"移除容器 {container_id} 失败: {e}")

    def cleanup_stale(self, max_age_seconds: int = 3600) -> int:
        """清理孤儿/过期 agent-runner 容器（保险 B）"""
        removed = 0
        try:
            for container in self._client.containers.list(
                all=True, filters={"label": "managed_by=crucible-agent-runner"}
            ):
                created = container.attrs.get("Created", "")
                if not created:
                    continue
                try:
                    created_dt = _parse_docker_time(created)
                except ValueError:
                    continue
                if time.time() - created_dt > max_age_seconds:
                    try:
                        container.remove(force=True, v=True)
                        removed += 1
                    except DockerException as e:
                        logger.warning(f"清理孤儿容器失败: {e}")
        except DockerException as e:
            logger.warning(f"cleanup_stale 扫描失败: {e}")
        return removed

    # ── 镜像/工具 ──

    def image_exists(self, image: str | None = None) -> bool:
        try:
            self._client.images.get(image or settings.agent_runner_image)
            return True
        except NotFound:
            return False
        except DockerException:
            return False

    def host_workdir_path(self, task_id: str) -> str:
        """根据 task_id 计算 host 临时目录路径(规范化为 OS 绝对路径)。

        settings.agent_runner_workdir_base 可能是 POSIX 风格(/tmp/...),
        Windows 下 os.path.abspath 会解析为当前盘符根(D:\\tmp\\...)。
        """
        base = settings.agent_runner_workdir_base.rstrip("/")
        return os.path.abspath(f"{base}-{task_id}")


# ─ ── AgentRunner 句柄 ─ ──


class AgentRunner:
    """单个 agent-runner 容器的操作句柄（最小化，仅留 stop / remove）"""

    def __init__(
        self,
        client: docker.DockerClient,
        container: docker.models.containers.Container,
        spec: AgentRunnerSpec,
        name: str,
    ) -> None:
        self._client = client
        self._container = container
        self.spec = spec
        self.name = name

    @property
    def id(self) -> str:
        return self._container.id

    @property
    def container(self) -> docker.models.containers.Container:
        return self._container

    def stop_and_remove(self, timeout: int = 10) -> None:
        """停止并移除容器（始终幂等）"""
        try:
            self._container.stop(timeout=timeout)
        except DockerException:
            pass
        try:
            self._container.remove(force=True, v=True)
        except DockerException:
            pass

    def read_logs(self, tail: int = 200) -> str:
        """读取容器日志（stdout+stderr）— 用于调试"""
        logs = self._container.logs(tail=tail, timestamps=False)
        if isinstance(logs, bytes):
            logs = logs.decode("utf-8", errors="replace")
        return logs


# ─ ── 单例 + 辅助 ─ ──

agent_runner_manager = AgentRunnerManager()


def _parse_docker_time(s: str) -> float:
    """解析 docker 返回的 ISO 时间字符串为 timestamp"""
    s = s.replace("Z", "+00:00")
    import datetime as _dt

    return _dt.datetime.fromisoformat(s).timestamp()


def git_clone_to_workdir(workdir: str, project_address: str, project_ref: str | None) -> tuple[bool, str]:
    """在 host 上 git clone 源码到 workdir/project。

    返回 (ok, error_or_empty)。clone 后校验 project 非空(防静默半失败)。
    """
    project_dir = os.path.join(workdir, "project")
    # 清理上次残留(retry 复用 host_workdir,旧 project 必须强删;只读文件也要删)
    if os.path.isdir(project_dir):

        def _force_remove(func, path, _exc):  # noqa: ANN001
            try:
                os.chmod(path, 0o777)
                func(path)
            except Exception:
                pass

        shutil.rmtree(project_dir, onerror=_force_remove)

    cmd = ["git", "clone", "--depth", "1"]
    if project_ref:
        cmd += ["--branch", project_ref]
    cmd += [project_address, project_dir]

    try:
        result = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout)[:1000]
        # clone 后校验:project 目录必须非空(防 .git 建了但 checkout 失败的静默半失败)
        if not os.path.isdir(project_dir):
            return False, f"clone 返回 0 但 project 目录不存在: {project_dir}"
        entries = [e for e in os.listdir(project_dir) if e != ".git"]
        if not entries:
            # .git 在但无工作区文件 = checkout 失败
            git_log = subprocess.run(
                ["git", "-C", project_dir, "log", "--oneline", "-1"],
                capture_output=True, text=True,
            ).stderr
            return False, f"clone 后 project 目录为空(checkout 失败): {(git_log or '')[:300]}"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "git clone 超时(>300s)"
    except Exception as e:
        return False, f"git clone 异常: {e}"


# ─ ── 异步包装（FastAPI 场景） ─ ──


async def run_with_streaming_async(
    spec: AgentRunnerSpec,
    on_event: Callable[[dict], None],
) -> tuple[int, dict]:
    """异步包装：run_with_streaming 是同步实现"""
    import asyncio

    return await asyncio.to_thread(agent_runner_manager.run_with_streaming, spec, on_event)


async def image_exists_async(image: str | None = None) -> bool:
    import asyncio

    return await asyncio.to_thread(agent_runner_manager.image_exists, image)


async def cleanup_stale_async(max_age_seconds: int = 3600) -> int:
    import asyncio

    return await asyncio.to_thread(agent_runner_manager.cleanup_stale, max_age_seconds)