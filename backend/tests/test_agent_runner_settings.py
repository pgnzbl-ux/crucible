"""AgentRunnerSpec 必须采纳 Settings（.env），禁止 dataclass 默认值挡住配置。"""
from __future__ import annotations

from app.core.agent_runner import AgentRunnerSpec, agent_runner_manager
from app.core.config import get_settings


def test_resolve_defaults_uses_settings_not_dataclass_literals(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_runner_image", "custom-runner:test")
    monkeypatch.setattr(settings, "agent_runner_cpu_limit", 2.5)
    monkeypatch.setattr(settings, "agent_runner_memory_limit", "2g")
    monkeypatch.setattr(settings, "agent_runner_network", "custom-net")

    resolved = agent_runner_manager._resolve_defaults(AgentRunnerSpec(host_workdir="/tmp/x"))
    assert resolved.image == "custom-runner:test"
    assert resolved.cpu_limit == 2.5
    assert resolved.memory_limit == "2g"
    assert resolved.network == "custom-net"


def test_resolve_defaults_keeps_explicit_overrides(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_runner_image", "from-env:latest")
    resolved = agent_runner_manager._resolve_defaults(
        AgentRunnerSpec(host_workdir="/tmp/x", image="explicit:tag", cpu_limit=0.5)
    )
    assert resolved.image == "explicit:tag"
    assert resolved.cpu_limit == 0.5
