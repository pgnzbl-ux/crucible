"""测试 runner 错误分类体系（仅传输层/基础设施/契约；业务语义归 backend llm_errors）。"""
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(os.path.dirname(root), "backend", "agent-runner"))

from runner.errors import (  # noqa: E402
    AgentErrorCode,
    AgentErrorInfo,
    ErrorCategory,
    classify_error,
    is_llm_provider_error,
)


def test_classify_llm_transport_errors():
    err_401 = classify_error("HTTP 401: Unauthorized access key")
    assert err_401.code == AgentErrorCode.LLM_AUTH_FAILED
    assert err_401.category == ErrorCategory.LLM_PROVIDER
    assert err_401.retryable is False

    # 余额类：runner 只认「Provider 传输失败」这一传输事实（退出码策略用），
    # 充值指引等业务文案由 backend llm_errors 依据原始报错二次分类。
    err_balance = classify_error('{"code": "1004", "message": "账户余额不足"}')
    assert err_balance.code == AgentErrorCode.LLM_PROVIDER_ERROR
    assert err_balance.category == ErrorCategory.LLM_PROVIDER
    assert err_balance.retryable is True
    assert err_balance.details.get("raw_payload", {}).get("code") == "1004"


def test_classify_llm_rate_limit_and_context_exceeded():
    err_429 = classify_error("API Error: 429 rate_limit exceeded")
    assert err_429.code == AgentErrorCode.LLM_RATE_LIMIT
    assert err_429.retryable is True

    err_ctx = classify_error("Prompt is too long: maximum context length exceeded")
    assert err_ctx.code == AgentErrorCode.LLM_CONTEXT_EXCEEDED
    assert err_ctx.retryable is False


def test_classify_gateway_5xx_and_sdk_misreport():
    err_5xx = classify_error("HTTP 502 Bad Gateway")
    assert err_5xx.code == AgentErrorCode.LLM_GATEWAY_ERROR
    assert err_5xx.retryable is True

    err_misreport = classify_error("error result: success")
    assert err_misreport.category == ErrorCategory.LLM_PROVIDER


def test_classify_contract_submit_missing():
    err_no_submit = classify_error("任务 audit 未调用 submit_result（无提交产物）")
    assert err_no_submit.code == AgentErrorCode.CONTRACT_SUBMIT_MISSING
    assert err_no_submit.category == ErrorCategory.CONTRACT
    assert err_no_submit.retryable is True


def test_classify_infrastructure_errors():
    err_sigkill = classify_error("process exited with SIGKILL (code 137)")
    assert err_sigkill.code == AgentErrorCode.INFRA_SIGKILL
    assert err_sigkill.category == ErrorCategory.INFRASTRUCTURE
    assert err_sigkill.retryable is False

    for raw in (
        "bubblewrap is required",
        "Sandbox dependencies not available",
        "claude_agent_sdk 导入失败: boom",
        "No module named 'runner'",
    ):
        info = classify_error(raw)
        assert info.code == AgentErrorCode.INFRA_DEPENDENCY_MISSING, raw

    # 平台超时/Git 等业务文案不再由 runner 分类（worker 文本到不了容器内）
    fallback = classify_error("AI 节点超过单节点最长执行时间 3600 秒")
    assert fallback.code == AgentErrorCode.UNKNOWN_ERROR
    assert fallback.category == ErrorCategory.GENERAL


def test_error_info_json_serialization():
    info = AgentErrorInfo(
        code=AgentErrorCode.LLM_AUTH_FAILED,
        category=ErrorCategory.LLM_PROVIDER,
        title="鉴权失败",
        hint="检查 Key",
        message="HTTP 401",
        retryable=False,
        details={"status": 401},
    )
    d = info.to_dict()
    assert d["code"] == "LLM_AUTH_FAILED"
    assert d["category"] == "llm_provider"
    assert d["details"]["status"] == 401


def test_is_llm_provider_error_drives_exit_policy():
    assert is_llm_provider_error("HTTP 401 Unauthorized") is True
    assert is_llm_provider_error("boom crashed") is False
