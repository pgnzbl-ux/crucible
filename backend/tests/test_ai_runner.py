"""ai_runner output 校验测试(不起真容器)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.contexts.agent.ai_runner import (
    NODE_INPUT_SCHEMAS,
    _mock_output,
    rewrite_url_for_agent_container,
    validate_output,
)


def test_validate_env_ready_ok():
    ok, err = validate_output(
        "env_ready",
        {
            "target_url": "http://x:8080",
            "compose_path": ".vuln-env/docker-compose.yml",
            "initial_creds": {"username": "admin", "password": "admin123"},
        },
    )
    assert ok and err is None


def test_validate_env_ready_requires_initial_creds():
    """env_ready 必须明确交回完整账密、免登录或凭据来源说明。"""
    ok, err = validate_output("env_ready", {"target_url": "http://x:8080"})
    assert not ok  # 缺 compose_path
    assert "compose_path" in err
    # 补 compose_path 仍缺 initial_creds
    ok2, _ = validate_output("env_ready", {"target_url": "http://x:8080", "compose_path": "x.yml"})
    assert not ok2


@pytest.mark.parametrize(
    "initial_creds",
    [
        {},
        {"username": "admin"},
        {"password": "admin123"},
        {"auth_required": True},
        {"note": "  "},
    ],
)
def test_validate_env_ready_rejects_ambiguous_initial_creds(initial_creds):
    ok, err = validate_output(
        "env_ready",
        {
            "target_url": "http://x:8080",
            "compose_path": ".vuln-env/docker-compose.yml",
            "initial_creds": initial_creds,
        },
    )
    assert not ok
    assert "initial_creds" in (err or "")


@pytest.mark.parametrize(
    "initial_creds",
    [
        {"username": "admin", "password": "admin123"},
        {"auth_required": False, "note": "公开入口无需登录"},
        {"note": "需要用户自行注册后登录"},
    ],
)
def test_validate_env_ready_accepts_explicit_initial_creds_states(initial_creds):
    ok, err = validate_output(
        "env_ready",
        {
            "target_url": "http://x:8080",
            "compose_path": ".vuln-env/docker-compose.yml",
            "initial_creds": initial_creds,
        },
    )
    assert ok, err


def test_validate_env_ready_no_target():
    ok, err = validate_output("env_ready", {"compose_path": "x"})
    assert not ok
    assert "target_url" in err


def _payload_ok(**over):
    base = {
        "method": "POST",
        "path": "/login",
        "expected_observable": "响应回显管理员密码哈希",
    }
    base.update(over)
    return base


def _pass_ok(**over):
    base = {
        "gate_verdict": "pass",
        "gate_reason": "Q1 有危害。Q2 连通。Q3 无阻断。",
        "kill_chain": "login → sql",
        "defense_layers": [],
        "payloads": [_payload_ok()],
        "runtime_dependent": False,
        "core_claim": "匿名攻击者可经 /login 读出管理员密码哈希",
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    "output,needle",
    [
        ({"kill_chain": "..."}, "gate_verdict"),
        ({"gate_verdict": "fail"}, "gate_reason"),
        (_pass_ok(gate_verdict="maybe"), "pass|fail|uncertain"),
        (_pass_ok(gate_reason="  "), "gate_reason 不能为空"),
        (_pass_ok(kill_chain=""), "kill_chain"),
        (_pass_ok(payloads=[]), "payloads"),
        (_pass_ok(runtime_dependent="yes"), "runtime_dependent"),
        (
            {"gate_verdict": "fail", "gate_reason": "阻断", "kill_chain": "a", "defense_layers": []},
            "defense_layers",
        ),
        (
            {"gate_verdict": "uncertain", "gate_reason": "对不上", "payloads": ["x"]},
            "uncertain 不得带非空 payloads",
        ),
        (_pass_ok(core_claim=""), "pass 需要非空 core_claim"),
        (_pass_ok(payloads=["' OR 1=1"]), "pass 的 payloads 必须是请求模板对象"),
        (_pass_ok(payloads=[_payload_ok(path="login")]), "payload path 必须以 / 开头"),
        (_pass_ok(runtime_dependent=True, unresolved_facts=[]), "pass 需要 unresolved_facts 为非空字符串数组"),
        (_pass_ok(runtime_dependent=True), "pass 需要 unresolved_facts 为非空字符串数组"),
    ],
)
def test_validate_audit_shape_rejects(output, needle):
    ok, err = validate_output("audit", output)
    assert not ok
    assert needle in (err or "")


@pytest.mark.parametrize(
    "output",
    [
        _pass_ok(),
        _pass_ok(runtime_dependent=True, unresolved_facts=["需登录后的 CSRF token"]),
        {
            "gate_verdict": "fail",
            "gate_reason": "结构性阻断",
            "kill_chain": "entry 到 validator 停",
            "defense_layers": [{"name": "validator", "bypass": "不可绕过"}],
            "payloads": [],
        },
        {"gate_verdict": "uncertain", "gate_reason": "描述对不上任何 sink"},
        {"gate_verdict": "uncertain", "gate_reason": "锁不住 harm", "payloads": []},
    ],
)
def test_validate_audit_shape_accepts(output):
    ok, err = validate_output("audit", output)
    assert ok, err


def test_audit_input_schema_has_payload_template():
    spec = NODE_INPUT_SCHEMAS["audit"]
    assert "core_claim" in spec["properties"]
    items = spec["properties"]["payloads"]["items"]
    assert items["type"] == "object"
    assert set(items["required"]) == {"method", "path", "expected_observable"}


def test_audit_input_schema_has_uncertain_and_runtime_dependent():
    spec = NODE_INPUT_SCHEMAS["audit"]
    assert spec["properties"]["gate_verdict"]["enum"] == ["pass", "fail", "uncertain"]
    assert spec["properties"]["runtime_dependent"]["type"] == "boolean"
    assert "gate_reason" in spec["required"]


def test_audit_mock_output_passes_validation():
    """Mock 模式全链路自洽：_mock_output("audit") 必须能过新的三值形状校验。"""
    ok, err = validate_output("audit", _mock_output("audit", {}))
    assert ok, err


REPORT_MD_KEYS = (
    "product_intro", "vulnerability", "impact", "details",
    "reproduction", "poc_commands", "fix_suggestions", "reporting_decision",
)
RECORD_MD_KEYS = (
    "product_intro", "claimed_issue", "whitebox_analysis", "test_record",
    "blocker", "observed_facts", "remaining_conditions", "reporting_decision",
)


def _md_sections(**over):
    base = {k: f"正文-{k}" for k in REPORT_MD_KEYS}
    base["document_kind"] = "vulnerability_report"
    base.update(over)
    return base


def _record_sections(**over):
    base = {k: f"正文-{k}" for k in RECORD_MD_KEYS}
    base["document_kind"] = "verification_record"
    base.update(over)
    return base


def _attempt(**over):
    base = {
        "purpose": "确认核心危害",
        "request": "curl -sS -i http://host.docker.internal:3002/login --data-raw 'q=1'",
        "response_status": 200,
        "response_excerpt": "marker in body",
        "observation": "响应回显 marker",
        "result": "observed_harm",
    }
    base.update(over)
    return base


def _confirmed_ok(**over):
    base = {
        "verdict": "confirmed",
        "reproduced": True,
        "evidence": [{"type": "http_response", "detail": "200 dump"}],
        "attempts": [_attempt()],
        "screenshots": [],
        "cvss": {
            "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
            "base_score": 9.8,
            "severity": "Critical",
        },
        "vulnerable_file": "app/login.py",
    }
    base.update(over)
    return base


def _not_reproduced_ok(**over):
    base = {
        "verdict": "not_reproduced",
        "reproduced": False,
        "evidence": [{"type": "http_response", "detail": "CLI missing"}],
        "attempts": [_attempt(result="blocked", observation="ENOENT", response_status=500)],
        "screenshots": [],
        "vulnerable_file": "app/login.py",
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    "output,needle",
    [
        ({"reproduced": True}, "verdict"),
        (_confirmed_ok(verdict="maybe"), "confirmed|partial|code_reachable"),
        (_confirmed_ok(reproduced=False), "confirmed/partial 需要 reproduced=true"),
        (
            _confirmed_ok(verdict="false_positive", reproduced=True, evidence=[]),
            "false_positive/not_reproduced 需要 reproduced=false",
        ),
        (_confirmed_ok(evidence=[]), "confirmed/partial 需要 evidence 至少 1 条"),
        (
            _confirmed_ok(evidence=[{"type": "http", "detail": ""}]),
            "evidence 条目需要非空 type 和 detail",
        ),
        (_confirmed_ok(screenshots="shot.png"), "screenshots 必须是字符串数组"),
        (_confirmed_ok(cvss={}), "cvss 需要 vector/base_score/severity"),
        (_confirmed_ok(attempts=[]), "attempts"),
        (_confirmed_ok(report_data=_md_sections()), "reproduce 不得交 report_data"),
        (_not_reproduced_ok(cvss={
            "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
            "base_score": 8.9,
            "severity": "Critical",
        }), "未确认判定不得交 cvss"),
        (_confirmed_ok(screenshots=["img/evidence.txt"]), "真实图片"),
    ],
)
def test_validate_reproduce_shape_rejects(output, needle):
    ok, err = validate_output("reproduce", output)
    assert not ok
    assert needle in (err or "")


@pytest.mark.parametrize(
    "output",
    [
        _confirmed_ok(),
        _confirmed_ok(verdict="partial"),
        _not_reproduced_ok(),
        {
            "verdict": "code_smell",
            "reproduced": False,
            "attempts": [_attempt(result="diagnostic")],
            "vulnerable_file": "",
        },
    ],
)
def test_validate_reproduce_shape_accepts(output):
    ok, err = validate_output("reproduce", output)
    assert ok, err


def test_validate_reproduce_allows_empty_evidence_for_not_reproduced():
    """未确认档可交 evidence=[]；空数组不得误判为 confirmed/partial 缺证据。"""
    ok, err = validate_output(
        "reproduce",
        _not_reproduced_ok(evidence=[]),
    )
    assert ok, err


def test_validate_report_vulnerability_and_record_kinds():
    ok, err = validate_output(
        "report",
        {"report_data": {"x": 1}, "final_verdict": "confirmed"},
    )
    assert not ok
    assert "document_kind" in (err or "") or "8 节" in (err or "")

    ok2, err2 = validate_output(
        "report",
        {
            "report_data": _md_sections(),
            "final_verdict": "confirmed",
            "cvss": {
                "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
                "base_score": 9.8,
                "severity": "Critical",
            },
        },
    )
    assert ok2, err2

    ok3, err3 = validate_output(
        "report",
        {"report_data": _md_sections(), "final_verdict": "not_reproduced"},
    )
    assert not ok3
    assert "verification_record" in (err3 or "") or "document_kind" in (err3 or "")

    ok4, err4 = validate_output(
        "report",
        {
            "report_data": _record_sections(),
            "final_verdict": "not_reproduced",
            "cvss": {
                "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
                "base_score": 8.9,
                "severity": "Critical",
            },
        },
    )
    assert not ok4
    assert "不得交 cvss" in (err4 or "")

    ok5, err5 = validate_output(
        "report",
        {"report_data": _record_sections(), "final_verdict": "not_reproduced"},
    )
    assert ok5, err5

    ok6, err6 = validate_output(
        "report",
        {"report_data": _record_sections(poc_commands="curl x"), "final_verdict": "not_reproduced"},
    )
    assert not ok6
    assert "poc_commands" in (err6 or "")


def test_not_reproduced_failed_probes_must_not_become_poc():
    """回归：CLI 缺失等失败探测不得当作漏洞 PoC。"""
    ok, err = validate_output(
        "report",
        {
            "report_data": _md_sections(
                poc_commands="curl ... Claude Code native binary not found",
            ),
            "final_verdict": "not_reproduced",
        },
    )
    assert not ok
    assert err


def test_txt_screenshot_rejected_even_if_file_exists(tmp_path):
    shot = tmp_path / "evidence-auth.txt"
    shot.write_text("not an image", encoding="utf-8")
    ok, err = validate_output(
        "reproduce",
        _confirmed_ok(screenshots=["evidence-auth.txt"]),
        host_workdir=str(tmp_path),
    )
    assert not ok
    assert "真实图片" in (err or "")


def test_mock_reproduce_and_report_pass_validation():
    ok, err = validate_output("reproduce", _mock_output("reproduce", {}))
    assert ok, err
    ok2, err2 = validate_output("report", _mock_output("report", {}))
    assert ok2, err2


def test_validate_profile_requires_is_web():
    ok, err = validate_output("profile", {"language": "python"})
    assert not ok
    assert "is_web" in (err or "")
    ok2, _ = validate_output(
        "profile",
        {"is_web": True, "language": "python", "framework": "fastapi"},
    )
    assert ok2


def test_input_schemas_defined_for_all_ai_nodes():
    """5 个 AI 节点都有 submit_result input schema。"""
    for key in ["profile", "env_ready", "audit", "reproduce", "report"]:
        assert key in NODE_INPUT_SCHEMAS, f"{key} 缺 input schema"
        assert "required" in NODE_INPUT_SCHEMAS[key]


def test_rewrite_localhost_to_host_docker_internal():
    assert rewrite_url_for_agent_container("http://localhost:8080") == "http://host.docker.internal:8080"
    assert rewrite_url_for_agent_container("http://127.0.0.1:5000/login") == "http://host.docker.internal:5000/login"
    assert rewrite_url_for_agent_container("http://example.com") == "http://example.com"
    assert rewrite_url_for_agent_container(None) is None


@pytest.mark.asyncio
async def test_missing_output_includes_failed_event(tmp_path, monkeypatch):
    """容器 exit=1 且没写 .node_output.json 时，错误必须带上 JSONL 失败事件。

    回归：stderr 为空时用户只看到「未产出 .node_output.json」，真正原因在 stdout JSONL。
    """
    from unittest.mock import MagicMock, patch

    from app.contexts.agent import ai_runner
    from app.core.agent_runner import AgentRunnerError

    settings = MagicMock()
    settings.claude_agent_sdk_enabled = True

    def _fake_run(spec, on_event):
        on_event({
            "type": "agent.failed",
            "error": "节点 env_ready 未调用 submit_result(无 .node_output.json)",
            "title": "Agent 没有提交节点结果就结束了",
        })
        return 1, {"stderr_tail": "", "timed_out": False}

    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    with patch("app.contexts.agent.ai_runner.agent_runner_manager") as mgr:
        mgr.run_with_streaming.side_effect = _fake_run
        with pytest.raises(AgentRunnerError) as ei:
            await ai_runner.run_ai_node(
                node_key="env_ready",
                input_json={"attempt": 1},
                host_workdir=str(tmp_path),
                runner_env={},
            )
    msg = str(ei.value)
    assert "未产出 .node_output.json" in msg
    assert "未调用 submit_result" in msg


@pytest.mark.asyncio
async def test_missing_output_includes_stderr_runner_import_error(tmp_path, monkeypatch):
    """python -m runner.run_one 找不到模块时，错误必须带上 stderr。"""
    from unittest.mock import MagicMock, patch

    from app.contexts.agent import ai_runner
    from app.core.agent_runner import AgentRunnerError

    settings = MagicMock()
    settings.claude_agent_sdk_enabled = True
    stderr = (
        "/usr/local/bin/python: Error while finding module specification for "
        "'runner.run_one' (ModuleNotFoundError: No module named 'runner')"
    )

    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    with patch("app.contexts.agent.ai_runner.agent_runner_manager") as mgr:
        mgr.run_with_streaming.return_value = (1, {"stderr_tail": stderr, "timed_out": False})
        with pytest.raises(AgentRunnerError) as ei:
            await ai_runner.run_ai_node(
                node_key="env_ready",
                input_json={"attempt": 1},
                host_workdir=str(tmp_path),
                runner_env={},
            )
    assert "No module named 'runner'" in str(ei.value)
