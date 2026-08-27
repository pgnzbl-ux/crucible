"""
敏感凭据加密 — Fernet 对称加密。

用途：LLM API Key / 任务凭据加密后落库，列表接口仅回显掩码。
写入走 seal_secret；读取走 reveal_secret（兼容存量明文）。

Key 纪律（安全红线 §5.3）：
- 必须使用**独立**的 SETTINGS_ENCRYPT_KEY（标准 Fernet 32 字节 urlsafe base64）
- **禁止**从 AUTH_SECRET 派生或以任何方式与之关联——签名密钥与加密密钥
  职责不同、轮换节奏不同，混用会让轮换 JWT 密钥静默废掉全部凭据密文
- 生产启动即校验存在与格式（config._enforce_production_security）；
  未配置时首个加解密调用也会以本模块异常失败（fail-closed），无任何回退
"""

from __future__ import annotations

import re

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

# Fernet token 形态特征（gAAAA 开头 + urlsafe 字符集）：用于区分「存量明文」
# 与「拿错密钥解不开的密文」——后者绝不能当明文透传给下游（会把密文发出去）
_FERNET_TOKEN_RE = re.compile(r"^gAAAA[A-Za-z0-9_=-]{80,}$")


class CryptoKeyNotConfiguredError(ValueError):
    """未配置独立 SETTINGS_ENCRYPT_KEY（禁止 auth_secret 回退）。"""


class CredentialDecryptError(ValueError):
    """存储值为 Fernet 密文但当前密钥无法解开（密钥缺失轮换/不一致）。"""


def _get_key() -> bytes:
    raw = (get_settings().settings_encrypt_key or "").strip()
    if not raw:
        raise CryptoKeyNotConfiguredError(
            "SETTINGS_ENCRYPT_KEY 未配置：凭据加解密必须有独立密钥"
            "（禁止从 AUTH_SECRET 派生）。生成："
            'python -c "from cryptography.fernet import Fernet;'
            ' print(Fernet.generate_key().decode())"'
        )
    try:
        Fernet(raw.encode("utf-8"))  # 格式校验（合法 32 字节 urlsafe base64）
    except ValueError as exc:
        raise CryptoKeyNotConfiguredError(
            f"SETTINGS_ENCRYPT_KEY 不是合法的 Fernet 密钥"
            f"（需 32 字节 urlsafe base64）: {exc}"
        ) from exc
    return raw.encode("utf-8")


_fernet_cache: dict[bytes, Fernet] = {}


def _get_fernet() -> Fernet:
    key = _get_key()
    cached = _fernet_cache.get(key)
    if cached is None:
        cached = Fernet(key)
        _fernet_cache[key] = cached
    return cached


def encrypt_secret(plaintext: str) -> str:
    """加密敏感值，返回字符串"""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """解密敏感值；密钥不匹配时返回空串（不把密文当明文）。"""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def seal_secret(plaintext: str) -> str:
    """写入落库：明文 → Fernet 密文。空串保持空串。"""
    return encrypt_secret(plaintext)


def reveal_secret(stored: str) -> str:
    """读出：Fernet 则解密；非密文形态视为升级前明文原样返回。

    形如 Fernet token 却解不开 → 说明密钥错配/轮换丢失，
    显式抛错而不是把密文当明文发给下游供应商。
    """
    if not stored:
        return ""
    try:
        return _get_fernet().decrypt(stored.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        if _FERNET_TOKEN_RE.match(stored):
            raise CredentialDecryptError(
                "凭据为密文但当前 SETTINGS_ENCRYPT_KEY 无法解开"
                "（密钥轮换丢失或环境不一致），拒绝透传密文"
            ) from None
        return stored


def mask_secret(plaintext: str, visible: int = 4) -> str:
    """掩码展示：sk-abc12345 → sk-***2345"""
    if not plaintext:
        return ""
    if len(plaintext) <= visible + 3:
        return "*" * len(plaintext)
    return f"{plaintext[:3]}***{plaintext[-visible:]}"
