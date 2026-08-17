"""run_one.py：蒸馏 skill 作 system_prompt；user 只带本轮 JSON；不加载桌面插件。"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "infrastructure",
        "agent-runner",
    ),
)

sys.modules.setdefault("claude_agent_sdk", MagicMock())

from runner.run_one import (  # noqa: E402
    NODE_AI_KEYS,
    _build_node_prompt,
    _build_options,
    _container_source_dir,
    _load_node_skill,
    _sdk_cwd,
)

ROOT = Path(__file__).resolve().parents[2]
NODE_SKILLS = ROOT / "infrastructure" / "agent-runner" / "node-skills"

_ROLE_LEAKS = (
    "你是项目画像员",
    "你是靶场工程师",
    "你是白盒审计员",
    "你是漏洞复现员",
    "你是误报路径的报告撰写员",
    "不要执行 docker compose",
    "源码目录:",
)


def test_build_node_prompt_is_thin_payload_only():
    for key in NODE_AI_KEYS:
        text = _build_node_prompt(key, {"foo": "bar", "source_path": "/workspace/demo"})
        assert "foo" in text
        assert "bar" in text
        assert "submit_result" in text
        assert "输入(JSON)" in text
        for leak in _ROLE_LEAKS:
            assert leak not in text, f"{key} user message 不应含 {leak!r}"


def test_env_ready_prompt_puts_attempt_in_json_not_header():
    text = _build_node_prompt(
        "env_ready",
        {"source_path": "/workspace/claudecodeui", "attempt": 1, "profile": {"language": "python"}},
    )
    assert "/workspace/claudecodeui" in text
    assert '"attempt": 1' in text or '"attempt":1' in text
    assert "不要执行 docker compose" not in text


def test_reproduce_prompt_includes_target_url_in_json():
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
    dumped = json.dumps({"gate_verdict": "pass"})
    assert "gate_verdict" in text
    assert dumped.split(":")[0].strip("{ ") in text or "gate_verdict" in text
    assert "靶场就绪" not in text


def test_node_skills_exist_and_are_sliced():
    assert NODE_AI_KEYS == frozenset(
        {"profile", "env_ready", "audit", "reproduce", "report"}
    )
    for key in NODE_AI_KEYS:
        path = NODE_SKILLS / key / "SKILL.md"
        assert path.is_file(), f"缺少 {path}"
        body = path.read_text(encoding="utf-8")
        assert "submit_result" in body
        if key == "audit":
            assert "一次 HTTP 请求测" not in body
            assert "不发" in body or "禁止" in body
        if key == "profile":
            assert "docker compose up" not in body.lower()
        if key == "env_ready":
            assert "不要执行" in body or "禁止" in body
        if key == "reproduce":
            assert "host.docker.internal" in body
            assert "report_data" in body
        if key == "report":
            assert "false_positive" in body


def test_load_node_skill_reads_distilled_file():
    text = _load_node_skill("profile")
    assert "画像" in text
    assert "submit_result" in text


def test_build_options_appends_skill_without_desktop_plugin():
    captured: dict = {}

    class CaptureOptions:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

    import runner.run_one as run_one

    run_one.ClaudeAgentOptions = CaptureOptions
    run_one._build_options(model="m", max_turns=8, node_key="profile", cwd="/workspace/x")

    assert "plugins" not in captured
    extra = captured.get("extra_args") or {}
    assert "agent" not in extra
    assert "plugin-dir" not in extra
    prompt = captured.get("system_prompt")
    assert isinstance(prompt, dict)
    assert prompt.get("preset") == "claude_code"
    assert "画像" in (prompt.get("append") or "")


def test_build_options_extra_args_is_dict_when_present():
    """SDK 0.2.x _build_command 走 extra_args.items()，list 会炸。"""
    captured: dict = {}

    class CaptureOptions:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

    import runner.run_one as run_one

    run_one.ClaudeAgentOptions = CaptureOptions
    run_one._build_options(model="m", max_turns=8, node_key="audit", cwd="/workspace/x")
    extra = captured.get("extra_args")
    if extra is not None:
        pairs = list(extra.items())
        for flag, _value in pairs:
            assert not str(flag).startswith("--"), flag
            assert flag != "agent"


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
