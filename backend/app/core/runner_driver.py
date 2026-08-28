"""沙箱计算驱动抽象层 (借鉴 OpenStack Nova Compute Driver 模式)。

将 Agent 沙箱的底层运行时（Docker / Kubernetes / MicroVM）与上层 AI 节点编排解耦：
- BaseRunnerDriver: 统一的容器生命周期与流式消费协议
- DockerRunnerDriver: 基于本地 Docker daemon 的标准驱动实现
- （未来可扩展）KubernetesJobRunnerDriver: 云原生 K8s Job 驱动
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class BaseRunnerDriver(Protocol):
    """沙箱运行驱动抽象协议。"""

    def create(self, spec: Any, name: str | None = None) -> Any:
        """拉起沙箱容器/Pod（不阻塞等待结束）。"""
        ...

    def run_with_streaming(
        self,
        spec: Any,
        on_event: Callable[[dict], None],
    ) -> tuple[int, dict]:
        """拉起沙箱 + 同步流式消费日志 + 结束后清理，返回 (exit_code, summary)。"""
        ...

    def remove_by_id(self, container_id: str) -> None:
        """强制销毁沙箱实例。"""
        ...

    def cleanup_stale(self, max_age_seconds: int = 7200) -> int:
        """巡检并清理超期孤儿沙箱。"""
        ...

    def image_exists(self, image: str | None = None) -> bool:
        """检查运行镜像是否存在。"""
        ...
