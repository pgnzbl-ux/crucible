"""ai_runner output 校验测试(不起真容器)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.ai_runner import (
    NODE_INPUT_SCHEMAS,
    rewrite_url_for_agent_container,
    validate_output,
)


def test_validate_env_ready_ok():
    ok, err = validate_output(
        "env_ready",
        {"target_url": "http://x:8080", "compose_path": ".vuln-env/docker-compose.yml"},
    )
    assert ok and err is None


def test_validate_env_ready_only_target_required():
    """env_ready: target_url + compose_path 都 required。"""
    ok, err = validate_output("env_ready", {"target_url": "http://x:8080"})
    assert not ok  # 缺 compose_path
    assert "compose_path" in err
    # 补上 compose_path 才 ok
    ok2, _ = validate_output("env_ready", {"target_url": "http://x:8080", "compose_path": "x.yml"})
    assert ok2


def test_validate_env_ready_no_target():
    ok, err = validate_output("env_ready", {"compose_path": "x"})
    assert not ok
    assert "target_url" in err


def test_validate_audit_gate_verdict():
    ok, _ = validate_output("audit", {"gate_verdict": "fail"})
    assert ok


def test_validate_audit_no_gate():
    ok, err = validate_output("audit", {"kill_chain": "..."})
    assert not ok
    assert "gate_verdict" in err


def test_validate_report():
    ok, _ = validate_output("report", {"report_data": {"x": 1}, "final_verdict": "confirmed"})
    assert ok


def test_validate_report_missing():
    ok, err = validate_output("report", {"report_data": {}})
    assert not ok
    assert "final_verdict" in err


def test_input_schemas_defined_for_all_ai_nodes():
    """4 个 AI 节点都有 submit_result input schema。"""
    for key in ["env_ready", "audit", "reproduce", "report"]:
        assert key in NODE_INPUT_SCHEMAS, f"{key} 缺 input schema"
        assert "required" in NODE_INPUT_SCHEMAS[key]


def test_rewrite_localhost_to_host_docker_internal():
    assert rewrite_url_for_agent_container("http://localhost:8080") == "http://host.docker.internal:8080"
    assert rewrite_url_for_agent_container("http://127.0.0.1:5000/login") == "http://host.docker.internal:5000/login"
    assert rewrite_url_for_agent_container("http://example.com") == "http://example.com"
    assert rewrite_url_for_agent_container(None) is None
