"""把 Agent/编排异常翻成人类可读的短句 + 可操作下一步。"""
from __future__ import annotations

from app.contexts.agent.llm_errors import classify_llm_api_error

# (子串匹配, 标题, 下一步) — 更具体的规则必须排在前面
_RULES: list[tuple[str, str, str]] = [
    (
        "单节点最长执行",
        "AI 节点超过单节点最长执行时间",
        "可在「设置 → 并发与资源」调大单节点超时（0=不限），或从本节点重试。",
    ),
    (
        "bubblewrap is required",
        "Agent 运行环境缺少进程隔离依赖",
        "请重建 Agent 运行镜像（需包含 bubblewrap）后重试。",
    ),
    (
        "Sandbox dependencies not available",
        "Agent 运行环境缺少沙箱运行依赖",
        "请重建 Agent 运行镜像（需包含 bubblewrap 与 socat）后重试。",
    ),
    (
        "No permissions to create new namespace",
        "Agent 嵌套沙箱被 Docker 拦截",
        "当前安全策略阻止了沙箱命名空间（seccomp）。请更新并重启 API 与 Worker 后重试。",
    ),
    (
        "No module named 'runner'",
        "Agent 运行入口模块找不到",
        "请确认 Agent 运行镜像完整，并重建 crucible-agent-runner:base 后重试。",
    ),
    (
        "SIGKILL",
        "Agent 容器被强制结束",
        "优先检查内存不足、人工取消或运行环境异常；查看本节点事件流确认执行进度。",
    ),
    (
        "LLM 调用失败",
        "LLM 调用失败",
        "核对「设置 → LLM Provider」的 API Key、Base URL、模型名与账户余额。",
    ),
    (
        "error result: success",
        "LLM 会话异常结束",
        "多为上游接口报错（余额不足、模型不可用等）。请查看本节点更早的失败事件，并核对 Provider 配置。",
    ),
    (
        "DSML",
        "模型工具调用格式异常",
        "模型把工具调用泄成了纯文本（常见于 DeepSeek DSML）。平台会自动回喂重试；反复出现请换已通过 Agent 测试的模型。",
    ),
    (
        "未产出 .node_output.json",
        "Agent 没有提交节点结果",
        "本轮分析未完成结构化回传。可从本节点重试；若反复出现，请检查 Provider 是否通过 Agent 测试。",
    ),
    (
        "output 校验失败",
        "Agent 回传 JSON 缺必填字段",
        "缺少必填字段。请从本节点重试；持续失败时核对模型是否支持工具调用。",
    ),
    (
        "output JSON 解析失败",
        "节点结果格式异常",
        "回传内容无法解析。请从本节点重试。",
    ),
    (
        "网络错误",
        "Git 拉取网络失败",
        "检查本机能否访问 Git 远程（DNS、代理、防火墙），稍后重试。",
    ),
    (
        "仓库不存在或无权访问",
        "仓库不存在或无权访问",
        "核对 Git 地址，以及任务凭据是否有 clone 权限。",
    ),
    (
        "分支/tag 不存在",
        "指定的分支或标签不存在",
        "核对任务填写的分支 / 标签是否在远程仓库中。",
    ),
    (
        "源码解包失败",
        "上传源码解开失败",
        "确认源码包为 zip / tar.gz，且任务仍能访问已上传的文件。",
    ),
    (
        "源码工作区准备失败",
        "源码工作目录权限异常",
        "目录可能被靶场进程改过属主。请先停止占用该任务目录的容器，再从「源码获取」节点重试。",
    ),
    (
        "already exists and is not an empty directory",
        "源码工作目录没有清空",
        "工作区残留导致无法重新拉取。请停止相关容器后，从「源码获取」节点重试。",
    ),
    (
        "源码克隆失败",
        "Git 克隆源码失败",
        "核对仓库地址、分支/标签，以及任务凭据是否有访问权限。",
    ),
    (
        "超时",
        "节点曾超时结束",
        "可从本节点重试。若反复超时，请检查模型响应速度与 Provider 超时设置。",
    ),
    (
        "agent-runner 镜像",
        "缺少 agent-runner 镜像",
        "请由管理员构建并加载 crucible-agent-runner:base 镜像后重试。",
    ),
    (
        "未配置默认 LLM Provider",
        "没有可用的 LLM API Key",
        "到「设置」配置并设为默认 LLM Provider。",
    ),
    (
        "未配置 API Key",
        "没有可用的 LLM API Key",
        "到「设置」补全默认 LLM Provider 的 API Key。",
    ),
    (
        "缺少 LLM 凭据",
        "没有可用的 LLM API Key",
        "到「设置」配置并设为默认 LLM Provider。",
    ),
    (
        "靶场搭建 5 轮全失败",
        "靶场 5 轮排障仍未就绪",
        "查看最后一轮启动与健康检查日志；常见原因是端口冲突或依赖安装失败。",
    ),
    (
        "compose up",
        "靶场启动失败",
        "查看启动日志：端口占用、Dockerfile 语法，或配方是否放在仓库 .vuln-env 目录。",
    ),
    (
        "健康检查不过",
        "靶场已启动但探活失败",
        "确认端口映射正确，应用监听 0.0.0.0，且画像中的端口配置无误。",
    ),
    (
        "Authentication",
        "LLM 鉴权失败",
        "检查 API Key 是否有效，Base URL 是否为 Anthropic 兼容端点。",
    ),
    (
        "401",
        "LLM 接口拒绝访问（401）",
        "API Key 无效或未正确注入。请核对「设置 → LLM Provider」。",
    ),
    (
        "claude_agent_sdk 导入失败",
        "Agent 运行环境不完整",
        "请重建 Agent 运行镜像后重试。",
    ),
    (
        "NameError",
        "Agent 运行入口异常",
        "请更新代码并重建 Agent 运行镜像后重试。",
    ),
    (
        "未调用 submit_result",
        "Agent 没有提交节点结果",
        "本轮分析未完成结构化回传。可从本节点重试；若反复出现，请检查 Provider 是否通过 Agent 测试。",
    ),
    (
        "既无 .node.json",
        "容器未收到任务输入",
        "请从本节点重试；持续失败时检查任务工作目录挂载是否正常。",
    ),
    (
        ".node.json 解析失败",
        "节点输入文件损坏",
        "请从本节点重试；持续失败时联系管理员检查任务调度。",
    ),
    (
        "infra_error",
        "平台预检未通过",
        "常见原因是缺少 Agent 运行镜像或未配置 LLM API Key。",
    ),
]


