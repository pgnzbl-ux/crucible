"""run_one.py Bash 策略：env_ready 拒绝装依赖与 docker。"""
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "infrastructure", "agent-runner"))

# 容器入口依赖 SDK；单测只验证 Bash 分类，不真调 query()
sys.modules.setdefault("claude_agent_sdk", MagicMock())

import pytest
from runner.run_one import _allowed_tools_for, _classify_bash


@pytest.mark.parametrize(
    "cmd",
    ["npm install", "npx eslint", "pip install flask", "apt-get update", "docker compose up"],
)
def test_env_ready_denies_install_and_docker(cmd):
    decision, reason = _classify_bash(cmd, node_key="env_ready")
    assert decision == "deny"
    assert reason and "blocked by policy" in reason


def test_env_ready_allows_node_eval():
    decision, _ = _classify_bash("node -e \"console.log(1)\"", node_key="env_ready")
    assert decision == "allow"


def test_audit_still_allows_npm():
    decision, _ = _classify_bash("npm install", node_key="audit")
    assert decision == "allow"


@pytest.mark.parametrize("cmd", ["curl http://x", "wget http://x", "httpie GET x"])
def test_audit_denies_http_clients(cmd):
    decision, reason = _classify_bash(cmd, node_key="audit")
    assert decision == "deny"
    assert reason and "blocked by policy" in reason


def test_reproduce_still_allows_curl():
    decision, _ = _classify_bash("curl -s http://x", node_key="reproduce")
    assert decision == "allow"


@pytest.mark.parametrize("cmd", ["docker compose up", "docker-compose up", "docker ps"])
def test_reproduce_denies_docker(cmd):
    decision, reason = _classify_bash(cmd, node_key="reproduce")
    assert decision == "deny"
    assert reason and "docker" in reason


def test_reproduce_keeps_write_and_webfetch():
    tools = _allowed_tools_for("reproduce")
    assert "Write" in tools and "WebFetch" in tools and "Bash" in tools


def test_audit_allowed_tools_are_read_plus_bash_websearch():
    tools = _allowed_tools_for("audit")
    assert tools == ["Read", "Grep", "Glob", "Bash", "WebSearch"]
    assert "Write" not in tools
    assert "WebFetch" not in tools


def test_env_ready_allowed_tools_keep_write():
    tools = _allowed_tools_for("env_ready")
    assert "Write" in tools and "WebFetch" in tools
