"""把 Agent/编排异常翻成人类可读的短句 + 排错提示。"""
from __future__ import annotations

# (子串匹配, 标题, 下一步) — 更具体的规则必须排在前面
_RULES: list[tuple[str, str, str]] = [
    (
        "No module named 'runner'",
        "agent-runner 入口模块找不到",
        "host_workdir 会盖掉 /workspace。确认镜像把 runner 放到 /app 且 PYTHONPATH=/app，然后在项目根重建: docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .",
    ),
    (
        "SIGKILL",
        "Agent 容器被平台强杀",
        "exit=137 多为超过 run 硬顶（agent_run_hard_timeout_seconds）被巡检拆掉、单容器超时或 OOM。"
        "看该节点事件流最后几条确认执行到哪一步。",
    ),
    (
        "未产出 .node_output.json",
        "Agent 没有提交节点结果就结束了",
        "模型未调用 submit_result。检查该节点 prompt、MCP 工具是否注入成功，或重建 agent-runner 镜像。",
    ),
    (
        "output 校验失败",
        "Agent 回传 JSON 缺必填字段",
        "对照该节点 schema（env_ready 要 target_url+compose_path，audit 要 gate_verdict，report 要 report_data+final_verdict）。",
    ),
    (
        "output JSON 解析失败",
        "节点结果不是合法 JSON",
        "submit_result 写出的内容损坏。看容器 stderr 或重跑该节点。",
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
        "指定的分支或 tag 不存在",
        "核对任务填写的 branch / tag 是否在远程仓库中。",
    ),
    (
        "源码解包失败",
        "上传源码解开失败",
        "核对源码包是否为 zip / tar.gz，以及任务是否仍能找到已上传的缓存。",
    ),
    (
        "源码克隆失败",
        "Git 克隆源码失败",
        "核对仓库地址、分支/tag，以及任务凭据是否有权限。",
    ),
    (
        "超时",
        "节点执行超时被停止",
        "模型卡住或靶场过慢。可加大 AGENT_RUNNER_TIMEOUT_SECONDS，或检查 compose/健康检查。",
    ),
    (
        "agent-runner 镜像",
        "缺少 agent-runner 镜像",
        "在项目根执行: docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .",
    ),
    (
        "未配置默认 LLM Provider",
        "没有可用的 LLM API Key",
        "到「设置」配置并激活默认 LLM Provider。",
    ),
    (
        "未配置 API Key",
        "没有可用的 LLM API Key",
        "到「设置」补全默认 LLM Provider 的 API Key。",
    ),
    (
        "缺少 LLM 凭据",
        "没有可用的 LLM API Key",
        "到「设置」配置并激活默认 LLM Provider。",
    ),
    (
        "靶场搭建 5 轮全失败",
        "靶场 5 轮排障仍未就绪",
        "看最后一轮 compose/健康检查日志；配方可能端口冲突或依赖装不上。",
    ),
    (
        "compose up",
        "靶场 docker compose 启动失败",
        "看错误后的 logs：端口占用、Dockerfile 语法、或 compose 是否写在仓库/.vuln-env/。",
    ),
    (
        "健康检查不过",
        "靶场容器起来了但端口探活失败",
        "确认 compose 端口映射、应用是否监听 0.0.0.0，以及 profile.port 是否正确。",
    ),
    (
        "Authentication",
        "LLM 鉴权失败",
        "检查 API Key 是否有效、Base URL 是否指向 Anthropic 兼容端点。",
    ),
    (
        "401",
        "LLM 接口拒绝访问（401）",
        "API Key 无效或未注入容器。核对设置页 Provider 与 docker env。",
    ),
    (
        "claude_agent_sdk 导入失败",
        "容器内缺少 Claude Agent SDK",
        "agent-runner 镜像不完整，请重新构建镜像。",
    ),
    (
        "NameError",
        "容器入口代码异常",
        "run_one.py 有语法/缺失符号。更新代码后必须重建 agent-runner 镜像。",
    ),
    (
        "未调用 submit_result",
        "Agent 没有提交节点结果就结束了",
        "模型未调用 submit_result。检查该节点 prompt、MCP 工具是否注入成功，或重建 agent-runner 镜像。",
    ),
    (
        "既无 .node.json",
        "容器没拿到任务输入",
        "检查 host_workdir 是否正确 bind mount 到 /workspace。",
    ),
    (
        ".node.json 解析失败",
        "节点输入文件损坏",
        "worker 写入的 .node.json 不是合法 JSON。",
    ),
    (
        "infra_error",
        "平台预检未通过",
        "看原因：通常是缺少 agent-runner 镜像或 LLM API Key。",
    ),
]


def humanize_agent_error(raw: str | None) -> tuple[str, str]:
    """返回 (人类可读标题, 排错提示)。匹配不到则标题=原文截断，提示给通用建议。"""
    text = (raw or "").strip() or "未知错误"
    for needle, title, hint in _RULES:
        if needle.lower() in text.lower():
            return title, hint
    return text[:240], "查看该节点的事件流（错误/工具输出）与容器 stderr，定位具体失败步骤。"


def format_agent_error(raw: str | None, *, node_key: str | None = None) -> str:
    """落库 / 前端展示用的多行错误（标题 + 原因 + 下一步）。"""
    title, hint = humanize_agent_error(raw)
    prefix = f"节点 {node_key} 失败: " if node_key else ""
    cause = (raw or "").strip()[:400] or "未知错误"
    return f"{prefix}{title}\n原因: {cause}\n下一步: {hint}"
