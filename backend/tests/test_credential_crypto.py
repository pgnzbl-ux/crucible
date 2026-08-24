"""凭据存储一致性测试(明文存储)。

验证 Provider/Credential 的 api_key 明文落库、build_env_from_provider
直接返回 DB 存的值(不加密不调解)。响应层 mask_secret 掩码。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_build_env_returns_db_value_directly():
    """build_env_from_provider 直接返回 DB 存的明文 key,不调解。"""
    from app.contexts.settings.models import (
        DEFAULT_LLM_EFFORT,
        DEFAULT_LLM_MAX_CONTEXT_TOKENS,
        LlmProvider,
    )
    from app.contexts.settings.service import SettingsService

    real_key = "sk-test-1234567890abcdef"
    provider = LlmProvider(
        id="p1",
        name="test",
        provider_type="deepseek",
        base_url="https://api.deepseek.com/anthropic",
        api_key_encrypted=real_key,  # 明文存
        model="deepseek-v4-flash",
        timeout_ms=600000,
        is_default=True,
    )

    svc = SettingsService.__new__(SettingsService)
    env = svc.build_env_from_provider(provider)

    # 核心断言:注入容器的 key 就是 DB 存的明文原值
    assert env["ANTHROPIC_API_KEY"] == real_key
    assert env["ANTHROPIC_AUTH_TOKEN"] == real_key
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == str(DEFAULT_LLM_MAX_CONTEXT_TOKENS)
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == DEFAULT_LLM_EFFORT
    assert env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] == "1"


def test_to_response_masks_key():
    """mask_secret 返回掩码,不含完整 key。"""
    from app.core.crypto import mask_secret

    real_key = "sk-1234567890abcdef"
    masked = mask_secret(real_key)
    assert real_key not in masked
    assert masked.endswith("cdef")  # 末尾 4 位可见
    assert masked.startswith("sk-")  # 前缀可见
