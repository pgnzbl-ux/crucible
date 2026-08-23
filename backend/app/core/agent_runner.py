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
AGENT_EXTRA_HOSTS = {"host.docker.internal": "host-gateway"}
# 自定义 bridge 在 Docker Desktop 上 127.0.0.11 经常解析不了公网；写死公共 DNS。
AGENT_RUNNER_DNS = ["223.5.5.5", "8.8.8.8", "1.1.1.1"]


class AgentRunnerError(Exception):
    """agent-runner 容器编排失败"""


def _mount_matches_workdir(source: str, workdir: str) -> bool:
    """比较 Docker Mount.Source 与任务 host_workdir（容忍斜杠差异）。"""
    if not source or not workdir:
        return False
    a = os.path.normcase(os.path.normpath(source.replace("/", os.sep)))
    b = os.path.normcase(os.path.normpath(workdir.replace("/", os.sep)))
    return a == b


def _task_id_from_workdir(path: str) -> str:
    name = os.path.basename(os.path.abspath(path or ""))
    if name.startswith("audit-"):
        return name[6:]
    return ""


def _runner_labels(name: str, spec: AgentRunnerSpec) -> dict[str, str]:
    labels = {
        "managed_by": "crucible-agent-runner",
        "agent_runner_id": name,
        **(spec.extra_labels or {}),
    }
    tid = (
        labels.get("crucible.task_id")
        or labels.get("task_id")
        or _task_id_from_workdir(spec.host_workdir)
    )
    if tid:
        labels["crucible.task_id"] = tid
    return labels


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
    # 当前节点 skill 目录（host）→ /node-skill:ro；只挂本节点，不进镜像
    skill_host_dir: str | None = None
    prompt_json_filename: str = ".prompt.json"
    workdir_container: str = "/workspace"
    user: str = "1000:1000"
    extra_labels: dict[str, str] = field(default_factory=dict)
    pids_limit: int = 256
    network_disabled: bool = False           # True = 完全断网（强隔离）
    # 用空 tmpfs 遮蔽 /workspace 下的敏感子路径（如 .secrets/），轻工位节点用
    hide_workspace_paths: tuple[str, ...] = ()


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
            # 只建空壳：Docker 连接惰性建立，daemon 不可达不应炸掉 import
            # 该模块的所有代码（含 preflight 的友好报错路径）
            cls._instance._client = None  # type: ignore[assignment]
            cls._instance._active_ids = set()
        return cls._instance

    def _client_or_connect(self):
        """惰性连接 Docker daemon；失败抛 AgentRunnerError（调用方转节点失败）。

        Agent 允许无限运行，因此 Docker 流式读取也不能使用总时长 read timeout；
        任务取消时由 remove_for_task / stop_and_remove 主动中断容器。
        """
        if self._client is None:
            try:
                self._client = docker.from_env(timeout=None)
            except DockerException as e:
                raise AgentRunnerError(f"无法连接 Docker daemon: {e}") from e
            self._ensure_network()
        return self._client

    def _ensure_network(self) -> None:
        """确保专用网络存在且可外联（internal=False）。旧沙箱若建成 internal 则重建。"""
        try:
            net = self._client.networks.get(AGENT_RUNNER_NETWORK)
        except DockerException as e:
            # daemon 瞬断 / 权限异常：不阻断 import 与后续重连
            logger.warning("检查网络 %s 失败（下次重试）: %s", AGENT_RUNNER_NETWORK, e)
            return
        except NotFound:
            net = None
        if net is not None:
            if not net.attrs.get("Internal"):
                return
            logger.warning("网络 %s 为 internal，重建为可外联", AGENT_RUNNER_NETWORK)
            try:
                net.remove()
            except DockerException as e:
                logger.warning("无法删除 internal 网络 %s（可能仍有容器）: %s", AGENT_RUNNER_NETWORK, e)
                return
        try:
            self._client.networks.create(
                AGENT_RUNNER_NETWORK,
                driver="bridge",
                internal=False,
                options={
                    "com.docker.network.bridge.enable_ip_masquerade": "true",
                },
                labels={"managed_by": "crucible-agent-runner"},
            )
        except DockerException as e:
            logger.warning("创建网络 %s 失败（继续）: %s", AGENT_RUNNER_NETWORK, e)

    # ── 容器编排 ──

    def create(self, spec: AgentRunnerSpec, name: str | None = None) -> "AgentRunner":
        """拉起 agent-runner 容器（不等待其结束）"""
        spec = self._resolve_defaults(spec)
        name = name or f"{AGENT_RUNNER_NAME_PREFIX}-{uuid.uuid4().hex[:8]}"

        if not spec.host_workdir:
            raise AgentRunnerError("AgentRunnerSpec.host_workdir 必须设置")

        # 资源限制
        nano_cpus = max(int(spec.cpu_limit * 1_000_000_000), 100_000_000)

        volumes: dict[str, dict[str, str]] = {
            spec.host_workdir: {"bind": spec.workdir_container, "mode": "rw"},
        }
        if spec.skill_host_dir:
            volumes[spec.skill_host_dir] = {"bind": "/node-skill", "mode": "ro"}

        container_config: dict = {
            "image": spec.image,
            "name": name,
            # 不覆盖镜像 ENTRYPOINT（tini → python -m runner.run_one）
            "environment": {**spec.env, "PYTHONPATH": "/app"},
            "working_dir": spec.workdir_container,
            "user": spec.user,
            "volumes": volumes,
            "labels": _runner_labels(name, spec),
            "nano_cpus": nano_cpus,
            "mem_limit": spec.memory_limit,
            "memswap_limit": spec.memory_limit,
            "pids_limit": spec.pids_limit,
            "read_only": True,
            # tmpfs：根只读下唯一可写区
            "tmpfs": {
                "/tmp": "rw,size=256m,nosuid,nodev,uid=1000,gid=1000,mode=1777",
                **{
                    p: "rw,size=1m,nosuid,nodev,uid=1000,gid=1000,mode=700"
                    for p in spec.hide_workspace_paths
                },
            },
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "network_disabled": spec.network_disabled,
            "network": None if spec.network_disabled else spec.network,
            "extra_hosts": AGENT_EXTRA_HOSTS,
            "dns": None if spec.network_disabled else AGENT_RUNNER_DNS,
            "log_config": LogConfig(
                type="json-file",
                config={"max-size": "10m", "max-file": "3"},
            ),
            "detach": True,
        }

        try:
            client = self._client_or_connect()
            self._ensure_image(spec.image)
            container = client.containers.create(**container_config)
            container.start()
        except ImageNotFound:
            raise AgentRunnerError(f"agent-runner 镜像不存在: {spec.image}（先构建）")
        except DockerException as e:
            raise AgentRunnerError(f"agent-runner 容器创建失败: {e}") from e

        if not hasattr(self, "_active_ids"):
            self._active_ids = set()
        self._active_ids.add(container.id)
        return AgentRunner(client, container, spec, name)

    def _ensure_image(self, image: str) -> None:
        """镜像不存在则拉取（默认本地已有；pull 留给运维）"""
        client = self._client_or_connect()
        try:
            client.images.get(image)
        except NotFound:
            try:
                client.images.pull(image)
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
        if not hasattr(self, "_active_ids"):
            self._active_ids = set()
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

            # logs(follow=True) 结束后容器应已退出；不按运行时长强制 stop/kill。
            wait_result = runner.container.wait(timeout=None)
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
                    raw = runner.container.logs(tail=80, stdout=False, stderr=True)
                    if isinstance(raw, bytes):
                        stderr_tail = raw.decode("utf-8", errors="replace")
                    else:
                        stderr_tail = str(raw)
                    # stderr-only 常为空；python -m 的 ModuleNotFound 也可能落在未分离的 stdout
                    if not stderr_tail.strip():
                        raw = runner.container.logs(tail=80)
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
                "timed_out": False,
                "stop_failed": "",
            }
            return exit_code, summary

        except AgentRunnerError:
            raise
        except DockerException as e:
            raise AgentRunnerError(f"agent-runner 流式消费失败: {e}") from e
        finally:
            if runner is not None:
                self._active_ids.discard(runner.id)
                try:
                    runner.stop_and_remove()
                except Exception:
                    pass

    def remove_for_workdir(self, host_workdir: str) -> int:
        """删除 bind 了该 host_workdir 的 agent-runner（取消时 worker 可能已被杀掉）。"""
        if not host_workdir:
            return 0
        removed = 0
        try:
            containers = self._client_or_connect().containers.list(
                all=True, filters={"label": "managed_by=crucible-agent-runner"}
            )
        except (DockerException, AgentRunnerError) as e:
            logger.warning("列举 agent-runner 失败: %s", e)
            return 0
        for container in containers:
            try:
                attrs = container.attrs or {}
                mounts = attrs.get("Mounts") or []
                if not mounts:
                    container.reload()
                    mounts = (container.attrs or {}).get("Mounts") or []
                if not any(
                    _mount_matches_workdir(m.get("Source", ""), host_workdir) for m in mounts
                ):
                    continue
                self.remove_by_id(container.id)
                removed += 1
            except Exception:  # noqa: BLE001
                logger.warning("按工作区移除 agent-runner 失败", exc_info=True)
        return removed

    def remove_for_task(self, task_id: str, host_workdir: str | None = None) -> int:
        """按 crucible.task_id 标签拆 runner，并兜底按工作区挂载再扫一遍。"""
        if not task_id:
            return self.remove_for_workdir(host_workdir or "")
        removed = 0
        try:
            containers = self._client_or_connect().containers.list(
                all=True, filters={"label": f"crucible.task_id={task_id}"}
            )
        except (DockerException, AgentRunnerError) as e:
            logger.warning("按 task_id 列举 agent-runner 失败: %s", e)
            containers = []
        for container in containers:
            try:
                self.remove_by_id(container.id)
                removed += 1
            except Exception:  # noqa: BLE001
                logger.warning("按 task_id 移除 agent-runner 失败", exc_info=True)
        if host_workdir:
            removed += self.remove_for_workdir(host_workdir)
        return removed

    def remove_by_id(self, container_id: str) -> None:
        """按 ID 移除容器（幂等）"""
        if hasattr(self, "_active_ids"):
            self._active_ids.discard(container_id)
        try:
            container = self._client_or_connect().containers.get(container_id)
            container.remove(force=True, v=True)
        except NotFound:
            pass
        except (DockerException, AgentRunnerError) as e:
            logger.warning(f"移除容器 {container_id} 失败: {e}")

    def stop_all_active(self) -> int:
        """SIGTERM / 取消任务时强杀本 worker 仍登记的 agent-runner。"""
        if not hasattr(self, "_active_ids"):
            return 0
        removed = 0
        for cid in list(self._active_ids):
            try:
                self.remove_by_id(cid)
                removed += 1
            except Exception:  # noqa: BLE001
                self._active_ids.discard(cid)
        return removed

    def cleanup_stale(self, max_age_seconds: int = 3600) -> int:
        """清理孤儿/过期 agent-runner 容器（保险 B）"""
        removed = 0
        try:
            for container in self._client_or_connect().containers.list(
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
            self._client_or_connect().images.get(image or settings.agent_runner_image)
            return True
        except NotFound:
            return False
        except (DockerException, AgentRunnerError):
            return False

    def host_workdir_path(self, task_id: str) -> str:
        """根据 task_id 计算 host 临时目录路径(规范化为 OS 绝对路径)。

        settings.agent_runner_workdir_base 为 POSIX 路径（如 /tmp/crucible/audit）。
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
    """解析 docker 返回的 ISO 时间字符串为 UTC unix。naive 按 UTC，禁止当成本地时。"""
    from datetime import datetime

    from app.shared.time import utc_unix

    return utc_unix(datetime.fromisoformat(s.replace("Z", "+00:00"))) or 0.0


def _git_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(key, None)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def normalize_host_workdir(path: str) -> str:
    """把 settings/DB 中的 POSIX workdir（/tmp/...）规范化为 Linux 绝对路径。"""
    cleaned = (path or "").strip().replace("\\", "/")
    if not cleaned:
        raise ValueError("workdir 不能为空")
    if not cleaned.startswith("/"):
        raise ValueError(f"workdir 必须是 POSIX 绝对路径（以 / 开头）: {path!r}")
    return os.path.abspath(cleaned)


def git_clone_to_workdir(
    workdir: str,
    project_address: str,
    project_ref: str | None,
    dest_dirname: str | None = None,
    *,
    ref_type: str | None = None,
    clone_depth: int | None = 1,
) -> tuple[bool, str]:
    """在 host 上 git clone 到 workdir/{dest_dirname}（仓库名，而非固定 project）。

    ref_type 可选 branch|tag|commit；省略则自动推断。clone_depth=0 时不加 --depth（全量 clone）。
    返回 (ok, error_or_empty)。失败信息带「源码克隆失败」前缀，便于节点 0 展示。
    """
    from app.contexts.project.git_url import resolve_ref_type

    workdir = normalize_host_workdir(workdir)
    try:
        os.makedirs(workdir, exist_ok=True)
    except OSError as exc:
        return False, f"源码克隆失败: {exc}"

    name = dest_dirname or _dirname_from_url(project_address)
    project_dir = os.path.join(workdir, name)
    if os.path.isdir(project_dir):

        def _force_remove(func, path, _exc):  # noqa: ANN001
            try:
                os.chmod(path, 0o777)
                func(path)
            except Exception:
                pass

        shutil.rmtree(project_dir, onerror=_force_remove)

    git_env = _git_subprocess_env()
    rt, rn = resolve_ref_type(ref_type, project_ref)
    depth = 1 if clone_depth is None else clone_depth
    cmd = ["git", "clone"]
    if depth > 0:
        cmd += ["--depth", str(depth)]
    if rn and rt != "commit" and rn.upper() != "HEAD":
        cmd += ["--branch", rn]
    cmd += [project_address, project_dir]

    fetch_depth = depth if depth > 0 else 1

    try:
        result = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=300,
            env=git_env,
        )
        if result.returncode != 0:
            return False, _classify_clone_error(result.stderr or result.stdout)
        if rt == "commit" and rn:
            co = subprocess.run(
                [
                    "git", "-C", project_dir, "fetch",
                    "--depth", str(fetch_depth), "origin", rn,
                ],
                capture_output=True, text=True, timeout=120, env=git_env,
            )
            if co.returncode != 0:
                return False, _classify_clone_error(co.stderr or co.stdout or "无法获取指定 commit")
            ck = subprocess.run(
                ["git", "-C", project_dir, "checkout", rn],
                capture_output=True, text=True, timeout=60, env=git_env,
            )
            if ck.returncode != 0:
                return False, f"源码克隆失败: 引用不存在或无法检出: {(ck.stderr or ck.stdout)[:300]}"
        if not os.path.isdir(project_dir):
            return False, f"源码克隆失败: clone 返回 0 但目录不存在: {project_dir}"
        entries = [e for e in os.listdir(project_dir) if e != ".git"]
        if not entries:
            return False, f"源码克隆失败: 工作区为空（checkout 失败）: {project_dir}"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "源码克隆失败: 网络超时（>300s）"
    except Exception as e:
        return False, f"源码克隆失败: {e}"


def _dirname_from_url(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    if name.lower().endswith(".git"):
        name = name[: -len(".git")]
    return name or "repo"


def _looks_like_commit(ref: str) -> bool:
    import re
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref))


def _classify_clone_error(stderr: str) -> str:
    text = (stderr or "").strip()
    low = text.lower()
    snippet = text[:400]
    if "could not resolve" in low or "name or service not known" in low or "nodename nor servname" in low:
        return f"源码克隆失败: 网络错误（无法解析主机）: {snippet}"
    if "timed out" in low or "timeout" in low or "failed to connect" in low or "connection refused" in low:
        return f"源码克隆失败: 网络错误: {snippet}"
    if "remote branch" in low and "not found" in low:
        return f"源码克隆失败: 分支/tag 不存在: {snippet}"
    if "not found" in low or "authentication failed" in low or "permission denied" in low or "could not read username" in low:
        return f"源码克隆失败: 仓库不存在或无权访问: {snippet}"
    return f"源码克隆失败: {snippet or '未知 git 错误'}"


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
