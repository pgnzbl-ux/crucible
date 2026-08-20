"""从 Agent 容器 JSONL / 异常文本中识别 LLM 网关/API 错误（优先于 no_submit 误报）。"""
from __future__ import annotations

import json
import re

# 明确来自 LLM HTTP 响应或 SDK 流式错误的特征（避免误伤 Git clone / 平台预检文案）
_LLM_SIGNAL_RE = re.compile(
    r"(http\s+[45]\d{2}\b|"
    r"余额不足|"
    r'"code"\s*:\s*"1004"|'
    r"model_not_found|"
    r"model not found|"
    r"rate\s*limit|"
    r"error result:\s*success)",
    re.IGNORECASE,
)


def _extract_json_message(text: str) -> str | None:
    """从 HTTP 401: {...} 或裸 JSON 里抽出 message 字段。"""
    for chunk in re.findall(r"\{[^{}]*\}", text):
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            inner = data.get("error")
            if isinstance(inner, dict):
                msg = inner.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
            msg = data.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    return None


def _is_platform_preflight(text: str) -> bool:
    """平台侧预检/配置文案，不是容器内 LLM 网关响应。"""
    low = text.lower()
    if "源码克隆" in text or "git clone" in low:
        return True
    if "未配置默认 llm provider" in low or "缺少 llm 凭据" in low:
        return True
    if "请到「设置」" in text and "provider" in low:
        return True
    return False


def is_llm_api_failure(text: str | None) -> bool:
    if not text or not str(text).strip():
        return False
    raw = str(text).strip()
    if _is_platform_preflight(raw):
        return False
    return _LLM_SIGNAL_RE.search(raw) is not None


def classify_llm_api_error(text: str | None) -> tuple[str, str] | None:
    """返回 (标题, 下一步)；非 LLM API 错误则 None。"""
    raw = (text or "").strip()
    if not raw or _is_platform_preflight(raw):
        return None
    if not is_llm_api_failure(raw):
        return None

    low = raw.lower()
    json_msg = _extract_json_message(raw)

    if "余额不足" in raw or (json_msg and "余额" in json_msg) or '"code":"1004"' in raw or '"code": "1004"' in raw:
        detail = json_msg or "余额不足"
        return (
            "LLM 账户余额不足",
            f"LLM 服务商返回：{detail}。请到控制台充值或更换有余额的 API Key，再在「设置 → LLM Provider」更新后重试。",
        )

    if "rate limit" in low or "http 429" in low:
        return (
            "LLM 接口限流或配额用尽",
            "稍后重试，或更换 Provider / 提升配额。",
        )

    if "model_not_found" in low or "model not found" in low:
        return (
            "LLM 模型不存在或无权使用",
            "核对「设置 → LLM Provider」中的模型名是否与服务商文档一致。",
        )

    if "http 401" in low:
        detail = json_msg or "鉴权失败"
        return (
            "LLM 接口鉴权失败（401）",
            f"LLM 服务商返回：{detail}。检查 API Key、Base URL 与账户状态。",
        )

    if "http 403" in low:
        detail = json_msg or "拒绝访问"
        return (
            "LLM 接口拒绝访问（403）",
            f"LLM 服务商返回：{detail}。检查 Key 权限与账户状态。",
        )

    if re.search(r"http\s+5\d{2}\b", low):
        return (
            "LLM 服务商暂时不可用",
            "稍后重试；若持续失败，检查 Base URL 与服务商状态页。",
        )

    if "error result: success" in low:
        return (
            "LLM 会话异常结束",
            "多为 LLM API 报错（如余额不足、模型不存在），但被 Claude Agent SDK 误报。"
            "查看本节点事件流中较早的 agent.failed / stream_error 原文。",
        )

    detail = json_msg or raw[:200]
    return (
        "LLM 调用失败",
        f"LLM 服务商返回：{detail}。核对「设置 → LLM Provider」后重试。",
    )
