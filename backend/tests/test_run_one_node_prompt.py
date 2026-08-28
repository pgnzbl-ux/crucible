"""runner.gateway：prompt 只带本轮 JSON；options 契约；工作区发现。"""

import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "backend",
        "agent-runner",
    ),
)

sys.modules.setdefault("claude_agent_sdk", MagicMock())

import runner.gateway as gateway  # noqa: E402
from runner.schemas import AgentSpec  # noqa: E402

_ROLE_LEAKS = (
    "你是项目画像员",
    "你是靶场工程师",
    "你是白盒审计员",
    "你是漏洞复现员",
    "你是误报路径的报告撰写员",
    "不要执行 docker compose",
    "源码目录:",
)


def _spec(**overrides) -> AgentSpec:
    base = dict(node_key="audit", node_payload={"foo": "bar"}, submit_schema={"type": "object"})
    base.update(overrides)
    return AgentSpec(**base)


def test_task_prompt_is_thin_payload_only():
    """user message 只带本轮 JSON + 通用提交指令；任何业务角色文案不得进入网关。"""
    spec = _spec(node_payload={"foo": "bar", "source_path": "/workspace/demo"})
    text = gateway.build_task_prompt(spec)
    assert "foo" in text and "bar" in text
    assert "submit_result" in text
    assert "输入(JSON)" in text
    for leak in _ROLE_LEAKS:
        assert leak not in text, f"user message 不应含 {leak!r}"


def test_task_prompt_payload_roundtrip_includes_nested_input():
    spec = _spec(
        node_payload={
            "target_url": "http://10.0.0.8:8080",
            "initial_creds": {"username": "admin", "password": "admin123"},
            "attempt": 1,
        }
    )
    text = gateway.build_task_prompt(spec)
    assert "10.0.0.8:8080" in text
    assert "admin123" in text
    assert '"attempt": 1' in text or '"attempt":1' in text


def test_task_prompt_without_submit_contract_has_no_submit_instruction():
    spec = _spec(submit_schema=None)
    text = gateway.build_task_prompt(spec)
    assert "submit_result" not in text


def test_gateway_source_has_no_business_node_knowledge():
    """纯净网关红线：不含节点清单、schema、结论关键词、遗留叙事模板。"""
    src = (Path(gateway.__file__).read_text(encoding="utf-8"))
    for business_marker in (
        "_classify_conclusion", "漏洞存在", "reproduced", "误报",
        "project_address", "vulnerability_description", "阶段 A",
    ):
        assert business_marker not in src, f"gateway 不得包含业务残留: {business_marker}"


# ── options 组装 ──


class CaptureOptions:
    def __init__(self, **kwargs):
        CaptureOptions.captured = dict(kwargs)


@pytest.fixture()
def capture_options(monkeypatch):
    CaptureOptions.captured = {}
    monkeypatch.setattr(gateway, "ClaudeAgentOptions", CaptureOptions)
    return CaptureOptions


def test_build_options_appends_skill_without_desktop_plugin(capture_options):
    spec = _spec(skill_inline="画像 skill 正文", max_turns=8)
    gateway.build_options(spec, model="m", cwd="/workspace/x", hooks={})
    captured = capture_options.captured

    assert "plugins" not in captured
    extra = captured.get("extra_args") or {}
    assert "agent" not in extra
    assert "plugin-dir" not in extra
    prompt = captured.get("system_prompt")
    assert isinstance(prompt, dict)
    assert prompt.get("preset") == "claude_code"
    assert "画像" in (prompt.get("append") or "")
    assert captured["max_turns"] == 8


def test_build_options_keeps_full_automation_and_isolates_repo_config(capture_options):
    spec = _spec(skill_inline="s")
    hooks = {"PreToolUse": [object()], "Stop": [object()]}
    mcp = {"crucible": object()}
    gateway.build_options(spec, model="m", cwd="/workspace/x", hooks=hooks, mcp_servers=mcp)
    captured = capture_options.captured

    assert captured["permission_mode"] == "bypassPermissions"
    assert captured["tools"] == {"type": "preset", "preset": "claude_code"}
    assert captured["setting_sources"] == []
    assert captured["strict_mcp_config"] is True
    assert captured["sandbox"] == {"enabled": False}
    assert captured["hooks"] == hooks
    assert captured["mcp_servers"] == mcp
    assert "mcp__crucible__submit_result" in captured["allowed_tools"]
    assert "mcp__crucible__read_slice" in captured["allowed_tools"]
    assert "mcp__crucible__submit_result" not in gateway.BASE_ALLOWED_TOOLS


def test_build_options_extra_tools_and_no_mcp(capture_options):
    spec = _spec(submit_schema=None, allowed_tools_extra=["Task"])
    gateway.build_options(spec, model="m", cwd="/workspace", hooks={})
    captured = capture_options.captured
    assert "Task" in captured["allowed_tools"]
    assert "mcp_servers" not in captured
    assert "system_prompt" not in captured


def test_build_options_sets_effort_from_env(capture_options, monkeypatch):
    spec = _spec()
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "high")
    gateway.build_options(spec, model="m", cwd="/workspace", hooks={})
    assert capture_options.captured.get("effort") == "high"

    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "auto")
    gateway.build_options(spec, model="m", cwd="/workspace", hooks={})
    assert "effort" not in capture_options.captured


def test_build_mcp_servers_registers_submit_and_slice():
    stub = types.ModuleType("claude_agent_sdk")
    created: dict = {}

    def _fake_create_sdk_mcp_server(*, name, tools):
        created["name"] = name
        created["tools"] = tools
        return object()

    stub.create_sdk_mcp_server = _fake_create_sdk_mcp_server

    def _fake_tool(*args, **kwargs):
        def _wrap(fn):
            return fn
        return _wrap

    stub.tool = _fake_tool
    sys.modules["claude_agent_sdk"] = stub

    mcp = gateway.build_mcp_servers(
        _spec(submit_schema={"type": "object"}),
        workspace_root="/workspace",
        output_path="/workspace/.out.json",
    )
    assert created["name"] == "crucible"
    assert [t.__name__ for t in created["tools"]] == ["submit_result", "read_slice"]
    assert "crucible" in mcp

    assert gateway.build_mcp_servers(_spec(submit_schema=None), workspace_root="/workspace", output_path="/x") is None


# ── 工作区发现 ──


def test_container_source_dir_discovers_repo_when_project_missing(tmp_path):
    (tmp_path / "claudecodeui").mkdir()
    spec = _spec(source_path="/workspace/project", workspace_root=str(tmp_path))
    assert gateway.container_source_dir(spec) == str(tmp_path / "claudecodeui")


def test_container_source_dir_keeps_explicit_repo_path_without_disk(tmp_path):
    spec = _spec(source_path="/workspace/claudecodeui", workspace_root="/workspace")
    assert gateway.container_source_dir(spec) == "/workspace/claudecodeui"


def test_sdk_cwd_never_uses_missing_project_dir(tmp_path):
    spec = _spec(source_path="/workspace/project", workspace_root=str(tmp_path))
    got = gateway.sdk_cwd(spec)
    assert got == str(tmp_path)
    assert not got.endswith("/project")


def test_skill_loading_prefers_inline_then_path(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("文件版 skill", encoding="utf-8")
    assert gateway.load_skill(_spec(skill_path=str(skill_file))) == "文件版 skill"
    assert gateway.load_skill(_spec(skill_path=str(skill_file), skill_inline="内联版")) == "内联版"
    assert gateway.load_skill(_spec()) is None
    with pytest.raises(FileNotFoundError):
        gateway.load_skill(_spec(skill_path=str(tmp_path / "nope.md")))
