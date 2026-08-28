"""统一服务目录与组件注册表 (借鉴 OpenStack Keystone Service Catalog 模式)。

集中管理平台内部组件与下游微服务（Scanner, Parser, LLM Gateway, Lab Manager, Agent Runner, MinIO）
的访问端点、协议配置与健康状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.config import get_settings


class ServiceType(StrEnum):
    SCANNER = "scanner"
    PARSER = "parser"
    LLM_GATEWAY = "llm_gateway"
    LAB_MANAGER = "lab_manager"
    AGENT_RUNNER = "agent_runner"
    OBJECT_STORE = "object_store"


@dataclass(frozen=True)
class ServiceEndpoint:
    name: str
    service_type: ServiceType
    protocol: str  # in_process | grpc | http | s3 | docker
    endpoint_url: str = ""
    is_enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class ServiceCatalog:
    """平台服务目录管理器。"""

    def __init__(self) -> None:
        self._endpoints: dict[str, ServiceEndpoint] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        settings = get_settings()

        # 静态扫描引擎
        self.register(
            ServiceEndpoint(
                name="scanner-semgrep",
                service_type=ServiceType.SCANNER,
                protocol="in_process",
                endpoint_url="",
                is_enabled=settings.scanner_semgrep_enabled,
            )
        )
        self.register(
            ServiceEndpoint(
                name="scanner-gitleaks",
                service_type=ServiceType.SCANNER,
                protocol="in_process",
                endpoint_url="",
                is_enabled=settings.scanner_gitleaks_enabled,
            )
        )
        self.register(
            ServiceEndpoint(
                name="scanner-osv",
                service_type=ServiceType.SCANNER,
                protocol="in_process",
                endpoint_url="https://api.osv.dev",
                is_enabled=settings.scanner_osv_enabled,
            )
        )

        # 轻量 LLM 网关
        self.register(
            ServiceEndpoint(
                name="llm-gateway",
                service_type=ServiceType.LLM_GATEWAY,
                protocol="in_process",
                is_enabled=settings.llm_gateway_enabled,
            )
        )

        # Agent 沙箱执行器
        self.register(
            ServiceEndpoint(
                name="agent-runner-docker",
                service_type=ServiceType.AGENT_RUNNER,
                protocol="docker",
                endpoint_url="unix:///var/run/docker.sock",
                is_enabled=settings.claude_agent_sdk_enabled,
                metadata={"image": settings.agent_runner_image},
            )
        )

        # 对象存储
        self.register(
            ServiceEndpoint(
                name="object-store-minio",
                service_type=ServiceType.OBJECT_STORE,
                protocol="s3",
                endpoint_url=settings.s3_endpoint,
                is_enabled=bool(settings.s3_endpoint),
                metadata={"secure": settings.s3_secure},
            )
        )

    def register(self, endpoint: ServiceEndpoint) -> None:
        self._endpoints[endpoint.name] = endpoint

    def get(self, name: str) -> ServiceEndpoint | None:
        return self._endpoints.get(name)

    def list_by_type(self, service_type: ServiceType) -> list[ServiceEndpoint]:
        return [ep for ep in self._endpoints.values() if ep.service_type == service_type]


catalog = ServiceCatalog()
