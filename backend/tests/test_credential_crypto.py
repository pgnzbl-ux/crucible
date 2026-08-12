"""凭据加密链路一致性测试。

验证 Provider/Credential 的 api_key 落库前 Fernet 加密、
读取(build_env / test_connection / to_response)时解密。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_encrypt_decrypt_roundtrip():
    """加密→解密往返一致。"""
    from app.core.crypto import encrypt_secret, decrypt_secret

    for plaintext in ["sk-abc", "short", "a" * 100, "中文密钥测试"]:
        enc = encrypt_secret(plaintext)
        assert enc.startswith("gAAAAA"), f"应为 Fernet 密文: {enc[:10]}"
        assert enc != plaintext, "加密后应与明文不同"
        assert decrypt_secret(enc) == plaintext


def test_decrypt_invalid_returns_empty():
    """解密失败(key 不匹配)返回空串,不抛异常。"""
    from app.core.crypto import decrypt_secret

    assert decrypt_secret("gAAAAAinvalid") == ""
    assert decrypt_secret("") == ""


def test_build_env_decrypts_provider_key():
    """build_env_from_provider 必须返回解密后的明文 key,不是 DB 密文。

    回归 bug:working copy 把 decrypt_secret 去掉,导致容器拿到 Fernet
    密文当 API key → 401。还原后 build_env 必须解密。
    """
    from app.core.crypto import encrypt_secret
    from app.contexts.settings.models import LlmProvider
    from app.contexts.settings.service import SettingsService

    real_key = "sk-test-1234567890abcdef"
    encrypted = encrypt_secret(real_key)
    assert encrypted.startswith("gAAAAA")

    provider = LlmProvider(
        id="p1",
        name="test",
        provider_type="deepseek",
        base_url="https://api.deepseek.com/anthropic",
        api_key_encrypted=encrypted,
        model="deepseek-v4-flash",
        timeout_ms=600000,
        enabled=True,
        is_default=True,
    )

    # build_env_from_provider 是同步方法,不依赖 repo
    svc = SettingsService.__new__(SettingsService)
    env = svc.build_env_from_provider(provider)

    # 核心断言:注入容器的 key 是明文,不是密文
    assert env["ANTHROPIC_API_KEY"] == real_key, "build_env 应解密,注入明文 key"
    assert env["ANTHROPIC_AUTH_TOKEN"] == real_key
    assert env["ANTHROPIC_API_KEY"] != encrypted, "绝不能注入 Fernet 密文"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