def humanize_agent_error(raw: str | None) -> tuple[str, str]:
    """返回 (人类可读标题, 下一步提示)。匹配不到则标题=原文截断，提示给通用建议。"""
    text = (raw or "").strip() or "未知错误"
    llm = classify_llm_api_error(text)
    if llm is not None:
        return llm
    for needle, title, hint in _RULES:
        if needle.lower() in text.lower():
            return title, hint
    return text[:240], "查看本节点事件流中的错误与工具输出，定位失败步骤后重试。"


NODE_ERROR_LOG_MAX = 32_000
RUN_ERROR_LOG_MAX = 2_000


def clip_error_log(text: str | None, *, limit: int = NODE_ERROR_LOG_MAX) -> str:
    """节点排错日志：保留现场，只在极端长度时截尾。"""
    body = (text or "").strip() or "未知错误"
    if len(body) <= limit:
        return body
    marker = "\n...[truncated]"
    return body[: max(0, limit - len(marker))] + marker


def node_error_log_from_output(output: dict | None) -> str | None:
    """从节点 output 抽出应落库的错误日志（扫描失败隔离也要留）。"""
    if not isinstance(output, dict):
        return None
    err = output.get("error") or output.get("error_log")
    if err not in (None, ""):
        return clip_error_log(str(err))
    if output.get("status") == "failed":
        return clip_error_log("引擎失败")
    return None


def format_agent_error(raw: str | None, *, node_key: str | None = None) -> str:
    """落库 / 前端展示用的多行错误（标题 + 原因原文 + 下一步）。"""
    title, hint = humanize_agent_error(raw)
    prefix = f"节点 {node_key} 失败: " if node_key else ""
    cause = clip_error_log((raw or "").strip() or "未知错误")
    return f"{prefix}{title}\n原因: {cause}\n下一步: {hint}"
