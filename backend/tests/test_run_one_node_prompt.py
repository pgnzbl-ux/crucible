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
    NODE_INPUT_SCHEMAS,
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


def test_reproduce_submit_schema_rejects_empty_evidence_fields():
    evidence = NODE_INPUT_SCHEMAS["reproduce"]["properties"]["evidence"]
    item = evidence["items"]

    assert item["type"] == "object"
    assert item["required"] == ["type", "detail"]
    assert item["properties"]["type"]["minLength"] == 1
    assert item["properties"]["detail"]["minLength"] == 1


def test_submit_schema_is_single_source_shared_across_boundary():
    """run_one 只从 runner.node_schemas 复用同一对象，禁止再各自维护副本。

    历史上后端 ai_runner 与容器 run_one 各存一份手工同步的 NODE_INPUT_SCHEMAS，
    已多次漂移（evidence.items 约束、字段 description 不一致）。此后单一真相在
    runner.node_schemas，两侧 import 同一对象，物理上杜绝漂移。
    """
    from runner import node_schemas

    assert NODE_INPUT_SCHEMAS is node_schemas.NODE_INPUT_SCHEMAS
    assert set(NODE_INPUT_SCHEMAS) == set(NODE_AI_KEYS)


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
            assert "没有 Docker CLI" in body
            assert "不要寻找历史会话" in body
            assert "上一轮产物" in body
            assert "failed_stage" in body
        if key == "reproduce":
            assert "host.docker.internal" in body
            assert "attempts" in body
            assert "`type`" in body
            assert "`detail`" in body
            assert "report_data" in body
            assert "禁止" in body
        if key == "report":
            assert "verification_record" in body
            assert "vulnerability_report" in body
            assert "expected_verdict" in body


def test_load_node_skill_reads_distilled_file():
    text = _load_node_skill("profile")
    assert "画像" in text
    assert "submit_result" in text


def test_env_ready_skill_requires_creds_lookup():
    text = _load_node_skill("env_ready")
    assert "auth_required" in text
    assert "不要交空对象" in text
    assert "先判断是否存在登录功能" in text
    assert "公开 dashboard" in text
    assert "仅修改 `.vuln-env`" in text
    assert "初始化靶场专用账号" in text
    assert "禁止修改项目业务源码" in text
    schema = NODE_INPUT_SCHEMAS["env_ready"]
    desc = schema["properties"]["initial_creds"]["description"]
    assert "auth_required" in desc
    assert "无登录功能" in desc
    assert "initial_creds" in schema["required"]
    assert len(schema["properties"]["initial_creds"]["anyOf"]) == 3
    assert "credential_lookup_only=true" in text


def test_env_ready_skill_requires_recon_and_one_process():
    text = _load_node_skill("env_ready")
    assert "build.context" in text
    assert "禁止把源码复制" in text
    assert "一容器一进程" in text
    assert "Could not transfer" in text
    assert "不要合并容器" in text
    assert "stdout" in text


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


def test_audit_skill_requires_payload_template():
    text = _load_node_skill("audit")
    assert "core_claim" in text
    assert "expected_observable" in text
    assert "method" in text


def test_reproduce_skill_drops_attempt_cap_and_requires_first_shot():
    text = _load_node_skill("reproduce")
    assert "上限 5 次" not in text
    assert "payloads[0]" in text
    assert "判定即停" in text
    assert "poc" in text
    assert "python" in text.lower()


def test_report_skill_poc_is_platform_owned():
    text = _load_node_skill("report")
    assert "覆盖" in text or "正本" in text
    assert "禁止改写" in text
