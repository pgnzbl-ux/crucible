"""Agent Runner 标准化错误分类 — 仅限容器内可观测的传输层/基础设施/契约错误。

职责边界（纯净网关）：
- runner 只分类「自己直接产生或观测」的错误：容器基础设施（依赖缺失/SIGKILL）、
  LLM Provider 传输层（鉴权/限流/上下文/网关 5xx）、提交契约（未调 submit_result）、
  执行规格非法。
- 业务语义（余额充值指引、DSML 处置、Git/工作区文案、平台超时文案等）归 backend
  （app.contexts.agent.llm_errors 与各节点校验器）：runner 通过 details.raw_payload
  与完整原文透传，backend 二次分类。本模块不再内置任何业务运营文案。

LLM 传输层信号（含「余额不足」/code 1004 等原始特征）保留 —— 这是"Provider API
报错了"的传输事实，gateway 退出码策略依赖它区分 LLM 失败与契约失败；但标题/建议
一律用中性文案，不带运营指引。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    LLM_PROVIDER = "llm_provider"
    CONTRACT = "contract"
    INFRASTRUCTURE = "infrastructure"
    GENERAL = "general"


class AgentErrorCode(str, Enum):
    # LLM Provider 传输层
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    LLM_CONTEXT_EXCEEDED = "LLM_CONTEXT_EXCEEDED"
    LLM_AUTH_FAILED = "LLM_AUTH_FAILED"
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    LLM_GATEWAY_ERROR = "LLM_GATEWAY_ERROR"

    # 执行契约
    CONTRACT_SUBMIT_MISSING = "CONTRACT_SUBMIT_MISSING"
    SPEC_INVALID = "SPEC_INVALID"

    # 容器运行时
    INFRA_SIGKILL = "INFRA_SIGKILL"
    INFRA_DEPENDENCY_MISSING = "INFRA_DEPENDENCY_MISSING"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass
class AgentErrorInfo:
    code: AgentErrorCode | str
    category: ErrorCategory | str
    title: str
    hint: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code.value if isinstance(self.code, AgentErrorCode) else self.code),
            "category": str(self.category.value if isinstance(self.category, ErrorCategory) else self.category),
            "title": self.title,
            "hint": self.hint,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def _extract_nested_json(text: str) -> dict[str, Any] | None:
    """从错误文本中提取内嵌 JSON 对象（仅认带字符串键的片段，避免任意花括号误收）。"""
    for chunk in re.findall(r"\{[^{}]*\}", text):
        if '"' not in chunk:
            continue
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data:
            return data
    return None


def _info(
    code: AgentErrorCode,
    category: ErrorCategory,
    title: str,
    hint: str,
    text: str,
    *,
    retryable: bool,
    details: dict[str, Any],
) -> AgentErrorInfo:
    return AgentErrorInfo(
        code=code,
        category=category,
        title=title,
        hint=hint,
        message=text[:500],
        retryable=retryable,
        details=details,
    )


def classify_error(
    raw: str | Exception | None, extra_details: dict[str, Any] | None = None
) -> AgentErrorInfo:
    """将任意原始错误文本/异常转换为统一结构化的 AgentErrorInfo。"""
    details: dict[str, Any] = dict(extra_details or {})
    if isinstance(raw, Exception):
        text = f"{type(raw).__name__}: {raw}"
        details["exception_type"] = type(raw).__name__
    else:
        text = (raw or "").strip() or "未知错误"

    low = text.lower()
    nested_json = _extract_nested_json(text)
    if nested_json:
        details["raw_payload"] = nested_json

    # 1. 容器基础设施
    if "sigkill" in low or "exit code 137" in low or "exited with code 137" in low:
        return _info(
            AgentErrorCode.INFRA_SIGKILL,
            ErrorCategory.INFRASTRUCTURE,
            "Agent 容器被强制结束",
            "优先检查内存不足(OOM)、外部取消或运行环境异常；查看本节点事件流确认进度。",
            text,
            retryable=False,
            details=details,
        )
    if (
        "bubblewrap is required" in low
        or "sandbox dependencies not available" in low
        or "no permissions to create new namespace" in low
        or "claude_agent_sdk 导入失败" in text
        or "no module named 'runner'" in low
        or "no module named claude_agent_sdk" in low
    ):
        return _info(
            AgentErrorCode.INFRA_DEPENDENCY_MISSING,
            ErrorCategory.INFRASTRUCTURE,
            "Agent 运行环境不完整",
            "请重建 Agent 运行镜像后重试。",
            text,
            retryable=False,
            details=details,
        )

    # 2. LLM Provider 传输层（业务运营语义由 backend 二次分类）
    if any(
        marker in low
        for marker in (
            "context size has been exceeded",
            "context window exceeded",
            "maximum context length",
            "prompt is too long",
        )
    ):
        return _info(
            AgentErrorCode.LLM_CONTEXT_EXCEEDED,
            ErrorCategory.LLM_PROVIDER,
            "LLM 上下文窗口不足",
            "调低 Provider 最大上下文，或减少单轮工具输出后重试。",
            text,
            retryable=False,
            details=details,
        )
    if "http 401" in low or "http 403" in low or (
        "authentication" in low and "fail" in low
    ):
        return _info(
            AgentErrorCode.LLM_AUTH_FAILED,
            ErrorCategory.LLM_PROVIDER,
            "LLM 接口鉴权失败（401/403）",
            "检查 Provider 的 API Key 与 Base URL 配置后重试。",
            text,
            retryable=False,
            details=details,
        )
    if "http 429" in low or "rate limit" in low or "rate_limit" in low:
        return _info(
            AgentErrorCode.LLM_RATE_LIMIT,
            ErrorCategory.LLM_PROVIDER,
            "LLM 调用频次受限（429）",
            "已触发服务商速率限制，稍候自动退避重试。",
            text,
            retryable=True,
            details=details,
        )
    if any(
        marker in low
        for marker in (
            "余额不足",
            '"code":"1004"',
            '"code": "1004"',
            "model_not_found",
            "model not found",
        )
    ):
        return _info(
            AgentErrorCode.LLM_PROVIDER_ERROR,
            ErrorCategory.LLM_PROVIDER,
            "LLM Provider 返回错误",
            "Provider 拒绝了本次调用（详见原始报错），核对 Provider 配置后重试。",
            text,
            retryable=True,
            details=details,
        )
    if "error result: success" in low or "http 500" in low or "http 502" in low or "http 503" in low:
        return _info(
            AgentErrorCode.LLM_GATEWAY_ERROR,
            ErrorCategory.LLM_PROVIDER,
            "LLM 网关上游服务异常",
            "服务商或网关返回临时错误，稍后重试。",
            text,
            retryable=True,
            details=details,
        )

    # 3. 执行契约
    if "未调用 submit_result" in text or "未产出提交结果" in text:
        return _info(
            AgentErrorCode.CONTRACT_SUBMIT_MISSING,
            ErrorCategory.CONTRACT,
            "Agent 没有提交执行结果",
            "本轮未完成结构化回传。可重试；若反复出现，请检查 Provider 是否支持标准工具调用。",
            text,
            retryable=True,
            details=details,
        )
    if "agentspec" in low and ("invalid" in low or "校验失败" in text):
        return _info(
            AgentErrorCode.SPEC_INVALID,
            ErrorCategory.CONTRACT,
            "Agent 执行规格非法",
            "调度下发的执行规格未通过校验，请检查 backend 版本与 runner 协议版本。",
            text,
            retryable=False,
            details=details,
        )

    # 4. 默认回退（原文全量透传，backend 可二次分类）
    return _info(
        AgentErrorCode.UNKNOWN_ERROR,
        ErrorCategory.GENERAL,
        text[:120],
        "查看本节点事件流中的错误与工具输出，定位失败步骤后重试。",
        text,
        retryable=True,
        details=details,
    )


def is_llm_provider_error(text: str) -> bool:
    """错误文本是否属于 LLM Provider 传输层失败（gateway 退出码策略用）。"""
    return classify_error(text).category == ErrorCategory.LLM_PROVIDER
