"""run_one.py 节点 prompt：必须存在且吃 input_json。"""
import json
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "infrastructure", "agent-runner"))

# 容器入口依赖 SDK；单测只验证 prompt 构造，不真调 query()
sys.modules.setdefault("claude_agent_sdk", MagicMock())

from runner.run_one import (  # noqa: E402
    NODE_AGENT_MAP,
    _build_node_prompt,
    _build_options,
    _container_source_dir,
    _sdk_cwd,
)


def test_build_node_prompt_defined_for_all_ai_nodes():
    for key in NODE_AGENT_MAP:
        text = _build_node_prompt(key, {"foo": "bar"})
        assert "foo" in text
        assert "bar" in text
        assert "submit_result" in text.lower() or "submit_result" in text


def test_env_ready_prompt_points_at_workspace_source():
    text = _build_node_prompt(
        "env_ready",
        {"source_path": "/workspace/claudecodeui", "attempt": 1, "profile": {"language": "python"}},
    )
    assert "/workspace/claudecodeui" in text
    assert "submit_result" in text
    assert "不要执行 docker compose" in text or "不要自己执行 docker compose" in text


def test_reproduce_prompt_includes_target_url():
    text = _build_node_prompt(
        "reproduce",
        {
            "target_url": "http://host.docker.internal:8080",
            "initial_creds": {"username": "admin", "password": "admin123"},
            "audit": {"gate_verdict": "pass"},
        },
    )
    assert "host.docker.internal:8080" in text
    assert "admin123" in text
    assert "靶场就绪" in text
    assert "submit_result" in text
    dumped = json.dumps({"gate_verdict": "pass"})
    assert "gate_verdict" in text


def test_build_options_extra_args_is_dict_for_sdk_cli():
    """SDK 0.2.x _build_command 走 extra_args.items()，list 会炸 'list' object has no attribute 'items'。"""
    import runner.run_one as run_one

    captured: dict = {}

    class CaptureOptions:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

    run_one.ClaudeAgentOptions = CaptureOptions
    run_one._build_options(model="m", max_turns=8, node_key="profile", cwd="/workspace/x")

    extra = captured["extra_args"]
    # 与 SDK subprocess_cli._build_command 同一用法
    pairs = list(extra.items())
    assert ("agent", "vuln-verify-expert:profiler") in pairs
    for flag, _value in pairs:
        assert not str(flag).startswith("--"), flag


def test_container_source_dir_discovers_repo_when_project_missing(tmp_path):
    (tmp_path / "claudecodeui").mkdir()
    got = _container_source_dir(
        {"source_path": "/workspace/project"},
        workspace_root=str(tmp_path),
    )
    assert got == "/workspace/claudecodeui"


def test_container_source_dir_keeps_explicit_repo_path_without_disk():
    assert _container_source_dir({"source_path": "/workspace/claudecodeui"}) == "/workspace/claudecodeui"


def test_sdk_cwd_never_uses_missing_project_dir(tmp_path):
    got = _sdk_cwd(
        {"source_path": "/workspace/project"},
        workspace_root=str(tmp_path),
    )
    assert got == "/workspace"
    assert not got.endswith("/project")
