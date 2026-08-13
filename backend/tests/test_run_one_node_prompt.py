"""run_one.py 节点 prompt：必须存在且吃 input_json。"""
import json
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "infrastructure", "agent-runner"))

# 容器入口依赖 SDK；单测只验证 prompt 构造，不真调 query()
sys.modules.setdefault("claude_agent_sdk", MagicMock())

from runner.run_one import _build_node_prompt, NODE_AGENT_MAP  # noqa: E402


def test_build_node_prompt_defined_for_all_ai_nodes():
    for key in NODE_AGENT_MAP:
        text = _build_node_prompt(key, {"foo": "bar"})
        assert "foo" in text
        assert "bar" in text
        assert "submit_result" in text.lower() or "submit_result" in text


def test_env_ready_prompt_points_at_workspace_project():
    text = _build_node_prompt(
        "env_ready",
        {"source_path": "/host/tmp/w", "attempt": 1, "profile": {"language": "python"}},
    )
    assert "/workspace/project" in text
    assert "docker compose" in text.lower() or "compose" in text


def test_reproduce_prompt_includes_target_url():
    text = _build_node_prompt(
        "reproduce",
        {"target_url": "http://host.docker.internal:8080", "audit": {"gate_verdict": "pass"}},
    )
    assert "host.docker.internal:8080" in text
    dumped = json.dumps({"gate_verdict": "pass"})
    assert "gate_verdict" in text
