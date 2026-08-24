"""Agent 错误文案：子串匹配 → 人类可读标题。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.contexts.agent.errors import humanize_agent_error


@pytest.mark.parametrize(
    "raw,expect_title_part",
    [
        ("AI 节点 env_ready 未产出 .node_output.json (exit=1)", "没有提交节点结果"),
        (
            "AI 节点 env_ready 未产出 .node_output.json (exit=1): "
            "/usr/local/bin/python: Error while finding module specification for "
            "'runner.run_one' (ModuleNotFoundError: No module named 'runner')",
            "入口模块找不到",
        ),
        ("ModuleNotFoundError: No module named 'runner'", "入口模块找不到"),
        ("AI 节点 audit output 校验失败: 缺必需字段: gate_verdict", "缺必填字段"),
        ("源码克隆失败: Authentication failed", "克隆源码失败"),
        ("源码克隆失败: 网络错误（无法解析主机）: github.com", "Git 拉取网络失败"),
        ("源码解包失败: 未找到已上传的源码包", "上传源码解开失败"),
        ("源码克隆失败: 仓库不存在或无权访问: 404", "仓库不存在或无权访问"),
        ("源码工作区准备失败: 无法清理 /tmp/audit/repo", "源码工作目录权限异常"),
        (
            "fatal: destination path '/tmp/audit/repo' already exists and is not an empty directory",
            "源码工作目录没有清空",
        ),
        ("agent-runner 镜像不存在: crucible-agent-runner:base", "缺少 agent-runner 镜像"),
        ("agent-runner 镜像不存在或 Docker 不可用: crucible-agent-runner:base", "缺少 agent-runner 镜像"),
        ("缺少 LLM 凭据：未配置默认 Provider", "没有可用的 LLM"),
        ("未配置默认 LLM Provider，请到「设置」配置并激活后再创建或重试任务", "没有可用的 LLM"),
        ("默认 LLM Provider 未配置 API Key，请到「设置」补全后再创建或重试任务", "没有可用的 LLM"),
        ("靶场搭建 5 轮全失败: attempt 5 compose up 失败", "5 轮排障"),
        ("AI 节点 reproduce 超时(1800s)", "超时"),
        ("节点 audit 未调用 submit_result(无 .node_output.json)", "没有提交节点结果"),
        (
            "AI 节点 canary 未产出 .node_output.json (exit=1): "
            "bubblewrap is required for subprocess env scrubbing and isolation",
            "缺少进程隔离依赖",
        ),
        (
            "AI 节点 canary 未产出 .node_output.json (exit=1): "
            "Sandbox dependencies not available: socat not installed",
            "缺少沙箱运行依赖",
        ),
        (
            "AI 节点 canary 未产出 .node_output.json (exit=1): "
            "bwrap: No permissions to create new namespace",
            "嵌套沙箱被 Docker 拦截",
        ),
    ],
)
def test_humanize_known_errors(raw, expect_title_part):
    title, hint = humanize_agent_error(raw)
    assert expect_title_part in title
    assert len(hint) > 10


def test_humanize_unknown_keeps_text():
    title, hint = humanize_agent_error("weird boom xyz")
    assert "weird boom xyz" in title
    assert "事件流" in hint


def test_format_agent_error_includes_next_step():
    from app.contexts.agent.errors import format_agent_error

    text = format_agent_error("AI 节点 env_ready 未产出 .node_output.json", node_key="env_ready")
    assert "env_ready" in text
    assert "下一步:" in text
    assert "原因:" in text
    long_stderr = "FATAL " + ("x" * 800)
    full = format_agent_error(long_stderr, node_key="audit")
    assert "x" * 800 in full
    assert "FATAL" in full


def test_clip_error_log_keeps_debug_body():
    from app.contexts.agent.errors import NODE_ERROR_LOG_MAX, clip_error_log, node_error_log_from_output

    body = "stderr\n" + ("a" * 2000)
    assert clip_error_log(body) == body
    huge = "h" * (NODE_ERROR_LOG_MAX + 50)
    clipped = clip_error_log(huge)
    assert len(clipped) <= NODE_ERROR_LOG_MAX
    assert clipped.endswith("[truncated]")
    assert node_error_log_from_output({"status": "failed", "error": body}) == body
    assert node_error_log_from_output({"status": "completed"}) is None


def test_runner_module_missing_not_misclassified_as_submit_result():
    """容器没起来时不能说成模型没调 submit_result。"""
    raw = (
        "AI 节点 env_ready 未产出 .node_output.json (exit=1): "
        "ModuleNotFoundError: No module named 'runner'"
    )
    title, hint = humanize_agent_error(raw)
    assert "submit_result" not in title
    assert "入口模块" in title
    assert "重建" in hint
    assert "agent-runner" in hint.lower() or "Dockerfile" in hint


def test_runner_bubblewrap_missing_not_misclassified_as_submit_result():
    raw = (
        "AI 节点 canary 未产出 .node_output.json (exit=1): "
        "bubblewrap is required for subprocess env scrubbing and isolation"
    )
    title, hint = humanize_agent_error(raw)
    assert "submit_result" not in title
    assert "进程隔离依赖" in title
    assert "bubblewrap" in hint


def test_runner_socat_missing_not_misclassified_as_submit_result():
    raw = (
        "AI 节点 canary 未产出 .node_output.json (exit=1): "
        "Sandbox dependencies not available: socat not installed"
    )
    title, hint = humanize_agent_error(raw)
    assert "submit_result" not in title
    assert "沙箱运行依赖" in title
    assert "socat" in hint


def test_runner_namespace_block_not_misclassified_as_submit_result():
    raw = (
        "AI 节点 canary 未产出 .node_output.json (exit=1): "
        "bwrap: No permissions to create new namespace"
    )
    title, hint = humanize_agent_error(raw)
    assert "submit_result" not in title
    assert "嵌套沙箱" in title
    assert "seccomp" in hint
